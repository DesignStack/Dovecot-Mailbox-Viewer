"""Deleting derived indexes must never delete source mail or unrelated files."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtWidgets import QApplication, QMessageBox
from viewer.cache_manager import cache_entries, delete_cache, CacheDialog
from viewer.catalog import Catalogue


class CacheTests(unittest.TestCase):
    def test_listing_deletion_and_path_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / ('a'*20 + '.sqlite3')
            db = Catalogue(cache)
            db.finish('test', account='test@example.com', source='Original backup.tgz')
            db.close()
            for name in ('settings.ini', 'viewer.log', 'backup.tgz', 'unrelated.sqlite3'):
                (root/name).write_text('keep')
            entries = cache_entries(root)
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].account, 'test@example.com')
            self.assertGreater(entries[0].size, 0)
            for path in (root/'backup.tgz', root/'unrelated.sqlite3', root.parent/cache.name):
                with self.assertRaises(ValueError):
                    delete_cache(root, path)
            delete_cache(root, cache)
            self.assertFalse(cache.exists())
            self.assertEqual((root/'backup.tgz').read_text(), 'keep')
            self.assertEqual(len(cache_entries(root)), 0)

    def test_current_cache_is_closed_before_removal_and_cancel_keeps_files(self):
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = root / ('b'*20 + '.sqlite3')
            db = Catalogue(cache)
            db.finish('test')
            closed = []
            def close():
                db.close()
                closed.append(True)
            dialog = CacheDialog(root, cache, close)
            dialog.tree.topLevelItem(0).setSelected(True)
            with patch('viewer.cache_manager.QMessageBox.question', return_value=QMessageBox.StandardButton.No):
                dialog.remove_selected()
            self.assertTrue(cache.exists())
            self.assertFalse(closed)
            with patch('viewer.cache_manager.QMessageBox.question', return_value=QMessageBox.StandardButton.Yes):
                dialog.remove_selected()
            self.assertTrue(closed)
            self.assertFalse(cache.exists())
            dialog.close()
