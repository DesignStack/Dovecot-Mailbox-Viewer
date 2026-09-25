"""Exercise onboarding with real synthetic backups and native picker outcomes."""
from io import BytesIO
from pathlib import Path
import gzip
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from viewer.app import Window


def make_backup(root, empty=False, broken=False):
    raw = b'From: Example <mail@example.com>\nSubject: Welcome example\n\nHello.\n'
    payload = gzip.compress(raw)
    storage = b'2 M1e C00000000\n'
    if not empty:
        storage += b'\x01\x02N ' + f'{len(payload):016X}'.encode() + b'\n' + payload + b'\n\x01\x03\nBINBOX\n\n'
    if broken:
        storage = b'invalid storage'
    relative = 'backup/email/example.com/mail/storage/m.1'
    folder = root / 'extracted'
    target = folder / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(storage)
    archive = root / 'backup.tar.gz'
    with tarfile.open(archive, 'w:gz') as tf:
        member = tarfile.TarInfo(relative)
        member.size = len(storage)
        tf.addfile(member, BytesIO(storage))
    return archive, folder


class WelcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = Window()
        self.window.show()
        self.app.processEvents()
        self.assertIsNotNone(self.window.welcome_dialog)
        self.assertTrue(self.window.welcome_dialog.isVisible())

    def tearDown(self):
        self.wait_for_import()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wait_for_import(self):
        deadline = time.monotonic() + 10
        while self.window.worker_thread is not None and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        self.app.processEvents()
        self.assertIsNone(self.window.worker_thread)

    def test_cancel_and_invalid_selection_leave_working_choices(self):
        w = self.window
        guide = w.welcome_dialog
        self.assertIs(w.content_stack.currentWidget(), w.empty_view)
        with patch('viewer.app.QFileDialog.getOpenFileName', return_value=('', '')) as choose:
            guide.page.choices.archive_button.click()
            self.assertIs(choose.call_args.args[0], guide)
        self.assertTrue(guide.isVisible())
        with tempfile.TemporaryDirectory() as temp, \
             patch('viewer.app.QFileDialog.getExistingDirectory', return_value=temp), \
             patch('viewer.app.QMessageBox.critical') as error:
            guide.page.choices.folder_button.click()
            self.wait_for_import()
            self.assertIn('No supported mailbox', error.call_args.args[2])
        self.assertTrue(guide.isVisible())
        self.assertIsNone(w.catalogue)
        guide.reject()
        self.app.processEvents()
        self.assertFalse(guide.isVisible())
        self.assertTrue(w.empty_page.choices.archive_button.isVisible())
        with patch('viewer.app.QFileDialog.getExistingDirectory', return_value='') as choose:
            w.empty_page.choices.folder_button.click()
            choose.assert_called_once()
        w.getting_started_action.trigger()
        self.assertTrue(guide.isVisible())
        self.assertIs(w.welcome_dialog, guide)

    def test_archive_folder_cache_and_clearing_return_to_welcome(self):
        w = self.window
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, folder = make_backup(root)
            original = archive.read_bytes()
            with patch('viewer.app.cache_path', return_value=root / 'catalogue.sqlite3'):
                for source, picker, value, button in (
                    (archive, 'getOpenFileName', (str(archive), ''), w.welcome_dialog.page.choices.archive_button),
                    (folder, 'getExistingDirectory', str(folder), w.welcome_dialog.page.choices.folder_button),
                ):
                    with self.subTest(source=source.name), patch('viewer.app.QFileDialog.' + picker, return_value=value):
                        w.show_welcome()
                        button.click()
                        self.assertFalse(w.welcome_dialog.isVisible())
                        self.wait_for_import()
                        self.assertEqual(w.catalogue.count(), 1)
                        self.assertEqual(w.heading.text(), 'Welcome example')
                        self.assertIs(w.content_stack.currentWidget(), w.panes)
                        # Reopen the same backup through the guide: use the cache.
                        w.show_welcome()
                        button.click()
                        self.wait_for_import()
                        self.assertIsNone(w.worker_thread)
                        self.assertFalse(w.welcome_dialog.isVisible())
                        self.assertIn('cached', w.activity.text())
                        w.clear_cache()
                        self.app.processEvents()
                        self.assertIsNone(w.catalogue)
                        self.assertTrue(w.welcome_dialog.isVisible())
                        self.assertIs(w.content_stack.currentWidget(), w.empty_view)
                self.assertEqual(archive.read_bytes(), original)

    def test_failed_import_returns_to_guide_but_empty_mailbox_does_not(self):
        w = self.window
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive, _ = make_backup(root, broken=True)
            with patch('viewer.app.cache_path', return_value=root / 'catalogue.sqlite3'), \
                 patch('viewer.app.QMessageBox.critical') as error:
                self.assertTrue(w.open_source(archive))
                self.wait_for_import()
                error.assert_called_once()
                self.assertTrue(w.welcome_dialog.isVisible())
                archive, _ = make_backup(root, empty=True)
                self.assertTrue(w.open_source(archive))
                self.wait_for_import()
                self.assertEqual(w.catalogue.count(), 0)
                self.assertFalse(w.welcome_dialog.isVisible())
                self.assertIs(w.content_stack.currentWidget(), w.panes)
                w.catalogue.close()
                w.catalogue = None
