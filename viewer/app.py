"""Dovecot Mailbox Viewer desktop interface."""

from pathlib import Path
from email.utils import parsedate_to_datetime
import sys
import logging
from logging.handlers import RotatingFileHandler
import time

from PySide6.QtCore import QObject, QThread, Signal, Slot, Qt, QUrl, QSize
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QPixmap, QPainter, QColor, QPen, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QProgressBar, QPushButton, QSplitter,
    QStyle, QToolButton, QVBoxLayout, QWidget,
)

from viewer.catalog import Catalogue, cache_path, describe, source_fingerprint
from viewer.html_preview import SafeHtmlPreview
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
    """Draw a small original mailbox glyph at runtime; no external asset needed."""
    image = QPixmap(64, 64)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#3478d4"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 5, 56, 54, 13, 13)
    painter.setPen(QPen(Qt.GlobalColor.white, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.drawRect(15, 19, 34, 25)
    painter.drawLine(16, 20, 32, 33)
    painter.drawLine(48, 20, 32, 33)
    painter.end()
    return QIcon(image)


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
        self.resize(1380, 860)
        self.catalogue = None
        self.source = None
        self.worker_thread = None
        self.worker = None  # Keep the Python wrapper alive until the Qt thread finishes.
        self.attachments = []
        self.search_options = {}

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #ffffff; color: #252b32; font: 10pt 'Segoe UI'; }
            QMenuBar, QMenu { background: #f9fafc; }
            QLineEdit, QComboBox, QListWidget, QTextBrowser {
                background: white; border: 1px solid #e0e4ea; border-radius: 6px; padding: 5px;
            }
            QListWidget { border: 0; border-radius: 0; outline: none; padding: 0; }
            QListWidget::item { border-bottom: 1px solid #edf0f4; padding: 9px 12px; }
            QListWidget::item:selected { background: #3478d4; color: white; }
            QPushButton { background: #3478d4; color: white; border: 0; border-radius: 5px;
                          padding: 8px 13px; font-weight: 600; }
            QPushButton:hover { background: #2767bd; }
            QPushButton:disabled { background: #b7c5da; }
            QToolButton { border: 1px solid #dbe1ea; background: #fff; border-radius: 6px;
                          padding: 7px; }
            QToolButton:hover { background: #eaf1fb; }
            QFrame#toolbar { background: #f8f9fb; border-bottom: 1px solid #e4e8ef; }
            QFrame#imageNotice { background: #fff6dd; border: 1px solid #f2db9a; border-radius: 5px; }
        """)

        open_archive = QAction("Open archive…", self)
        open_archive.triggered.connect(self.open_archive)
        open_folder = QAction("Open folder…", self)
        open_folder.triggered.connect(self.open_folder)
        clear_cache = QAction("Clear current cache", self)
        clear_cache.triggered.connect(self.clear_cache)
        open_log = QAction("Open diagnostic log", self)
        open_log.triggered.connect(self.open_log)
        file_menu = self.menuBar().addMenu("File")
        export_action = QAction("Export selected email as .eml…", self)
        export_action.triggered.connect(self.export_eml)
        file_menu.addActions([open_archive, open_folder, export_action, clear_cache, open_log])

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        toolbar_frame = QFrame()
        toolbar_frame.setObjectName("toolbar")
        toolbar = QHBoxLayout(toolbar_frame)
        title = QLabel("✉  Dovecot Mailbox Viewer")
        title.setStyleSheet("font-size: 13pt; font-weight: 700; color: #244a7d")
        toolbar.addWidget(title)
        toolbar.addStretch()
        toolbar.addWidget(self._tool("Open backup", QStyle.StandardPixmap.SP_DialogOpenButton, self.open_archive))
        toolbar.addWidget(self._tool("Open folder", QStyle.StandardPixmap.SP_DirOpenIcon, self.open_folder))
        toolbar.addWidget(self._tool("Search options", QStyle.StandardPixmap.SP_FileDialogContentsView, self.show_search))
        outer.addWidget(toolbar_frame)

        row = QHBoxLayout()
        row.setContentsMargins(12, 9, 12, 9)
        self.account = QComboBox()
        self.account.setMinimumWidth(260)
        self.account.currentIndexChanged.connect(self.load_account)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search mail…")
        self.search.setMaximumWidth(420)
        self.search.textChanged.connect(self.refresh_messages)
        row.addWidget(QLabel("Backup:"))
        row.addWidget(self.account)
        row.addStretch()
        row.addWidget(self.search, 1)
        row.addWidget(self._tool("More search options", QStyle.StandardPixmap.SP_FileDialogDetailedView, self.show_search))
        outer.addLayout(row)

        progress_row = QHBoxLayout()
        self.activity = QLabel("Ready")
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("%p%")
        self.progress_bar.hide()
        progress_row.setContentsMargins(12, 0, 12, 6)
        progress_row.addWidget(self.activity)
        progress_row.addWidget(self.progress_bar, 1)
        outer.addLayout(progress_row)

        panes = QSplitter(Qt.Orientation.Horizontal)
        self.folders = QListWidget()
        self.folders.setObjectName("folders")
        self.folders.currentRowChanged.connect(self.refresh_messages)
        panes.addWidget(self.folders)

        self.listing = QListWidget()
        self.listing.setObjectName("messages")
        self.listing.currentItemChanged.connect(self.show_message)
        panes.addWidget(self.listing)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(18, 14, 18, 12)
        right_layout.setSpacing(8)
        self.heading = QLabel("Open an archive or extracted mailbox folder")
        self.heading.setWordWrap(True)
        self.heading.setStyleSheet("font-size: 15pt; font-weight: 700")
        self.details = QLabel("")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details.setStyleSheet("color: #66717f; padding: 4px 0;")
        self.image_notice = QFrame()
        self.image_notice.setObjectName("imageNotice")
        notice_layout = QHBoxLayout(self.image_notice)
        notice_layout.addWidget(QLabel("Remote images are blocked to protect your privacy."), 1)
        images_button = QPushButton("Download images")
        images_button.clicked.connect(self.download_images)
        notice_layout.addWidget(images_button)
        self.image_notice.hide()
        self.preview = SafeHtmlPreview()
        self.save_button = QPushButton("Save attachment…")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_attachment)
        right_layout.addWidget(self.heading)
        right_layout.addWidget(self.details)
        right_layout.addWidget(self.image_notice)
        right_layout.addWidget(self.preview, 1)
        self.export_button = QPushButton("Export email as .eml…")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_eml)
        right_layout.addWidget(self.save_button)
        right_layout.addWidget(self.export_button)
        panes.addWidget(right)
        panes.setSizes([240, 360, 780])
        outer.addWidget(panes, 1)
        self.setCentralWidget(container)
        self.statusBar().showMessage("Ready · Local, read-only backup viewer")

    def _tool(self, label, icon, action):
        button = QToolButton()
        button.setIcon(self.style().standardIcon(icon))
        button.setToolTip(label)
        button.setAccessibleName(label)
        button.clicked.connect(action)
        return button

    def show_search(self):
        dialog = SearchDialog(dict(self.search_options, query=self.search.text()), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.search_options = dialog.options()
            self.search.setText(self.search_options.pop("query"))
            self.refresh_messages()

    def download_images(self):
        if self.preview.remote_urls:
            self.preview.load_images()
            self.image_notice.hide()

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
        self.source, self.accounts = source, accounts
        logging.getLogger("viewer").info("Opened source: %s; accounts: %s", source, ", ".join(accounts))
        self.account.blockSignals(True)
        self.account.clear()
        self.account.addItems(sorted(accounts))
        self.account.blockSignals(False)
        self.load_account()

    def load_account(self, _=None):
        if not self.source or not self.account.currentText():
            return
        if self.worker_thread and self.worker_thread.isRunning():
            return
        if self.catalogue:
            self.catalogue.close()
            self.catalogue = None
        account = self.account.currentText()
        database = cache_path(self.source, account)
        self.folders.clear()
        self.listing.clear()
        try:
            fingerprint = source_fingerprint(self.source, self.accounts[account])
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
        self.activity.setText(f"Indexing… {count} messages processed")
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
        if self.catalogue is None:
            self.catalogue = Catalogue(self.pending_database)
        self.populate_folders(count)

    def populate_folders(self, count):
        current = self.folders.currentItem()
        selected = current.data(Qt.ItemDataRole.UserRole) if current else None
        self.folders.blockSignals(True)
        self.folders.clear()
        all_item = QListWidgetItem(f"  ✉   All mail  ({count})")
        self.folders.addItem(all_item)
        folder_rows = sorted(self.catalogue.folders(), key=lambda row: (
            {"INBOX": 0, "Sent": 1, "Drafts": 2, "Archive": 3, "Trash": 4, "spam": 5}.get(row["name"], 6), row["name"].lower()))
        for row in folder_rows:
            folder_name = row["name"]
            icon = {"INBOX": "▣", "Sent": "➤", "Drafts": "▤", "Archive": "▧", "Trash": "♲", "spam": "⚠"}.get(folder_name, "▸")
            item = QListWidgetItem(f"  {icon}   {folder_name}  ({row['count']})")
            item.setData(Qt.ItemDataRole.UserRole, folder_name)
            self.folders.addItem(item)
        chosen = next((i for i in range(self.folders.count()) if self.folders.item(i).data(
            Qt.ItemDataRole.UserRole) == selected), 0)
        self.folders.setCurrentRow(chosen)
        self.folders.blockSignals(False)
        self.refresh_messages()
        self.statusBar().showMessage(f"{count} messages indexed · {self.account.currentText()}")

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
        self.listing.blockSignals(True)
        self.listing.clear()
        for row in rows:
            sender = (row["sender"] or "Unknown sender").split("<")[0].strip()[:45]
            try:
                shown_date = parsedate_to_datetime(row["date"]).strftime("%d %b %Y")
            except (TypeError, ValueError, IndexError):
                shown_date = ""
            snippet = " ".join((row["body"] or "").split())[:130]
            attachment = "  📎" if row["has_attachment"] else ""
            marker = "● " if row["status"] is not None and not row["status"] & SEEN else ""
            marker += " [Deleted]" if row["status"] is not None and row["status"] & DELETED else ""
            marker += " [Expunged]" if row["expunged"] else ""
            item = QListWidgetItem(f"{marker}{sender[:27]}{attachment}    {shown_date}\n{row['subject'] or '(No subject)'}\n{snippet}")
            item.setData(Qt.ItemDataRole.UserRole, row["id"])
            item.setToolTip(f"{row['date']} · {row['folder']}")
            item.setSizeHint(QSize(290, 78))
            self.listing.addItem(item)
        if selected_id is not None:
            for index in range(self.listing.count()):
                if self.listing.item(index).data(Qt.ItemDataRole.UserRole) == selected_id:
                    self.listing.setCurrentRow(index)
                    break
        self.listing.blockSignals(False)
        self.statusBar().showMessage(f"Showing {len(rows)} messages" + (" (first 5,000)" if len(rows) == 5000 else ""))

    def show_message(self, _=None):
        item = self.listing.currentItem()
        if not item or not self.catalogue:
            return
        ident = item.data(Qt.ItemDataRole.UserRole)
        row = self.catalogue.message(ident)
        if row is None:
            return
        msg, plain, html, _ = describe(row["raw"])
        self.heading.setText(row["subject"] or "(No subject)")
        self.details.setText("\n".join(f"{key}: {msg.get(key, '')}" for key in ("From", "To", "Cc", "Date") if msg.get(key)) + f"\nFolder: {row['folder']}")
        if html:
            self.preview.display(html, msg)
            self.image_notice.setVisible(bool(self.preview.remote_urls))
        elif plain:
            self.preview.display(f"<pre style='white-space:pre-wrap'>{__import__('html').escape(plain)}</pre>", msg)
            self.image_notice.hide()
        else:
            self.preview.setPlainText("(No readable text body)")
            self.image_notice.hide()
        self.attachments = [p for p in msg.walk() if not p.is_multipart() and (p.get_filename() or p.get_content_disposition() == "attachment")]
        self.save_button.setText(f"Save attachment… ({len(self.attachments)})")
        self.save_button.setEnabled(bool(self.attachments))
        self.export_button.setEnabled(True)

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
        if not self.source or not self.account.currentText():
            return
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, "Indexing", "Wait for indexing to finish first.")
            return
        if self.catalogue:
            self.catalogue.close()
            self.catalogue = None
        database = cache_path(self.source, self.account.currentText())
        for suffix in ("", "-wal", "-shm"):
            Path(str(database) + suffix).unlink(missing_ok=True)
        self.folders.clear()
        self.listing.clear()
        self.preview.clear()
        self.heading.setText("Search cache cleared")
        self.statusBar().showMessage("Cache removed; original backup unchanged")

    def closeEvent(self, event):
        if self.worker_thread and self.worker_thread.isRunning():
            QMessageBox.information(self, "Indexing", "Please let indexing finish before closing.")
            event.ignore()
            return
        if self.catalogue:
            self.catalogue.close()
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
