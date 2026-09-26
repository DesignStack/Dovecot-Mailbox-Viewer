"""Opening remains cancellable and exposes only committed messages to the UI."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication
from viewer.app import Window
from test_welcome import make_backup
from test_browsing import mail


class OpeningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.w = Window(show_welcome=False, settings=QSettings(str(self.root/'settings.ini'), QSettings.Format.IniFormat))
        self.archive, self.folder = make_backup(self.root)
        self.factory = patch('viewer.app.cache_path', return_value=self.root/'index.sqlite3')
        self.factory.start()

    def wait(self, predicate, seconds=5):
        deadline = time.monotonic()+seconds
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertTrue(predicate())

    def tearDown(self):
        self.w.cancel_import()
        self.wait(lambda: self.w.worker_thread is None)
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()
        self.factory.stop()
        self.temp.cleanup()

    def test_discovery_is_off_thread_and_cancellable_before_mail_appears(self):
        entered = threading.Event()
        def discovery(path, check, **kwargs):
            entered.set()
            while True:
                check()
                time.sleep(.005)
        ticks = []
        timer = QTimer()
        timer.timeout.connect(lambda: ticks.append(True))
        timer.start(1)
        with patch('viewer.importing.discover', discovery), patch('viewer.app.QMessageBox.critical') as errors:
            self.assertTrue(self.w.open_source(self.archive))
            self.wait(lambda: entered.is_set() and len(ticks)>2)
            self.w.cancel_import()
            self.wait(lambda: self.w.worker_thread is None)
            self.assertIsNone(self.w.catalogue)
            errors.assert_not_called()
        timer.stop()

    def test_partial_messages_are_readable_cancellation_is_incomplete_and_reopen_rebuilds(self):
        def records(path, info, check, **kwargs):
            yield mail('First available')
            while True:
                check()
                time.sleep(.005)
        with patch('viewer.importing.read_account', records):
            self.w.open_source(self.archive)
            self.wait(lambda: self.w.catalogue is not None)
            self.assertIsNone(self.w.shown_message)
            self.w.listing.setCurrentRow(0)
            self.assertEqual(self.w.heading.text(), 'First available')
            self.assertIsNotNone(self.w.worker_thread)
            self.w.cancel_import()
            self.wait(lambda: self.w.worker_thread is None)
        self.assertEqual(self.w.catalogue.count(), 1)
        self.assertEqual(self.w.catalogue.metadata()['complete'], '0')
        self.assertFalse(self.w.folder_export_action.isEnabled())
        self.w.open_source(self.archive)
        self.wait(lambda: self.w.worker_thread is None)
        self.assertEqual(self.w.heading.text(), 'Welcome example')
        self.assertEqual(self.w.catalogue.metadata()['complete'], '1')

    def test_cached_archive_skips_rescan_and_folder_changes_invalidate(self):
        original = self.archive.read_bytes()
        self.w.open_source(self.archive)
        self.wait(lambda: self.w.worker_thread is None)
        with patch('viewer.importing.discover', side_effect=AssertionError('Should reuse discovery')), \
                patch('viewer.importing.read_account', side_effect=AssertionError('Should reuse index')):
            self.w.open_source(self.archive)
            self.wait(lambda: self.w.worker_thread is None)
        self.assertIn('cached', self.w.activity.text())
        self.assertEqual(self.archive.read_bytes(), original)
        self.w.open_source(self.folder)
        self.wait(lambda: self.w.worker_thread is None)
        self.w.open_source(self.folder)
        self.wait(lambda: self.w.worker_thread is None)
        self.assertIn('cached', self.w.activity.text())
        (self.folder/'changed').write_text('Invalidate the derived cache')
        self.w.open_source(self.folder)
        self.wait(lambda: self.w.worker_thread is None)
        self.assertNotIn('cached', self.w.activity.text())

