"""Dovecot Mailbox Viewer desktop interface."""

from pathlib import Path
from email.utils import parsedate_to_datetime, parseaddr
import sys
import logging
from logging.handlers import RotatingFileHandler
import time

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt, QUrl, QSize
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QProgressBar, QSplitter, QInputDialog,
    QToolButton, QVBoxLayout, QWidget,
)

from viewer.catalog import Catalogue, cache_path, describe, source_fingerprint
from viewer.html_preview import SafeHtmlPreview, MAX_IMAGES
from viewer.icons import line_icon
from viewer.mail_widgets import FolderDelegate, MessageDelegate, DETAILS_ROLE
from viewer.mdbox import discover, read_account
from viewer.dovecot_index import read_statuses, SEEN, DELETED


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
    def __init__(self):
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
        for action in (self.attachment_action, self.export_action, self.images_action):
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
        file_menu.addSeparator()
        file_menu.addActions([self.export_action, clear_cache, open_log])
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
        self.images_link.linkActivated.connect(self.download_images)
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
        outer.addWidget(self.panes, 1)
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

    def download_images(self, _=None):
        if self.preview.remote_urls:
            self.preview.load_images()

    def update_image_notice(self):
        total = min(MAX_IMAGES, len(self.preview.remote_urls))
        loaded = len(self.preview.remote_images)
        pending = len(self.preview.pending)
        failed = len(self.preview.failed_images)
        self.images_action.setEnabled(total > loaded and not pending)
        self.images_action.setText("Download images")
        self.image_notice.setVisible(total > loaded)
        self.images_link.setVisible(not pending)
        if pending:
            self.notice_text.setText(f"Downloading images… {loaded} of {total} loaded")
        elif failed:
            self.notice_text.setText(f"{failed} image(s) could not be downloaded. See File → Open diagnostic log for details.")
        else:
            self.notice_text.setText("Remote images are blocked to protect your privacy.")
        label = "Try again" if failed else "Download images"
        self.images_link.setText(f'<a href="download" style="color:#786027; text-decoration:underline">{label}</a>')
        if total and loaded == total:
            self.statusBar().showMessage(f"{loaded} images downloaded", 5000)

    def reset_preview(self, title="Select an email to read"):
        from email.message import EmailMessage
        self.shown_message = None
        self.attachments = []
        self.heading.setText(title)
        self.sender_label.clear()
        self.details.clear()
        self.avatar.hide()
        self.preview.display("", EmailMessage())
        for action in (self.attachment_action, self.export_action, self.images_action):
            action.setEnabled(False)

    def open_archive(self):
        name, _ = QFileDialog.getOpenFileName(self, "Open JetBackup archive", "", "Archives (*.tar.gz *.tgz)")
        if name:
            self.open_source(Path(name))

    def open_folder(self):
        name = QFileDialog.getExistingDirectory(self, "Open extracted mailbox backup")
        if name:
            self.open_source(Path(name))

    def open_source(self, source: Path):
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, "Indexing", "Please wait for indexing to finish.")
            return
        try:
            accounts = discover(source)
            if not accounts:
                raise ValueError("No mdbox storage files (storage/m.*) were found.")
        except Exception as exc:
            QMessageBox.critical(self, "Cannot open backup", str(exc))
            return
        account = sorted(accounts)[0]
        if len(accounts) > 1:
            # There is one active mailbox. Select it once on opening a multi-
            # account backup instead of leaving a dropdown in the main window.
            account, ok = QInputDialog.getItem(self, "Choose mailbox", "Open mailbox", sorted(accounts), 0, False)
            if not ok:
                return
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
        self.load_account()

    def load_account(self, _=None):
        if not self.source or not self.account_name:
            return
        if self.worker_thread and self.worker_thread.isRunning():
            return
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
            QMessageBox.critical(self, "Cannot open backup", str(exc))
            return
        try:
            cached = Catalogue(database)
            if cached.reusable(fingerprint):
                self.catalogue = cached
                self.import_done(cached.count())
                self.activity.setText(f"Ready · {cached.count()} messages (cached)")
                self.progress_bar.hide()
                return
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
        self.worker_thread.start()

    @Slot()
    def thread_finished(self):
        self.worker = None
        self.worker_thread.deleteLater()
        self.worker_thread = None

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

    def populate_folders(self, count):
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
        QMessageBox.critical(self, "Import failed", f"{error}\n\nDiagnostic log: {log_path()}")

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
        self.listing.verticalScrollBar().setValue(scroll_position)
        self.listing.blockSignals(False)
        label = (selected.data(DETAILS_ROLE) or {}).get("label", "All mail") if selected else "All mail"
        self.folder_title.setText("Search results" if self.search.text() or any(self.search_options.values()) else label)
        self.message_count.setText(str(len(rows)))
        self.show_message()
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
        self.attachments = [p for p in msg.walk() if not p.is_multipart() and (p.get_filename() or p.get_content_disposition() == "attachment")]
        self.attachment_action.setText(f"Save attachment… ({len(self.attachments)})" if self.attachments else "Save attachment…")
        self.attachment_action.setEnabled(bool(self.attachments))
        self.export_action.setEnabled(True)

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
        names = [p.get_filename() or f"attachment-{i+1}" for i, p in enumerate(self.attachments)]
        from PySide6.QtWidgets import QInputDialog
        selected, ok = QInputDialog.getItem(self, "Choose attachment", "Attachment", names, 0, False)
        if not ok:
            return
        index = names.index(selected)
        default = Path(selected).name.replace("/", "_").replace("\\", "_")
        destination, _ = QFileDialog.getSaveFileName(self, "Save attachment", default)
        if destination:
            Path(destination).write_bytes(self.attachments[index].get_payload(decode=True) or b"")

    def clear_cache(self):
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
        self.statusBar().showMessage("Cache removed; original backup unchanged")

    def closeEvent(self, event):
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, "Indexing", "Please let indexing finish before closing.")
            event.ignore()
            return
        if self.catalogue:
            self.catalogue.close()
        self.reset_preview()
        event.accept()


def main():
    configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Dovecot Mailbox Viewer")
    app.setWindowIcon(app_icon())
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
