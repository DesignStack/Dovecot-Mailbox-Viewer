"""Reading controls operate on the displayed reply, not just its list row."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit
from viewer.app import Window
from viewer.catalog import Catalogue
from viewer.reading import original_headers
from viewer.preferences import read_preferences, write_preferences
from viewer.attachments import Attachment
from test_browsing import mail


class ReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.Format.IniFormat)
        self.w = Window(show_welcome=False, settings=self.settings)
        self.w.catalogue = Catalogue(self.root / 'catalogue.sqlite3')
        for i in range(3):
            self.w.catalogue.add(mail(f'Plan {i}', f'Fri, 25 Sep 2026 {10+i}:00:00 +0000',
                                     ident=f'{i}@example.com', refs='<0@example.com>' if i else '',
                                     folder='Sent' if i == 1 else 'INBOX'))
        self.w.catalogue.commit()
        self.w.populate_folders(3)
        self.w.show()
        self.app.processEvents()

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_conversation_navigation_export_print_and_headers_follow_displayed_reply(self):
        w = self.w
        w.conversations_action.trigger()
        self.assertEqual(w.listing.count(), 1)
        self.assertEqual(w.conversation_picker.count(), 3)
        w.pick_conversation_message(0)
        self.assertEqual(w.shown_message, 1)
        self.assertEqual(w.heading.text(), 'Plan 0')
        self.assertIn('Plan 0', w.print_document().toPlainText())
        out = self.root / 'reply.eml'
        with patch('viewer.app.QFileDialog.getSaveFileName', return_value=(str(out), '')):
            w.export_eml()
        self.assertEqual(out.read_bytes(), w.catalogue.message(1)['raw'])
        captured = []
        def inspect(dialog):
            captured.append(dialog.findChild(QPlainTextEdit).toPlainText())
            return QDialog.DialogCode.Accepted
        with patch('viewer.reading.QDialog.exec', inspect):
            w.view_headers()
        self.assertIn('Subject: Plan 0', captured[0])
        self.assertNotIn('The launch plan', captured[0])
        w.refresh_messages()
        self.assertEqual(w.shown_message, 1)
        w.conversations_action.trigger()
        self.assertEqual(w.listing.count(), 3)
        self.assertTrue(w.conversation_row.isHidden())

    def test_search_find_zoom_plain_text_and_no_automatic_image_consent(self):
        w = self.w
        w.search.setText('launch')
        self.assertTrue(w.preview.extraSelections())
        selections = w.preview.extraSelections()
        self.assertEqual(selections[0].cursor.selectedText().lower(), 'launch')
        w.show_find()
        w.find_text.setText('Search')
        self.assertIn('1 matches', w.find_count.text())
        self.assertEqual(w.preview.textCursor().selectedText(), 'Search')
        w.find_next()
        self.assertEqual(w.preview.textCursor().selectedText(), 'Search')
        w.html_button.setChecked(False)
        self.assertIn('launch plan', w.preview.toPlainText())
        self.assertEqual(w.preview.remote_images, {})
        w.change_zoom(30)
        self.assertEqual(w.zoom_button.toolTip(), 'Zoom · 130%')
        self.assertEqual(read_preferences(self.settings)['zoom'], 130)
        w.listing.setCurrentRow(1)
        self.assertEqual(w.zoom_button.toolTip(), 'Zoom · 130%')
        w.close_find()
        self.assertTrue(w.preview.extraSelections())
        w.search.clear()
        self.assertFalse(w.preview.extraSelections())

    def test_settings_and_layout_persist_and_recent_history_can_be_disabled(self):
        w = self.w
        w.preferences.update(remember_recent=False, page_size=100, sort='subject_asc')
        write_preferences(w.settings, w.preferences)
        w.resize(920, 640)
        w.panes.setSizes([180, 300, 440])
        w.close()
        other = Window(show_welcome=False, settings=self.settings)
        other.content_stack.setCurrentWidget(other.panes)
        other.show()
        self.app.processEvents()
        try:
            self.assertEqual(other.preferences['page_size'], 100)
            self.assertFalse(other.preferences['remember_recent'])
            self.assertEqual(other.preferences['sort'], 'subject_asc')
            self.assertEqual(other.size(), w.size())
            self.assertEqual(other.panes.sizes(), w.panes.sizes())
        finally:
            other.close()
            other.deleteLater()

    def test_save_all_attachments_preserves_duplicate_names_and_never_writes_into_backup(self):
        w = self.w
        # Use the actual collector to retain its attachment metadata contract.
        from email.message import EmailMessage
        from viewer.attachments import collect_attachments
        msg = EmailMessage()
        msg.set_content('Test')
        for data in (b'first', b'second'):
            msg.add_attachment(data, maintype='application', subtype='octet-stream', filename='../same.txt')
        w.attachments = collect_attachments(msg)
        with patch('viewer.reading.QFileDialog.getExistingDirectory', return_value=str(self.root)), \
                patch('viewer.reading.QMessageBox.information'):
            w.save_all_attachments()
            w.save_all_attachments()
        folders = list(self.root.glob('Attachments-*'))
        self.assertEqual(len(folders), 2)
        for folder in folders:
            self.assertEqual({p.read_bytes() for p in folder.iterdir()}, {b'first', b'second'})
        w.source = self.root
        with patch('viewer.reading.QMessageBox.information'):
            self.assertFalse(w.allowed_destination(self.root / 'source.eml'))
        self.assertFalse((self.root / 'source.eml').exists())

    def test_original_header_folding_and_duplicates_are_preserved(self):
        raw = b'Received: first\r\nReceived: second\r\nX-Long: one\r\n\ttwo\r\n\r\nPrivate body'
        self.assertEqual(original_headers(raw), 'Received: first\r\nReceived: second\r\nX-Long: one\r\n\ttwo')
