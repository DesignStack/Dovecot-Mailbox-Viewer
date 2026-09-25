"""Exercise multi-selection, export actions, PDF/print, drop targets and history."""
from email.message import EmailMessage
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QMimeData, QPoint, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QImage, QTextDocument
from PySide6.QtWidgets import QApplication, QDialog, QLabel

from viewer.app import Window
from viewer.catalog import Catalogue
from viewer.mdbox import Record
from viewer.printing import make_printer
from viewer.recent import RecentBackups, dropped_backup


class DesktopToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.window = Window(show_welcome=False, settings=self.settings)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.temporary.cleanup()

    def mailbox(self):
        catalogue = Catalogue(self.root / 'test.sqlite3')
        for i in range(3):
            message = EmailMessage()
            message['From'] = 'Sender <sender@example.com>'
            message['To'] = 'Recipient <recipient@example.com>'
            message['Date'] = 'Fri, 25 Sep 2026 10:00:00 +0100'
            message['Subject'] = f'Test message {i}'
            message.set_content('Body text')
            message.add_alternative('<p>Readable HTML body</p><img src="https://example.com/tracker.png">', subtype='html')
            catalogue.add(Record('INBOX', message.as_bytes(), 'synthetic'))
        catalogue.commit()
        self.window.catalogue = catalogue
        self.window.populate_folders(3)
        self.window.show()
        self.app.processEvents()

    def test_multiselection_survives_refresh_and_exports_in_background(self):
        self.mailbox()
        w = self.window
        w.listing.selectAll()
        w.populate_folders(3)
        self.assertEqual(len(w.listing.selectedItems()), 3)
        with patch('viewer.app.QFileDialog.getExistingDirectory', return_value=str(self.root)), \
                patch('viewer.app.QMessageBox.information') as finished:
            w.bulk_export_action.trigger()
            deadline = time.monotonic() + 10
            while w.export_worker is not None and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.assertIsNone(w.export_worker)
            self.assertIn('3 emails saved', finished.call_args.args[2])
        exported = list(self.root.glob('Mail-export-*/*/*.eml'))
        expected = {row[0] for row in w.catalogue.conn.execute('SELECT raw FROM messages')}
        self.assertEqual({file.read_bytes() for file in exported}, expected)
        w.search.setText('no match')
        self.assertTrue(w.folder_export_action.isEnabled())
        self.assertFalse(w.bulk_export_action.isEnabled())
        self.assertFalse(w.pdf_action.isEnabled())

    def test_pdf_and_print_include_headers_without_loading_external_resources(self):
        self.mailbox()
        w = self.window
        document = w.print_document()
        text = document.toPlainText()
        for expected in ('From:', 'To:', 'Date:', 'Test message', 'Readable HTML body', 'omitted'):
            self.assertIn(expected, text)
        for url in ('https://example.com/tracker.png', 'file:///private.txt'):
            resource = document.resource(QTextDocument.ResourceType.ImageResource, QUrl(url))
            self.assertIsInstance(resource, QImage)
            self.assertEqual(resource.pixelColor(0, 0).alpha(), 0)
        target = self.root / 'mail.pdf'
        with patch('viewer.app.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            w.pdf_action.trigger()
        self.assertTrue(target.read_bytes().startswith(b'%PDF-'))
        printer = make_printer('Print test', pdf=True)
        printed = self.root / 'printed.pdf'
        printer.setOutputFileName(str(printed))
        with patch('viewer.app.make_printer', return_value=printer), \
                patch('viewer.app.QPrintDialog.exec', return_value=QDialog.DialogCode.Rejected):
            w.print_action.trigger()
        self.assertFalse(printed.exists())
        with patch('viewer.app.make_printer', return_value=printer), \
                patch('viewer.app.QPrintDialog.exec', return_value=QDialog.DialogCode.Accepted):
            w.print_action.trigger()
        self.assertTrue(printed.read_bytes().startswith(b'%PDF-'))
        self.assertEqual(w.preview.remote_images, {})

    def test_recent_history_persists_deduplicates_and_clears_without_deleting_backup(self):
        history = self.window.recent_backups
        for i in range(10):
            history.remember(self.root / f'backup-{i}.tgz', 'mail@example.com')
        backup = self.root / 'backup-9.tgz'
        backup.write_bytes(b'archive placeholder')
        history.remember(backup, 'mail@example.com')
        reopened = RecentBackups(QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat))
        self.assertEqual(len(reopened.entries()), 8)
        self.assertEqual(reopened.entries()[0]['path'], str(backup.resolve()))
        self.window.refresh_recent()
        self.window.show_welcome()
        with patch.object(self.window, 'open_source') as opened:
            self.window.open_recent(str(backup))
            opened.assert_called_once_with(backup)
        with patch('viewer.app.QMessageBox.information') as missing:
            self.window.open_recent(str(self.root / 'missing.tgz'))
            missing.assert_called_once()
        self.window.clear_recent()
        self.assertEqual(reopened.entries(), [])
        self.assertTrue(backup.exists())
        labels = self.window.welcome_dialog.findChildren(QLabel)
        self.assertFalse(any('Not sure?' in label.text() for label in labels))

    def test_drag_drop_opens_single_local_backup_on_main_and_welcome(self):
        archive = self.root / 'backup.tgz'
        archive.write_bytes(b'archive placeholder')
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(archive))])
        self.assertEqual(dropped_backup(mime), archive)
        self.window.show()
        self.window.show_welcome()
        for target in (self.window, self.window.welcome_dialog):
            drag = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                              Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            with patch.object(self.window, 'open_source') as opened:
                QApplication.sendEvent(target, drag)
                self.assertTrue(drag.isAccepted())
                QApplication.sendEvent(target, drop)
                opened.assert_called_once_with(archive)
        mime.setUrls([QUrl('https://example.com/backup.tgz')])
        self.assertIsNone(dropped_backup(mime))
        mime.setUrls([QUrl.fromLocalFile(str(archive))] * 2)
        self.assertIsNone(dropped_backup(mime))
        self.window.welcome_dialog.reject()
        self.window.exit_action.trigger()
        self.assertFalse(self.window.isVisible())
