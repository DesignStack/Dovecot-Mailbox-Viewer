"""Dovecot Mailbox Viewer desktop interface."""

from pathlib import Path
from email.utils import parsedate_to_datetime, parseaddr
import sys
import logging
from logging.handlers import RotatingFileHandler
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot, Qt, QUrl, QSize
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QSplitter, QInputDialog,
    QScrollArea, QStackedWidget, QToolButton, QVBoxLayout, QWidget, QProgressDialog,
)
from PySide6.QtPrintSupport import QPrintDialog

from viewer.catalog import Catalogue, cache_path, describe, source_fingerprint
from viewer.html_preview import SafeHtmlPreview, MAX_IMAGES
from viewer.icons import line_icon
from viewer.mail_widgets import FolderDelegate, MessageDelegate, DETAILS_ROLE
from viewer.attachments import AttachmentList, collect_attachments, suggested_filename
from viewer.welcome import WelcomeDialog, WelcomePage
from viewer.mdbox import discover, read_account
from viewer.dovecot_index import read_statuses, SEEN, DELETED
from viewer.version import __version__
from viewer.recent import RecentBackups, dropped_backup
from viewer.exporting import ExportWorker, safe_name
from viewer.printing import MailPrintDocument, make_printer, save_pdf, print_document
from viewer.updates import UpdateDialog


def log_path() -> Path:
    """Keep diagnostic logs separate from the source mailbox backup."""
    return cache_path(Path("diagnostics"), "app").parent / "viewer.log"


def configure_logging():
    target = log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(target, maxBytes=2_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger("viewer").addHandler(handler)
    logging.getLogger("viewer").setLevel(logging.INFO)
    logging.getLogger("viewer").info("Dovecot Mailbox Viewer %s starting (%s)",
                                     __version__, "portable" if getattr(sys, "frozen", False) else "source")
    def log_uncaught(exc_type, exc_value, exc_traceback):
        logging.getLogger("viewer").error("Unhandled application error", exc_info=(exc_type, exc_value, exc_traceback))
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
    sys.excepthook = log_uncaught


def app_icon():
    return line_icon("mail", "#357ddb")


class SearchDialog(QDialog):
    """Search options stay separate from the compact toolbar."""

    def __init__(self, current: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search mail")
        self.setMinimumWidth(400)
        layout = QFormLayout(self)
        self.words = QLineEdit(current.get("query", ""))
        self.sender = QLineEdit(current.get("sender", ""))
        self.subject = QLineEdit(current.get("subject", ""))
        self.attachment = QCheckBox("Only emails with attachments")
        self.attachment.setChecked(current.get("attachments", False))
        self.after = QLineEdit(current.get("after", ""))
        self.before = QLineEdit(current.get("before", ""))
        self.after.setPlaceholderText("YYYY-MM-DD")
        self.before.setPlaceholderText("YYYY-MM-DD")
        layout.addRow("Any words", self.words)
        layout.addRow("From", self.sender)
        layout.addRow("Subject", self.subject)
        layout.addRow("From date", self.after)
        layout.addRow("To date", self.before)
        layout.addRow("", self.attachment)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def options(self):
        return dict(query=self.words.text().strip(), sender=self.sender.text().strip(),
                    subject=self.subject.text().strip(), after=self.after.text().strip(),
                    before=self.before.text().strip(), attachments=self.attachment.isChecked())

    def accept(self):
        from datetime import date
        for field in (self.after, self.before):
            if field.text().strip():
                try:
                    date.fromisoformat(field.text().strip())
                except ValueError:
                    QMessageBox.warning(self, "Invalid date", "Enter dates as YYYY-MM-DD.")
                    field.setFocus()
                    return
        super().accept()


class ImportWorker(QObject):
    progress = Signal(int, int)  # percentage, message count
    batch = Signal(int)
    completed = Signal(int)
    failed = Signal(str)

    def __init__(self, source: Path, account: str, info: dict, database: Path, fingerprint: str):
        super().__init__()
        self.source, self.account, self.info, self.database, self.fingerprint = source, account, info, database, fingerprint

    def run(self):
        catalogue = None
        try:
            logging.getLogger("viewer").info("Import started: %s (%s)", self.source, self.account)
            catalogue = Catalogue(self.database)
            catalogue.reset()
            for folder in sorted(self.info["folders"]):
                catalogue.add_folder(folder)
            statuses = read_statuses(self.source, self.info)
            count = 0
            last_commit = time.monotonic()
            last_percent, last_update = -1, 0.0
            for record in read_account(self.source, self.info):
                match = statuses.get(record.guid) if record.guid else None
                catalogue.add(record, match[1] if match else None)
                count += 1
                percent = min(99, int(100 * record.bytes_done / max(1, record.bytes_total)))
                now = time.monotonic()
                if percent != last_percent or now - last_update >= 0.5:
                    self.progress.emit(percent, count)
                    last_percent, last_update = percent, now
                if count == 1 or count % 25 == 0 or now - last_commit >= 0.5:
                    catalogue.commit()
                    self.batch.emit(count)
                    last_commit = now
            catalogue.finish(self.fingerprint)
            self.batch.emit(count)
            logging.getLogger("viewer").info("Import complete: %s messages (%s)", count, self.account)
            self.completed.emit(count)
        except Exception as exc:
            logging.getLogger("viewer").exception("Import failed for %s", self.account)
            self.failed.emit(str(exc))
        finally:
            if catalogue is not None:
                catalogue.close()


class Window(QMainWindow):
    def __init__(self, *, show_welcome=True, settings=None):
        super().__init__()
        self.setWindowTitle("Dovecot Mailbox Viewer")
        self.setWindowIcon(app_icon())
        self.resize(1420, 880)
        self.setMinimumSize(920, 580)
        self.catalogue = None
        self.source = None
        self.account_name = ""
        self.worker_thread = None
        self.worker = None
        self.attachments = []
        self.search_options = {}
        self.shown_message = None
        self.welcome_dialog = None
        self._welcome_enabled = show_welcome
        self._startup_welcome_scheduled = False
        self._import_error_visible = False
        self.recent_backups = RecentBackups(settings)
        self.export_worker = None
        self.export_progress = None
        self.update_dialog = None
        self.setAcceptDrops(True)

        # Avoid a blanket QWidget background: it paints white rectangles behind
        # labels in coloured containers, including the remote-image notice.
        self.setStyleSheet("""
            QMainWindow { background: #f4f5f7; }
            QWidget { color: #303742; font-family: 'Segoe UI'; font-size: 13px; }
            QLabel { background: transparent; }
            QMenuBar { background: #f5f6f8; padding: 2px 8px; }
            QMenu { background: white; border: 1px solid #dde1e7; padding: 6px; }
            QMenu::item { padding: 8px 26px 8px 8px; border-radius: 4px; }
            QMenu::item:selected { background: #eaf1fb; color: #234f87; }
            QMenu::item:disabled { color: #a0a7b1; }
            QLineEdit, QComboBox { background: #ffffff; border: 1px solid #dce1e7;
                border-radius: 6px; padding: 8px 10px; selection-background-color: #357ddb; }
            QLineEdit:focus { border-color: #7ba7df; }
            QListWidget { border: 0; border-radius: 0; outline: none; padding: 0; background: white; }
            QListWidget#folders { background: #f7f8fa; }
            QToolButton { border: 1px solid transparent; background: transparent;
                border-radius: 6px; padding: 7px; }
            QToolButton:hover, QToolButton:pressed { background: #e8ecf2; }
            QToolButton:focus { border-color: #8db4e5; }
            QToolButton::menu-indicator { image: none; width: 0; }
            QPushButton { border: 1px solid #dce1e7; background: white; border-radius: 5px; padding: 7px 14px; }
            QPushButton:hover { background: #eaf1fb; }
            QFrame#toolbar { background: #f5f6f8; border-bottom: 1px solid #dfe3e9; }
            QWidget#sidebar { background: #f7f8fa; }
            QWidget#messageColumn { background: #ffffff; }
            QFrame#messageHeader { background: #ffffff; border-bottom: 1px solid #edf0f3; }
            QLabel#mailboxName { font-weight: 600; padding: 16px 18px; color: #424b58; }
            QLabel#folderTitle { font-size: 15px; font-weight: 600; }
            QLabel#messageCount, QLabel#details { color: #78818f; }
            QWidget#readingPane { background: #f1f3f6; }
            QFrame#messageCard { background: white; border: 1px solid #e0e4ea; border-radius: 9px; }
            QLabel#subject { font-size: 19px; font-weight: 600; color: #28313d; }
            QLabel#sender { font-size: 14px; font-weight: 600; }
            QLabel#avatar { background: #eaf0f8; color: #5d7b9e; border-radius: 21px; font-size: 16px; }
            QFrame#imageNotice { background: #fff5d8; border: 1px solid #efdeb1; border-radius: 5px; }
            QFrame#imageNotice QLabel { background: transparent; color: #756135; }
            QTextBrowser { background: #ffffff; border: 0; padding: 0; }
            QSplitter::handle { background: #dfe3e9; }
            QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
            QScrollBar::handle:vertical { background: #ccd2db; border-radius: 4px; min-height: 24px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
            QStatusBar { background: #f5f6f8; color: #7d8591; border-top: 1px solid #e1e5eb; }
            QStatusBar::item { border: 0; }
            QProgressBar { border: 0; background: #e4e9f1; border-radius: 3px; height: 6px; }
            QProgressBar::chunk { background: #357ddb; border-radius: 3px; }
        """)

        self.attachment_action = QAction(line_icon("attachment"), "Save attachment…", self)
        self.attachment_action.triggered.connect(self.save_attachment)
        self.export_action = QAction(line_icon("export"), "Export email as .eml…", self)
        self.export_action.triggered.connect(self.export_eml)
        self.images_action = QAction(line_icon("image"), "Download images", self)
        self.images_action.triggered.connect(self.download_images)
        self.bulk_export_action = QAction(line_icon("export"), "Export selected emails…", self)
        self.bulk_export_action.triggered.connect(self.export_selected)
        self.folder_export_action = QAction(line_icon("folder"), "Export entire folder…", self)
        self.folder_export_action.triggered.connect(self.export_folder)
        self.pdf_action = QAction(line_icon("file_pdf"), "Save email as PDF…", self)
        self.pdf_action.triggered.connect(self.export_pdf)
        self.print_action = QAction(line_icon("print"), "Print email…", self)
        self.print_action.setShortcut("Ctrl+P")
        self.print_action.triggered.connect(self.print_email)
        for action in (self.attachment_action, self.export_action, self.images_action,
                       self.bulk_export_action, self.folder_export_action, self.pdf_action, self.print_action):
            action.setEnabled(False)
        open_archive = QAction(line_icon("archive"), "Open archive…", self)
        open_archive.setShortcut("Ctrl+O")
        open_archive.triggered.connect(self.open_archive)
        open_folder = QAction(line_icon("folder"), "Open folder…", self)
        open_folder.triggered.connect(self.open_folder)
        clear_cache = QAction(line_icon("refresh"), "Clear current cache", self)
        clear_cache.triggered.connect(self.clear_cache)
        open_log = QAction(line_icon("log"), "Open diagnostic log", self)
        open_log.triggered.connect(self.open_log)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addActions([open_archive, open_folder])
        self.recent_menu = file_menu.addMenu("Recent backups")
        file_menu.addSeparator()
        file_menu.addActions([self.export_action, self.bulk_export_action, self.folder_export_action])
        file_menu.addSeparator()
        file_menu.addActions([self.pdf_action, self.print_action])
        file_menu.addSeparator()
        file_menu.addActions([clear_cache, open_log])
        file_menu.addSeparator()
        self.exit_action = QAction(line_icon("exit"), "Exit", self)
        self.exit_action.setShortcut("Ctrl+Q")
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)
        help_menu = self.menuBar().addMenu("Help")
        self.getting_started_action = QAction(line_icon("mail"), "Getting started…", self)
        self.getting_started_action.triggered.connect(self.show_welcome)
        help_menu.addAction(self.getting_started_action)
        help_menu.addSeparator()
        self.updates_action = QAction(line_icon("refresh"), "Check for updates…", self)
        self.updates_action.triggered.connect(self.check_updates)
        help_menu.addAction(self.updates_action)
        self.contact_action = QAction(line_icon("mail"), "Contact author", self)
        self.contact_action.triggered.connect(self.contact_author)
        self.about_action = QAction(line_icon("info"), "About", self)
        self.about_action.triggered.connect(self.show_about)
        help_menu.addActions([self.contact_action, self.about_action])
        search_action = QAction("Search mail…", self)
        search_action.setShortcut("Ctrl+F")
        search_action.triggered.connect(self.show_search)
        self.addAction(search_action)

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("toolbar")
        toolbar = QHBoxLayout(toolbar_frame)
        toolbar.setContentsMargins(14, 8, 12, 8)
        toolbar.setSpacing(5)
        self.source_label = QLabel("Open a mailbox backup to get started")
        self.source_label.setStyleSheet("color: #929aa5; font-size: 12px")
        toolbar.addWidget(self.source_label, 1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search mail…")
        self.search.setAccessibleName("Search mail")
        self.search.setFixedWidth(300)
        self.search.textChanged.connect(self.refresh_messages)
        self.search.returnPressed.connect(self.show_search)
        toolbar.addWidget(self.search)
        toolbar.addWidget(self._tool("Search options (Ctrl+F)", "search", self.show_search))
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.VLine)
        separator.setFixedHeight(22)
        separator.setStyleSheet("color: #dce1e8")
        toolbar.addWidget(separator)
        toolbar.addWidget(self._tool("Open archive (Ctrl+O)", "archive", self.open_archive))
        toolbar.addWidget(self._tool("Open folder", "folder", self.open_folder))
        outer.addWidget(toolbar_frame)

        self.panes = QSplitter(Qt.Orientation.Horizontal)
        self.panes.setHandleWidth(1)
        self.panes.setChildrenCollapsible(False)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(170)
        side_layout = QVBoxLayout(sidebar)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.setSpacing(0)
        self.mailbox_name = QLabel("Mailboxes")
        self.mailbox_name.setObjectName("mailboxName")
        self.mailbox_name.setWordWrap(True)
        side_layout.addWidget(self.mailbox_name)
        self.folders = QListWidget()
        self.folders.setObjectName("folders")
        self.folders.setAccessibleName("Mailbox folders")
        self.folders.setItemDelegate(FolderDelegate(self.folders))
        self.folders.currentRowChanged.connect(self.refresh_messages)
        side_layout.addWidget(self.folders, 1)
        local_note = QLabel("Read-only backup")
        local_note.setStyleSheet("color: #939ba7; font-size: 11px; padding: 16px 18px;")
        side_layout.addWidget(local_note)
        self.panes.addWidget(sidebar)

        middle = QWidget()
        middle.setObjectName("messageColumn")
        middle.setMinimumWidth(250)
        middle_layout = QVBoxLayout(middle)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)
        middle_header = QFrame()
        middle_header.setObjectName("messageHeader")
        header_layout = QHBoxLayout(middle_header)
        header_layout.setContentsMargins(22, 16, 18, 16)
        self.folder_title = QLabel("All mail")
        self.folder_title.setObjectName("folderTitle")
        self.message_count = QLabel("")
        self.message_count.setObjectName("messageCount")
        header_layout.addWidget(self.folder_title, 1)
        header_layout.addWidget(self.message_count)
        middle_layout.addWidget(middle_header)
        self.listing = QListWidget()
        self.listing.setObjectName("messages")
        self.listing.setAccessibleName("Emails")
        self.listing.setItemDelegate(MessageDelegate(self.listing))
        self.listing.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.listing.setToolTip("Use Ctrl or Shift to select several emails, then File → Export selected emails.")
        self.listing.itemSelectionChanged.connect(self.update_export_actions)
        self.listing.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.listing.currentItemChanged.connect(self.show_message)
        middle_layout.addWidget(self.listing, 1)
        self.panes.addWidget(middle)

        right = QWidget()
        right.setObjectName("readingPane")
        right.setMinimumWidth(360)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(16, 16, 16, 16)
        card = QFrame()
        card.setObjectName("messageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 12)
        card_layout.setSpacing(14)
        title_row = QHBoxLayout()
        title_row.setSpacing(12)
        self.heading = QLabel("Open a mailbox to start reading")
        self.heading.setObjectName("subject")
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        title_row.addWidget(self.heading, 1)
        self.more_button = self._tool("Email actions", "more")
        self.more_button.setObjectName("emailActions")
        self.message_menu = QMenu(self.more_button)
        self.message_menu.addActions([self.attachment_action, self.export_action, self.images_action])
        self.message_menu.addSeparator()
        self.message_menu.addActions([self.pdf_action, self.print_action])
        self.more_button.setMenu(self.message_menu)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        title_row.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(title_row)
        sender_row = QHBoxLayout()
        sender_row.setSpacing(12)
        self.avatar = QLabel("")
        self.avatar.setObjectName("avatar")
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.avatar.setFixedSize(42, 42)
        self.avatar.hide()
        sender_row.addWidget(self.avatar, 0, Qt.AlignmentFlag.AlignTop)
        sender_info = QVBoxLayout()
        sender_info.setSpacing(3)
        self.sender_label = QLabel("")
        self.sender_label.setObjectName("sender")
        self.sender_label.setWordWrap(True)
        self.sender_label.setTextFormat(Qt.TextFormat.PlainText)
        self.details = QLabel("")
        self.details.setObjectName("details")
        self.details.setWordWrap(True)
        self.details.setTextFormat(Qt.TextFormat.PlainText)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sender_info.addWidget(self.sender_label)
        sender_info.addWidget(self.details)
        sender_row.addLayout(sender_info, 1)
        card_layout.addLayout(sender_row)

        self.attachment_cards = AttachmentList()
        self.attachment_cards.save_requested.connect(self.save_attachment_at)
        card_layout.addWidget(self.attachment_cards)

        self.image_notice = QFrame()
        self.image_notice.setObjectName("imageNotice")
        notice_layout = QHBoxLayout(self.image_notice)
        notice_layout.setContentsMargins(12, 9, 12, 9)
        self.notice_text = QLabel("Remote images are blocked to protect your privacy.")
        self.notice_text.setWordWrap(True)
        notice_layout.addWidget(self.notice_text, 1)
        self.images_link = QLabel()
        self.images_link.setTextFormat(Qt.TextFormat.RichText)
        self.images_link.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse |
                                               Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        self.images_link.setOpenExternalLinks(False)
        self.images_link.linkActivated.connect(self.image_link_clicked)
        notice_layout.addWidget(self.images_link)
        self.image_notice.hide()
        card_layout.addWidget(self.image_notice)
        self.preview = SafeHtmlPreview()
        self.preview.images_changed.connect(self.update_image_notice)
        card_layout.addWidget(self.preview, 1)
        right_layout.addWidget(card)
        self.panes.addWidget(right)
        self.panes.setSizes([230, 370, 820])
        self.panes.setStretchFactor(0, 0)
        self.panes.setStretchFactor(1, 0)
        self.panes.setStretchFactor(2, 1)
        self.content_stack = QStackedWidget()
        self.empty_page = WelcomePage()
        self.empty_page.archive_requested.connect(self.open_archive)
        self.empty_page.folder_requested.connect(self.open_folder)
        self.empty_page.recent_requested.connect(self.open_recent)
        self.empty_view = QScrollArea()
        self.empty_view.setWidgetResizable(True)
        self.empty_view.setFrameShape(QFrame.Shape.NoFrame)
        self.empty_view.setWidget(self.empty_page)
        self.content_stack.addWidget(self.empty_view)
        self.content_stack.addWidget(self.panes)
        outer.addWidget(self.content_stack, 1)
        self.setCentralWidget(container)
        self.activity = QLabel("Ready")
        self.activity.setStyleSheet("color: #8a929d; font-size: 11px; padding: 0 8px;")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setFixedSize(140, 6)
        self.progress_bar.hide()
        self.statusBar().addPermanentWidget(self.activity)
        self.statusBar().addPermanentWidget(self.progress_bar)
        self.statusBar().showMessage("Local, read-only backup viewer")
        self.preview.setAcceptDrops(False)
        self.refresh_recent()

    def refresh_recent(self):
        entries = self.recent_backups.entries()
        self.recent_menu.clear()
        for entry in entries:
            action = self.recent_menu.addAction(f"{Path(entry['path']).name} — {entry['account']}".replace('&', '&&'))
            action.setToolTip(entry['path'])
            action.triggered.connect(lambda checked=False, path=entry['path']: self.open_recent(path))
        if entries:
            self.recent_menu.addSeparator()
            self.recent_menu.addAction("Clear recent backups", self.clear_recent)
        else:
            self.recent_menu.addAction("No recent backups").setEnabled(False)
        self.empty_page.set_recent(entries)
        if self.welcome_dialog is not None:
            self.welcome_dialog.page.set_recent(entries)

    def clear_recent(self):
        self.recent_backups.clear()
        self.refresh_recent()

    def open_recent(self, path):
        if not Path(path).exists():
            QMessageBox.information(self._opening_parent(), "Backup not found",
                                    "This backup has moved or is unavailable. Use Open Archive or Open Folder to find it again.")
            return
        self.open_source(Path(path))

    def dragEnterEvent(self, event):
        if self.worker_thread is None and self.export_worker is None and dropped_backup(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = dropped_backup(event.mimeData())
        if path is not None and self.worker_thread is None and self.export_worker is None:
            event.acceptProposedAction()
            self.open_source(path)

    def check_updates(self):
        if self.update_dialog is None:
            self.update_dialog = UpdateDialog(self)
        if self.update_dialog.isVisible():
            self.update_dialog.activateWindow()
            return
        self.update_dialog.open()
        self.update_dialog.check()

    def showEvent(self, event):
        super().showEvent(event)
        if self._welcome_enabled and not self._startup_welcome_scheduled:
            self._startup_welcome_scheduled = True
            # Let the main window appear first so its child dialog is centred
            # correctly and accessible on startup. This never blocks the loop.
            QTimer.singleShot(0, self._welcome_if_empty)

    def _welcome_if_empty(self):
        if self.isVisible() and self.catalogue is None and self.worker_thread is None and not self._import_error_visible:
            self.show_welcome()

    def show_welcome(self):
        if self.worker_thread and self.worker_thread.isRunning():
            self.statusBar().showMessage("Your mailbox is being prepared. Emails will appear as they are read.")
            return
        if self.welcome_dialog is None:
            self.welcome_dialog = WelcomeDialog(self)
            self.welcome_dialog.archive_requested.connect(self.open_archive)
            self.welcome_dialog.folder_requested.connect(self.open_folder)
            self.welcome_dialog.recent_requested.connect(self.open_recent)
            self.welcome_dialog.backup_dropped.connect(lambda path: self.open_source(Path(path)))
        self.welcome_dialog.page.set_recent(self.recent_backups.entries())
        if self.welcome_dialog.isVisible():
            self.welcome_dialog.activateWindow()
            return
        self.welcome_dialog.open()

    def _opening_parent(self):
        if self.welcome_dialog is not None and self.welcome_dialog.isVisible():
            return self.welcome_dialog
        return self

    def _show_empty_page(self):
        self.content_stack.setCurrentWidget(self.empty_view)
        self.source_label.setText("Open a mailbox backup to get started")
        self.source_label.setToolTip("")
        if self._welcome_enabled:
            QTimer.singleShot(0, self._welcome_if_empty)

    def _tool(self, label, icon, action=None):
        button = QToolButton()
        button.setIcon(line_icon(icon))
        button.setIconSize(QSize(20, 20))
        button.setFixedSize(36, 36)
        button.setToolTip(label)
        button.setAccessibleName(label)
        if action is not None:
            button.clicked.connect(action)
        return button

    def show_search(self):
        dialog = SearchDialog(dict(self.search_options, query=self.search.text()), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.search_options = dialog.options()
            self.search.setText(self.search_options.pop("query"))
            self.refresh_messages()

    def contact_author(self):
        QDesktopServices.openUrl(QUrl("mailto:hello@designstack.co.uk"))

    def show_about(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("About Dovecot Mailbox Viewer")
        dialog.setFixedWidth(440)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(40, 40))
        layout.addWidget(icon)
        title = QLabel("Dovecot Mailbox Viewer")
        title.setStyleSheet("font-size: 19px; font-weight: 600;")
        layout.addWidget(title)
        version = QLabel(f"Version {__version__}")
        version.setStyleSheet("color: #657286;")
        layout.addWidget(version)
        description = QLabel("A local, read-only viewer for Dovecot mdbox backups from JetBackup and cPanel. "
                             "Browse and search emails, save attachments and export messages as .eml files. "
                             "Your original backup is never changed.")
        description.setWordWrap(True)
        layout.addWidget(description)
        credit = QLabel('Created by <a href="https://designstack.co.uk" '
                        'style="color:#357ddb; text-decoration:underline">DesignStack</a>')
        credit.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse |
                                      Qt.TextInteractionFlag.LinksAccessibleByKeyboard)
        credit.linkActivated.connect(lambda url: QDesktopServices.openUrl(QUrl(url)))
        layout.addWidget(credit)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    def download_images(self, _=None):
        if self.preview.remote_urls:
            self.preview.load_images()

    def image_link_clicked(self, action):
        if action == "stop":
            self.preview.cancel_images()
        else:
            self.download_images()

    def update_image_notice(self):
        total = min(MAX_IMAGES, len(self.preview.remote_urls))
        loaded = len(self.preview.remote_images)
        pending = len(self.preview.pending)
        failed = len(self.preview.failed_images)
        self.images_action.setEnabled(total > loaded and not pending)
        self.images_action.setText("Download images")
        self.image_notice.setVisible(total > loaded)
        self.images_link.show()
        if pending:
            self.notice_text.setText(f"Downloading images… {loaded} of {total} loaded")
        elif failed:
            errors = self.preview.failed_images.values()
            if all("timed out" in error for error in errors):
                reason = f"{failed} timed out."
            elif all("stopped" in error for error in errors):
                reason = "Remaining downloads stopped."
            else:
                reason = f"{failed} could not be downloaded."
            self.notice_text.setText(f"{loaded} of {total} images loaded. {reason}")
            self.notice_text.setToolTip("See File → Open diagnostic log for download details.")
        else:
            self.notice_text.setText("Remote images are blocked to protect your privacy.")
            self.notice_text.setToolTip("")
        label = "Stop" if pending else ("Try again" if failed else "Download images")
        action = "stop" if pending else "download"
        self.images_link.setText(f'<a href="{action}" style="color:#786027; text-decoration:underline">{label}</a>')
        if total and loaded == total:
            self.statusBar().showMessage(f"All {loaded} images loaded", 10000)

    def reset_preview(self, title="Select an email to read"):
        from email.message import EmailMessage
        self.shown_message = None
        self.attachments = []
        self.attachment_cards.set_attachments([])
        self.heading.setText(title)
        self.sender_label.clear()
        self.details.clear()
        self.avatar.hide()
        self.preview.display("", EmailMessage())
        for action in (self.attachment_action, self.export_action, self.images_action, self.pdf_action, self.print_action):
            action.setEnabled(False)
        self.update_export_actions()

    def open_archive(self):
        name, _ = QFileDialog.getOpenFileName(self._opening_parent(), "Choose your mailbox backup file", "", "Mailbox backups (*.tar.gz *.tgz)")
        if name:
            return self.open_source(Path(name))
        return False

    def open_folder(self):
        name = QFileDialog.getExistingDirectory(self._opening_parent(), "Choose the extracted backup folder")
        if name:
            return self.open_source(Path(name))
        return False

    def open_source(self, source: Path):
        if self.export_worker is not None:
            QMessageBox.information(self, "Export in progress", "Finish or cancel the current export before opening another backup.")
            return False
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, "Indexing", "Please wait for indexing to finish.")
            return False
        try:
            accounts = discover(source)
            if not accounts:
                raise ValueError("No supported mailbox was found here. Choose a JetBackup/cPanel "
                                 "mailbox archive (.tar.gz or .tgz), or its extracted backup folder. "
                                 "If you selected a folder inside the backup, try the parent folder.")
        except Exception as exc:
            QMessageBox.critical(self._opening_parent(), "Cannot open backup", str(exc))
            return False
        account = sorted(accounts)[0]
        if len(accounts) > 1:
            # There is one active mailbox. Select it once on opening a multi-
            # account backup instead of leaving a dropdown in the main window.
            account, ok = QInputDialog.getItem(self._opening_parent(), "Choose mailbox", "Open mailbox", sorted(accounts), 0, False)
            if not ok:
                return False
        self.source, self.accounts, self.account_name = source, accounts, account
        self.mailbox_name.setText(account)
        self.mailbox_name.setTextFormat(Qt.TextFormat.PlainText)
        self.source_label.setText(source.name)
        self.source_label.setTextFormat(Qt.TextFormat.PlainText)
        self.source_label.setToolTip(str(source))
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self.search_options = {}
        logging.getLogger("viewer").info("Opened source: %s; account: %s", source, account)
        opened = self.load_account()
        if opened and self.welcome_dialog is not None:
            self.welcome_dialog.accept()
        return opened

    def load_account(self, _=None):
        if not self.source or not self.account_name:
            return False
        if self.worker_thread and self.worker_thread.isRunning():
            return False
        if self.catalogue:
            self.catalogue.close()
            self.catalogue = None
        account = self.account_name
        database = cache_path(self.source, account)
        self.folders.clear()
        self.listing.clear()
        self.reset_preview()
        try:
            fingerprint = source_fingerprint(self.source, self.accounts[account])
        except OSError as exc:
            QMessageBox.critical(self._opening_parent(), "Cannot open backup", str(exc))
            self._show_empty_page()
            return False
        try:
            cached = Catalogue(database)
            if cached.reusable(fingerprint):
                self.catalogue = cached
                self.import_done(cached.count())
                self.activity.setText(f"Ready · {cached.count()} messages (cached)")
                self.progress_bar.hide()
                return True
            cached.close()
        except Exception:
            logging.getLogger("viewer").exception("Cache check failed; rebuilding")
        self.heading.setText("Indexing…")
        self.activity.setText(f"Preparing {account}…")
        self.progress_bar.setValue(0)
        self.progress_bar.show()
        self.statusBar().showMessage(f"Reading {account} · original backup remains untouched")
        self.worker_thread = QThread(self)
        self.pending_database = database
        logging.getLogger("viewer").info("Starting worker for %s", account)
        self.worker = ImportWorker(self.source, account, self.accounts[account], database, fingerprint)
        self.worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(self.worker.run)
        self.worker.progress.connect(self.update_progress)
        self.worker.batch.connect(self.import_batch)
        self.worker.completed.connect(self.import_done)
        self.worker.failed.connect(self.import_failed)
        self.worker.completed.connect(self.worker_thread.quit)
        self.worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(self.worker.deleteLater)
        self.worker_thread.finished.connect(self.thread_finished)
        self.content_stack.setCurrentWidget(self.panes)
        self.worker_thread.start()
        return True

    @Slot()
    def thread_finished(self):
        self.worker = None
        self.worker_thread.deleteLater()
        self.worker_thread = None
        self.update_export_actions()
        if self.catalogue is None:
            self._show_empty_page()

    @Slot(int, int)
    def update_progress(self, percent, count):
        self.progress_bar.setValue(percent)
        self.activity.setText(f"Indexing {percent}% · {count} messages")
        self.statusBar().showMessage(f"Indexing {percent}% · {count} messages")

    @Slot(int)
    def import_batch(self, count):
        if self.catalogue is None:
            self.catalogue = Catalogue(self.pending_database)
        self.populate_folders(count)

    @Slot(int)
    def import_done(self, count):
        self.progress_bar.setValue(100)
        self.activity.setText(f"Ready · {count} messages indexed")
        self.progress_bar.hide()
        if self.catalogue is None:
            self.catalogue = Catalogue(self.pending_database)
        self.populate_folders(count)
        if self.source is not None:
            self.recent_backups.remember(self.source, self.account_name)
            self.refresh_recent()

    def populate_folders(self, count):
        self.content_stack.setCurrentWidget(self.panes)
        current = self.folders.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.folders.blockSignals(True)
        self.folders.clear()
        all_item = QListWidgetItem(f"All mail ({count})")
        all_item.setData(DETAILS_ROLE, {"label": "All mail", "icon": "mail", "count": count})
        self.folders.addItem(all_item)
        folder_rows = sorted(self.catalogue.folders(), key=lambda row: (
            {"inbox": 0, "sent": 1, "drafts": 2, "archive": 3, "trash": 4, "spam": 5}.get(row["name"].lower(), 6), row["name"].lower()))
        for row in folder_rows:
            folder_name = row["name"]
            icon = {"inbox": "inbox", "sent": "sent", "drafts": "draft", "archive": "archive",
                    "trash": "trash", "spam": "warning", "junk": "warning"}.get(folder_name.lower(), "folder")
            label = {"INBOX": "Inbox", "spam": "Spam"}.get(folder_name, folder_name)
            item = QListWidgetItem(f"{label} ({row['count']})")
            item.setData(Qt.ItemDataRole.UserRole, folder_name)
            item.setData(DETAILS_ROLE, {"label": label, "icon": icon, "count": row["count"]})
            self.folders.addItem(item)
        chosen = next((i for i in range(self.folders.count()) if self.folders.item(i).data(
            Qt.ItemDataRole.UserRole) == selected), 0)
        self.folders.setCurrentRow(chosen)
        self.folders.blockSignals(False)
        self.refresh_messages()
        self.statusBar().showMessage(f"{count} messages indexed · {self.account_name}")

    @Slot(str)
    def import_failed(self, error):
        self.progress_bar.hide()
        self.activity.setText("Import failed · see File → Open diagnostic log")
        self.heading.setText("Unable to index this backup")
        self.statusBar().showMessage("Import failed")
        self._import_error_visible = True
        try:
            QMessageBox.critical(self, "Import failed", f"{error}\n\nDiagnostic log: {log_path()}")
        finally:
            self._import_error_visible = False
        if self._welcome_enabled:
            QTimer.singleShot(0, self._welcome_if_empty)

    def open_log(self):
        target = log_path()
        if not target.exists():
            QMessageBox.information(self, "Diagnostic log", "No log has been created yet.")
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def refresh_messages(self, _=None):
        if not self.catalogue:
            return
        selected = self.folders.currentItem()
        folder = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        try:
            rows = self.catalogue.messages(folder, self.search.text(), **self.search_options)
        except Exception as exc:
            self.statusBar().showMessage(f"Search error: {exc}")
            return
        previous = self.listing.currentItem()
        selected_id = previous.data(Qt.ItemDataRole.UserRole) if previous else None
        selected_ids = {item.data(Qt.ItemDataRole.UserRole) for item in self.listing.selectedItems()}
        scroll_position = self.listing.verticalScrollBar().value()
        self.listing.blockSignals(True)
        self.listing.clear()
        for row in rows:
            sender = parseaddr(row["sender"] or "")[0] or parseaddr(row["sender"] or "")[1] or "Unknown sender"
            try:
                shown_date = parsedate_to_datetime(row["date"]).strftime("%d %b %Y")
            except (TypeError, ValueError, IndexError):
                shown_date = ""
            snippet = " ".join((row["body"] or "").split())[:180]
            subject = row["subject"] or "(No subject)"
            status_label = "Expunged · " if row["expunged"] else (
                "Deleted · " if row["status"] is not None and row["status"] & DELETED else "")
            item = QListWidgetItem(f"{sender} — {subject}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setData(DETAILS_ROLE, dict(sender=sender, date=shown_date, subject=status_label + subject,
                snippet=snippet, attachment=bool(row["has_attachment"]),
                unread=row["status"] is not None and not row["status"] & SEEN))
            item.setToolTip(f"{row['date']} · {row['folder']}")
            self.listing.addItem(item)
        if selected_id is not None:
            for index in range(self.listing.count()):
                if self.listing.item(index).data(Qt.ItemDataRole.UserRole) == selected_id:
                    self.listing.setCurrentRow(index)
                    break
        if self.listing.currentItem() is None and rows:
            self.listing.setCurrentRow(0)
        surviving = [self.listing.item(index) for index in range(self.listing.count())
                     if self.listing.item(index).data(Qt.ItemDataRole.UserRole) in selected_ids]
        if surviving:
            self.listing.clearSelection()
            for item in surviving:
                item.setSelected(True)
        self.listing.verticalScrollBar().setValue(scroll_position)
        self.listing.blockSignals(False)
        label = (selected.data(DETAILS_ROLE) or {}).get("label", "All mail") if selected else "All mail"
        self.folder_title.setText("Search results" if self.search.text() or any(self.search_options.values()) else label)
        self.message_count.setText(str(len(rows)))
        self.show_message()
        self.update_export_actions()
        self.statusBar().showMessage(f"Showing {len(rows)} messages" + (" (first 5,000)" if len(rows) == 5000 else ""))

    def show_message(self, _=None):
        item = self.listing.currentItem()
        if not item or not self.catalogue:
            self.reset_preview("No emails to display" if self.catalogue else "Open a mailbox to start reading")
            return
        ident = item.data(Qt.ItemDataRole.UserRole)
        # New batches must not discard the reader's scroll or image consent.
        if ident == self.shown_message:
            return
        row = self.catalogue.message(ident)
        if row is None:
            self.reset_preview()
            return
        self.shown_message = ident
        msg, plain, html, _ = describe(row["raw"])
        self.heading.setText(row["subject"] or "(No subject)")
        sender = str(msg.get("From", "Unknown sender"))
        name, address = parseaddr(sender)
        self.sender_label.setText(sender)
        self.avatar.setText("".join(word[0] for word in (name or address or "?").split()[:2]).upper())
        self.avatar.show()
        self.details.setText("\n".join(f"{key}: {msg.get(key, '')}" for key in ("To", "Cc", "Date") if msg.get(key)))
        if html:
            self.preview.display(html, msg)
        else:
            from html import escape
            self.preview.display(f"<pre style='white-space:pre-wrap'>{escape(plain or '(No readable text body)')}</pre>", msg)
        self.attachments = collect_attachments(msg)
        self.attachment_cards.set_attachments(self.attachments)
        self.attachment_action.setText(f"Save attachment… ({len(self.attachments)})" if self.attachments else "Save attachment…")
        self.attachment_action.setEnabled(bool(self.attachments))
        self.export_action.setEnabled(True)
        self.pdf_action.setEnabled(True)
        self.print_action.setEnabled(True)

    def update_export_actions(self):
        count = len(self.listing.selectedItems()) if self.catalogue else 0
        self.bulk_export_action.setText(f"Export selected emails… ({count})" if count else "Export selected emails…")
        self.bulk_export_action.setEnabled(count > 0 and self.export_worker is None)
        folder = self.folders.currentItem()
        folder_count = (folder.data(DETAILS_ROLE) or {}).get('count', 0) if folder else 0
        all_mail = folder is not None and folder.data(Qt.ItemDataRole.UserRole) is None
        self.folder_export_action.setText("Export all mail…" if all_mail else "Export entire folder…")
        self.folder_export_action.setEnabled(bool(self.catalogue and folder_count and self.worker_thread is None
                                                  and self.export_worker is None))

    def export_selected(self):
        ids = [item.data(Qt.ItemDataRole.UserRole) for item in self.listing.selectedItems()]
        if ids:
            self.start_export(ids=ids)

    def export_folder(self):
        if self.worker_thread is not None:
            QMessageBox.information(self, "Indexing", "Wait for indexing to finish to export the complete folder.")
            return
        selected = self.folders.currentItem()
        if selected:
            self.start_export(folder=selected.data(Qt.ItemDataRole.UserRole))

    def start_export(self, *, ids=None, folder=None):
        if not self.catalogue or self.export_worker is not None:
            return
        title = "Choose where to save selected emails" if ids is not None else "Choose where to save the entire folder (all emails)"
        destination = QFileDialog.getExistingDirectory(self, title)
        if not destination:
            return
        if self.source is not None and self.source.is_dir() and Path(destination).resolve().is_relative_to(self.source.resolve()):
            QMessageBox.information(self, "Choose another folder", "Choose a destination outside the original backup to keep it unchanged.")
            return
        worker = ExportWorker(self.catalogue.path, Path(destination), ids=ids, folder=folder, parent=self)
        self.export_worker = worker
        self._export_result = None
        self.export_progress = QProgressDialog("Preparing email export…", "Cancel", 0, 0, self)
        self.export_progress.setWindowTitle("Export emails")
        self.export_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.export_progress.setAutoClose(False)
        self.export_progress.setAutoReset(False)
        self.export_progress.canceled.connect(worker.requestInterruption)
        worker.progress.connect(self.export_progress_changed)
        worker.completed.connect(self.export_completed)
        worker.failed.connect(self.export_failed)
        worker.finished.connect(self.export_finished)
        self.update_export_actions()
        self.export_progress.show()
        worker.start()

    @Slot(int, int)
    def export_progress_changed(self, done, total):
        if self.export_progress and not self.export_progress.wasCanceled():
            self.export_progress.setRange(0, max(total, 1))
            self.export_progress.setValue(done)
            self.export_progress.setLabelText(f"Saving emails… {done:,} of {total:,}")

    @Slot(str, int, bool)
    def export_completed(self, path, count, cancelled):
        self._export_result = (path, count, cancelled)

    @Slot(str)
    def export_failed(self, message):
        self._export_result = message

    @Slot()
    def export_finished(self):
        worker, self.export_worker = self.export_worker, None
        worker.deleteLater()
        self.export_progress.close()
        self.export_progress.deleteLater()
        self.export_progress = None
        self.update_export_actions()
        if isinstance(self._export_result, tuple):
            path, count, cancelled = self._export_result
            title = "Export cancelled" if cancelled else "Export complete"
            text = f"{count:,} emails saved in:\n{path}"
            if cancelled:
                text += "\n\nThe emails already saved have been kept."
            QMessageBox.information(self, title, text)
        else:
            QMessageBox.warning(self, "Export stopped", self._export_result or "The export did not finish.")

    def print_document(self):
        item = self.listing.currentItem()
        if item is None or self.catalogue is None:
            return None
        row = self.catalogue.message(item.data(Qt.ItemDataRole.UserRole))
        message, _, _, _ = describe(row['raw'])
        return MailPrintDocument(self.preview, message, [part.filename for part in self.attachments])

    def export_pdf(self):
        document = self.print_document()
        if document is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save email as PDF", safe_name(self.heading.text()) + '.pdf', "PDF files (*.pdf)")
        if not path:
            return
        if not path.lower().endswith('.pdf'):
            path += '.pdf'
        try:
            save_pdf(document, path, self.heading.text())
            self.statusBar().showMessage(f"Saved {path}", 10000)
        except OSError as exc:
            QMessageBox.warning(self, "Cannot save PDF", str(exc))

    def print_email(self):
        document = self.print_document()
        if document is None:
            return
        printer = make_printer(self.heading.text())
        dialog = QPrintDialog(printer, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            print_document(document, printer)
            if printer.printerState() == printer.PrinterState.Error:
                QMessageBox.warning(self, "Printing failed", "The printer reported an error. Please check the printer and try again.")
            else:
                self.statusBar().showMessage("Email sent to the printer", 5000)

    def export_eml(self):
        item = self.listing.currentItem()
        if not item or not self.catalogue:
            return
        row = self.catalogue.message(item.data(Qt.ItemDataRole.UserRole))
        if row is None:
            return
        import re
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', row['subject'] or 'message')[:100].strip(' .') or 'message'
        destination, _ = QFileDialog.getSaveFileName(self, 'Export email', filename + '.eml', 'Email files (*.eml)')
        if destination:
            try:
                Path(destination).write_bytes(row['raw'])
                self.statusBar().showMessage(f"Exported {destination}")
            except OSError as exc:
                QMessageBox.critical(self, 'Export failed', str(exc))

    def save_attachment(self):
        if not self.attachments:
            return
        if len(self.attachments) == 1:
            self.save_attachment_at(0)
            return
        # Number the choices so duplicate filenames remain individually selectable.
        names = [f"{i+1}. {attachment.filename}" for i, attachment in enumerate(self.attachments)]
        selected, ok = QInputDialog.getItem(self, "Choose attachment", "Attachment", names, 0, False)
        if not ok:
            return
        self.save_attachment_at(names.index(selected))

    def save_attachment_at(self, index):
        if not 0 <= index < len(self.attachments):
            return
        attachment = self.attachments[index]
        default = suggested_filename(attachment.filename)
        destination, _ = QFileDialog.getSaveFileName(self, "Save attachment", default)
        if destination:
            try:
                Path(destination).write_bytes(attachment.data)
                self.statusBar().showMessage(f"Saved {attachment.filename}", 5000)
            except OSError as exc:
                QMessageBox.critical(self, "Cannot save attachment", str(exc))

    def clear_cache(self):
        if self.export_worker is not None:
            QMessageBox.information(self, "Export in progress", "Finish or cancel the export before clearing its search cache.")
            return
        if not self.source or not self.account_name:
            return
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, "Indexing", "Wait for indexing to finish first.")
            return
        if self.catalogue:
            self.catalogue.close()
            self.catalogue = None
        database = cache_path(self.source, self.account_name)
        for suffix in ("", "-wal", "-shm"):
            Path(str(database) + suffix).unlink(missing_ok=True)
        self.folders.clear()
        self.listing.clear()
        self.reset_preview("Search cache cleared")
        self.folder_title.setText("All mail")
        self.message_count.clear()
        self.source = None
        self.account_name = ""
        self.mailbox_name.setText("Mailboxes")
        self.activity.setText("Ready")
        self._show_empty_page()
        self.statusBar().showMessage("Cache removed; original backup unchanged")

    def closeEvent(self, event):
        if self.export_worker is not None:
            QMessageBox.information(self, "Export in progress", "Finish or cancel the export before closing.")
            event.ignore()
            return
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, "Indexing", "Please let indexing finish before closing.")
            event.ignore()
            return
        if self.catalogue:
            self.catalogue.close()
        if self.welcome_dialog is not None:
            self.welcome_dialog.reject()
        if self.update_dialog is not None:
            self.update_dialog.reject()
        self.reset_preview()
        event.accept()


def main():
    configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Dovecot Mailbox Viewer")
    app.setApplicationVersion(__version__)
    app.setWindowIcon(app_icon())
    if len(sys.argv) == 3 and sys.argv[1] == "--smoke-test":
        # CI launches the packaged executable from a folder containing only it.
        from viewer.smoke_test import run
        return run(app, Window, Path(sys.argv[2]))
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
