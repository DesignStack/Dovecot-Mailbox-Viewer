"""Exercise the GUI thread handoff using synthetic, non-customer mail."""

from io import BytesIO
from pathlib import Path
import gzip
import tarfile
import tempfile
import time
import unittest

from PySide6.QtWidgets import QApplication

from viewer.app import Window


class GuiImportTests(unittest.TestCase):
    def test_import_finishes_and_messages_are_selectable(self):
        app = QApplication.instance() or QApplication([])
        raw = b"From: Example <test@example.com>\nSubject: GUI check\n\nHello.\n"
        compressed = gzip.compress(raw)
        storage = (b"2 M1e C00000000\n\x01\x02N " + f"{len(compressed):016X}".encode() +
                   b"\n" + compressed + b"\n\x01\x03\nBINBOX\n\n")
        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "backup.tar.gz"
            with tarfile.open(archive, "w:gz") as tf:
                info = tarfile.TarInfo("backup/email/example.com/test/storage/m.1")
                info.size = len(storage)
                tf.addfile(info, BytesIO(storage))
            window = Window()
            try:
                window.open_source(archive)
                deadline = time.monotonic() + 10
                while (window.catalogue is None or window.worker_thread is not None) and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                self.assertIsNotNone(window.catalogue)
                self.assertIsNone(window.worker_thread)
                self.assertEqual(window.progress_bar.value(), 100)
                self.assertEqual(window.listing.rowCount(), 1)
                window.listing.selectRow(0)
                app.processEvents()
                self.assertEqual(window.heading.text(), "GUI check")
            finally:
                # Drain the worker before Qt destroys its thread at test shutdown.
                while window.worker_thread is not None and time.monotonic() < deadline:
                    app.processEvents()
                    time.sleep(0.01)
                window.close()
