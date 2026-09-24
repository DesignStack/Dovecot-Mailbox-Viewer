"""DesignStack Dovecot Mailbox Viewer desktop interface."""

from pathlib import Path
import sys

from PySide6.QtCore import QObject, QThread, Signal, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
    QLineEdit, QListWidget, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QTableWidget, QTableWidgetItem, QTextBrowser, QVBoxLayout, QWidget,
)

from viewer.catalog import Catalogue, cache_path, describe, html_to_text
from viewer.mdbox import discover, read_account


class ImportWorker(QObject):
    progress = Signal(int)
    completed = Signal(int)
    failed = Signal(str)

    def __init__(self, source: Path, account: str, info: dict, database: Path):
        super().__init__()
        self.source, self.account, self.info, self.database = source, account, info, database

    def run(self):
        catalogue = None
        try:
            catalogue = Catalogue(self.database)
            catalogue.reset()
            for folder in sorted(self.info["folders"]):
                catalogue.add_folder(folder)
            count = 0
            for record in read_account(self.source, self.info):
                catalogue.add(record)
                count += 1
                if count % 25 == 0:
                    catalogue.commit()
                    self.progress.emit(count)
            catalogue.commit()
            self.completed.emit(count)
        except Exception as exc:
            self.failed.emit(str(exc))
        finally:
            if catalogue is not None:
                catalogue.close()


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DesignStack Dovecot Mailbox Viewer")
        self.resize(1320, 800)
        self.catalogue = None
        self.source = None
        self.worker_thread = None
        self.attachments = []

        self.setStyleSheet("""
            QMainWindow, QWidget { background: #f5f7fb; color: #17243b; font: 10pt 'Segoe UI'; }
            QLineEdit, QComboBox, QListWidget, QTableWidget, QTextBrowser {
                background: white; border: 1px solid #dfe5ee; border-radius: 6px; padding: 5px;
            }
            QPushButton { background: #234ec4; color: white; border: 0; border-radius: 6px;
                          padding: 9px 14px; font-weight: 600; }
            QPushButton:disabled { background: #aab7d3; }
            QPushButton:hover { background: #183f9d; }
            QHeaderView::section { background: #eaf0fa; padding: 8px; border: 0; }
        """)

        open_archive = QAction("Open archive…", self)
        open_archive.triggered.connect(self.open_archive)
        open_folder = QAction("Open folder…", self)
        open_folder.triggered.connect(self.open_folder)
        clear_cache = QAction("Clear current cache", self)
        clear_cache.triggered.connect(self.clear_cache)
        file_menu = self.menuBar().addMenu("File")
        file_menu.addActions([open_archive, open_folder, clear_cache])

        container = QWidget()
        outer = QVBoxLayout(container)
        toolbar = QHBoxLayout()
        title = QLabel("DesignStack  /  Dovecot Mailbox Viewer")
        title.setStyleSheet("font-size: 17pt; font-weight: 700; color: #183477")
        toolbar.addWidget(title)
        toolbar.addStretch()
        archive_btn = QPushButton("Open archive")
        archive_btn.clicked.connect(self.open_archive)
        folder_btn = QPushButton("Open folder")
        folder_btn.clicked.connect(self.open_folder)
        toolbar.addWidget(archive_btn)
        toolbar.addWidget(folder_btn)
        outer.addLayout(toolbar)

        row = QHBoxLayout()
        self.account = QComboBox()
        self.account.setMinimumWidth(240)
        self.account.currentIndexChanged.connect(self.load_account)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sender, subject, recipients or message text…")
        self.search.textChanged.connect(self.refresh_messages)
        row.addWidget(QLabel("Mailbox:"))
        row.addWidget(self.account)
        row.addWidget(self.search, 1)
        outer.addLayout(row)

        panes = QSplitter(Qt.Orientation.Horizontal)
        self.folders = QListWidget()
        self.folders.currentRowChanged.connect(self.refresh_messages)
        panes.addWidget(self.folders)

        self.listing = QTableWidget(0, 4)
        self.listing.setHorizontalHeaderLabels(["From", "Subject", "Date", "📎"])
        self.listing.verticalHeader().hide()
        self.listing.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.listing.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.listing.setAlternatingRowColors(True)
        self.listing.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.listing.itemSelectionChanged.connect(self.show_message)
        panes.addWidget(self.listing)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        self.heading = QLabel("Open an archive or extracted mailbox folder")
        self.heading.setWordWrap(True)
        self.heading.setStyleSheet("font-size: 14pt; font-weight: 700")
        self.details = QLabel("")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        self.preview.setOpenExternalLinks(False)
        self.preview.anchorClicked.connect(self.confirm_link)
        self.save_button = QPushButton("Save attachment…")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(self.save_attachment)
        right_layout.addWidget(self.heading)
        right_layout.addWidget(self.details)
        right_layout.addWidget(self.preview, 1)
        right_layout.addWidget(self.save_button)
        panes.addWidget(right)
        panes.setSizes([230, 520, 570])
        outer.addWidget(panes, 1)
        self.setCentralWidget(container)
        self.statusBar().showMessage("Ready · Local, read-only backup viewer")

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
        self.listing.setRowCount(0)
        self.heading.setText("Indexing…")
        self.statusBar().showMessage(f"Reading {account} · original backup remains untouched")
        self.worker_thread = QThread(self)
        worker = ImportWorker(self.source, account, self.accounts[account], database)
        worker.moveToThread(self.worker_thread)
        self.worker_thread.started.connect(worker.run)
        worker.progress.connect(lambda count: self.statusBar().showMessage(f"Indexed {count} messages…"))
        worker.completed.connect(lambda count: self.import_done(database, count))
        worker.failed.connect(lambda error: self.import_failed(error))
        worker.completed.connect(self.worker_thread.quit)
        worker.failed.connect(self.worker_thread.quit)
        self.worker_thread.finished.connect(worker.deleteLater)
        self.worker_thread.finished.connect(self.worker_thread.deleteLater)
        self.worker_thread.start()

    def import_done(self, database, count):
        self.catalogue = Catalogue(database)
        self.folders.addItem(f"All folders  ({count})")
        for row in self.catalogue.folders():
            self.folders.addItem(f"{row['name']}  ({row['count']})")
            self.folders.item(self.folders.count() - 1).setData(Qt.ItemDataRole.UserRole, row["name"])
        self.folders.setCurrentRow(0)
        self.statusBar().showMessage(f"{count} messages indexed · {self.account.currentText()}")

    def import_failed(self, error):
        self.heading.setText("Unable to index this backup")
        self.statusBar().showMessage("Import failed")
        QMessageBox.critical(self, "Import failed", error)

    def refresh_messages(self, _=None):
        if not self.catalogue:
            return
        selected = self.folders.currentItem()
        folder = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        try:
            rows = self.catalogue.messages(folder, self.search.text())
        except Exception as exc:
            self.statusBar().showMessage(f"Search error: {exc}")
            return
        self.listing.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for col, value in enumerate((row["sender"], row["subject"], row["date"], "📎" if row["has_attachment"] else "")):
                item = QTableWidgetItem(value or "")
                if col == 0:
                    item.setData(Qt.ItemDataRole.UserRole, row["id"])
                self.listing.setItem(index, col, item)
        self.statusBar().showMessage(f"Showing {len(rows)} messages" + (" (first 5,000)" if len(rows) == 5000 else ""))

    def show_message(self):
        selection = self.listing.selectedItems()
        if not selection or not self.catalogue:
            return
        ident = self.listing.item(selection[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        row = self.catalogue.message(ident)
        if row is None:
            return
        msg, plain, html, _ = describe(row["raw"])
        self.heading.setText(row["subject"] or "(No subject)")
        self.details.setText("\n".join(f"{key}: {msg.get(key, '')}" for key in ("From", "To", "Cc", "Date") if msg.get(key)) + f"\nFolder: {row['folder']}")
        # Show text only: do not load remote tracking images or active email content.
        if plain:
            self.preview.setPlainText(plain)
        elif html:
            self.preview.setPlainText(html_to_text(html))
        else:
            self.preview.setPlainText("(No readable text body)")
        self.attachments = [p for p in msg.walk() if not p.is_multipart() and (p.get_filename() or p.get_content_disposition() == "attachment")]
        self.save_button.setText(f"Save attachment… ({len(self.attachments)})")
        self.save_button.setEnabled(bool(self.attachments))

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

    def confirm_link(self, url: QUrl):
        if url.scheme() not in ("https", "http"):
            return
        if QMessageBox.question(self, "Open external link?", f"Open this link in your browser?\n{url.toString()}") == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(url)

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
        self.listing.setRowCount(0)
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
    app = QApplication(sys.argv)
    app.setApplicationName("DesignStack Dovecot Mailbox Viewer")
    window = Window()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
