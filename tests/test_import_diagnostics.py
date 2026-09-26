"""Regression checks for measurable archive progress and large-mailbox browsing."""
from email.message import EmailMessage
from io import BytesIO
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import zipfile
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, QTimer
from PySide6.QtGui import QTextDocument
from PySide6.QtWidgets import QApplication
from viewer.app import Window
from viewer.catalog import Catalogue
from viewer.diagnostics import save_diagnostics
from viewer.html_preview import SafeHtmlPreview, preview_html
from viewer.mdbox import discover, read_account
from viewer.operations import Cancellation, Cancelled
from viewer.progress import ImportProgress, format_progress
from test_mdbox import storage_record, storage_file
from test_browsing import mail


class ProgressTests(unittest.TestCase):
    def test_stalled_event_loop_writes_a_native_trace_without_gui_cooperation(self):
        with tempfile.TemporaryDirectory() as temp:
            # Run separately: fault handlers and Qt log hooks are process-wide.
            code = '''
import time
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication
from viewer import diagnostics as d
d.configure_logging()
app = QCoreApplication([])
original = d.faulthandler.dump_traceback_later
with patch.object(d.faulthandler, 'dump_traceback_later',
    side_effect=lambda timeout, **kw: original(.05, **kw)):
    d.start_watchdog(app)
    time.sleep(.15)  # Deliberately do not process Qt events.
d.faulthandler.cancel_dump_traceback_later()
d._fault_stream.flush()
assert 'Timeout' in d.log_path().with_name('viewer-fault.log').read_text()
'''
            result = subprocess.run([sys.executable, '-c', code], timeout=15,
                env=dict(os.environ, LOCALAPPDATA=temp), capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_stage_percentage_eta_and_stall_are_based_on_work(self):
        clock = [100.0]
        state = ImportProgress(clock=lambda: clock[0])
        state.phase('Discovering mailbox')
        state.update(25, 100)
        clock[0] = 105.0
        percent, detail, short = format_progress(state.snapshot(), clock[0])
        self.assertEqual(percent, 25)
        self.assertIn('Stage remaining ~15s', detail)
        clock[0] = 120.0
        state.update()  # A heartbeat must not pretend bytes have advanced.
        self.assertIn('No measurable progress for 20s', format_progress(state.snapshot(), clock[0])[1])
        state.phase('Checking files', unit='paths')
        self.assertIsNone(format_progress(state.snapshot(), clock[0])[0])

    def test_compressed_archive_reports_skipped_bytes_and_is_cancellable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'backup.tar.gz'
            raw = b'Subject: Last member\n\nHello\n'
            with tarfile.open(path, 'w:gz') as archive:
                for name, data in [('padding.bin', os.urandom(2_000_000)),
                        ('backup/email/example.com/test/storage/m.1', storage_file(storage_record(raw)))]:
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, BytesIO(data))
            amounts = []
            def progress(done=None, total=None, **kwargs):
                if done is not None:
                    amounts.append((done, total))
            info = discover(path, progress=progress)['test@example.com']
            self.assertGreater(len(amounts), 10)
            self.assertEqual(amounts[-1][1], path.stat().st_size)
            self.assertEqual([r.raw for r in read_account(path, info, progress=progress)], [raw])
            cancellation = Cancellation()
            def stop(done=None, total=None, **kwargs):
                if done and done > 20_000:
                    cancellation.cancel()
            with self.assertRaises(Cancelled):
                discover(path, cancellation.check, progress=stop)

    def test_report_only_includes_allowlisted_diagnostics(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('viewer.log', 'viewer-fault.log', 'mail.sqlite3', 'private.eml', 'backup.tar.gz'):
                (root / name).write_text(name)
            report = root / 'report.zip'
            save_diagnostics(report, folder=root, activity={'messages': 123})
            with zipfile.ZipFile(report) as archive:
                self.assertEqual(set(archive.namelist()),
                    {'viewer.log', 'viewer-fault.log', 'system.json', 'last-import.json'})

    def test_reader_sees_commits_without_requesting_writer_lock_or_raw_blobs(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'cache.sqlite3'
            writer = Catalogue(path)
            writer.add(mail('Committed', ident='one@x'))
            writer.commit()
            reader = Catalogue(path, readonly=True)
            writer.add(mail('Uncommitted', ident='two@x'))
            self.assertEqual(reader.count(), 1)
            with self.assertRaises(sqlite3.OperationalError):
                reader.conn.execute("DELETE FROM messages")
            # Conversation sorting must never materialise complete raw emails.
            reader.conn.set_authorizer(lambda action, table, column, database, trigger:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'messages'
                    and column == 'raw' else sqlite3.SQLITE_OK)
            self.assertEqual(reader.messages(conversations=True)[0]['subject'], 'Committed')
            plan = reader.conn.execute('EXPLAIN QUERY PLAN SELECT id FROM messages '
                'ORDER BY sent_timestamp IS NULL, sent_timestamp DESC, id DESC LIMIT 200').fetchall()
            self.assertFalse(any('TEMP B-TREE' in str(tuple(row)) for row in plan))
            writer.commit()
            self.assertEqual(reader.count(), 2)
            reader.close()
            writer.close()


class LargeImportGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_many_messages_remain_cancellable_browsable_and_preserve_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            storage = root / 'backup/email/example.com/test/storage/m.1'
            storage.parent.mkdir(parents=True)
            storage.write_bytes(storage_file(*(storage_record(
                f'Subject: Message {i}\nDate: Fri, 25 Sep 2026 10:00:00 +0000\n\n'.encode()
                + b'Large synthetic body. ' * 1000) for i in range(1200))))
            w = Window(show_welcome=False, settings=QSettings(str(root/'prefs.ini'), QSettings.Format.IniFormat))
            ticks = []
            timer = QTimer()
            timer.timeout.connect(lambda: ticks.append(time.monotonic()))
            timer.start(10)
            try:
                with patch('viewer.app.cache_path', return_value=root/'cache.sqlite3'):
                    w.open_source(root/'backup')
                    deadline = time.monotonic() + 25
                    while w.worker_thread is not None and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(.003)
                self.assertIsNone(w.worker_thread)
                self.assertEqual(w.catalogue.count(), 1200)
                self.assertLessEqual(w.listing.count(), 200)
                self.assertGreater(len(ticks), 5)
                self.assertLess(max(b-a for a,b in zip(ticks, ticks[1:])), 2.0)
                w.listing.setCurrentRow(5)
                ident = w.shown_message
                w.import_incomplete = True
                for count in (300, 400, 800):
                    w.import_batch(count)
                self.assertEqual(w.shown_message, ident)
                self.assertEqual(w.folders.item(0).text(), 'All mail (1200)')
            finally:
                w.cancel_import()
                while w.worker_thread is not None:
                    self.app.processEvents()
                    time.sleep(.005)
                timer.stop()
                w.close()
                w.deleteLater()
                QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_large_html_falls_back_and_old_documents_are_released(self):
        html, simplified = preview_html('<table><tr><td>' * 20 + 'Content' + '</td></tr></table>' * 20)
        self.assertTrue(simplified)
        self.assertIn('Simplified preview', html)
        preview = SafeHtmlPreview()
        message = EmailMessage()
        for i in range(20):
            preview.display(f'<b>Message {i}</b>', message)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(len(preview.findChildren(QTextDocument)), 1)
        preview.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
