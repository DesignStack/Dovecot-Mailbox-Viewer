"""Bulk exports must be complete, read-only and safe to cancel or repeat."""
from pathlib import Path
import tempfile
import unittest

from viewer.catalog import Catalogue
from viewer.exporting import export_messages


class BulkExportTests(unittest.TestCase):
    def database(self, root, rows):
        path = root / 'messages.sqlite3'
        catalogue = Catalogue(path)
        catalogue.conn.executemany('INSERT INTO messages(folder,subject,raw,source) VALUES (?,?,?,?)', rows)
        catalogue.commit()
        catalogue.close()
        return path

    def test_folder_export_exceeds_display_limit_and_preserves_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            raw = b'Subject: Same subject\r\n\r\nOriginal bytes\x00\r\n'
            database = self.database(root, [('INBOX', 'CON: /same?', raw, 'synthetic')] * 5001
                                     + [('Sent', 'Ignore this', b'other folder', 'synthetic')])
            unchanged = database.read_bytes()
            (root / 'keep.eml').write_bytes(b'existing file')
            output, count, cancelled = export_messages(database, root, folder='INBOX')
            self.assertEqual(count, 5001)
            self.assertFalse(cancelled)
            files = list(output.rglob('*.eml'))
            self.assertEqual(len(files), 5001)
            self.assertTrue(all(file.read_bytes() == raw for file in files))
            self.assertEqual(database.read_bytes(), unchanged)
            self.assertEqual((root / 'keep.eml').read_bytes(), b'existing file')

    def test_selection_folder_collisions_and_repeated_exports_are_safe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [(folder, '../NUL', f'email {i}'.encode(), 'synthetic')
                    for i, folder in enumerate(('a/b', 'a\\b', 'A_B', '../../escape', 'Sent'))]
            database = self.database(root, rows)
            output, count, _ = export_messages(database, root, ids=[1, 2, 3, 4, 1])
            self.assertEqual(count, 4)
            files = list(output.rglob('*.eml'))
            self.assertEqual(len({f.parent.name.casefold() for f in files}), 4)
            self.assertEqual({f.read_bytes() for f in files}, {f'email {i}'.encode() for i in range(4)})
            self.assertTrue(all(f.resolve().is_relative_to(output.resolve()) for f in files))
            again, count, _ = export_messages(database, root, ids=[1])
            self.assertNotEqual(again, output)
            self.assertEqual(len(list(output.rglob('*.eml'))), 4)

    def test_cancel_keeps_only_completed_emails(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = self.database(root, [('INBOX', 'Mail', b'raw email', 'synthetic')] * 60)
            progress = []
            output, count, cancelled = export_messages(database, root,
                progress=lambda done, total: progress.append(done), cancelled=lambda: progress[-1] >= 25)
            self.assertTrue(cancelled)
            self.assertEqual(count, 25)
            self.assertEqual(len(list(output.rglob('*.eml'))), 25)
