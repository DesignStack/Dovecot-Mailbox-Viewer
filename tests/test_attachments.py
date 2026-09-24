"""Attachment cards must preserve identity and bytes, including duplicate names."""
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication

from viewer.app import Window
from viewer.attachments import collect_attachments, suggested_filename
from viewer.catalog import Catalogue
from viewer.mdbox import Record


class AttachmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Keep the application alive across GUI test classes and network threads.
        cls.app = QApplication.instance() or QApplication([])

    def test_cards_and_menu_save_correct_duplicate_filename_and_reset(self):
        app = QApplication.instance() or QApplication([])
        message = EmailMessage()
        message['Subject'] = 'Files for review'
        message['Date'] = 'Thu, 24 Sep 2026 11:30:00 +0100'
        message.set_content('Please see the attached files.')
        message.add_attachment(b'first pdf', maintype='application', subtype='pdf', filename='proposal.pdf')
        message.add_attachment(b'second pdf', maintype='application', subtype='pdf', filename='proposal.pdf')
        message.add_attachment(b'spreadsheet', maintype='application', subtype='octet-stream', filename='Budget.XLSX')
        with tempfile.TemporaryDirectory() as temp:
            window = Window()
            window.catalogue = Catalogue(Path(temp) / 'test.sqlite3')
            window.catalogue.reset()
            window.catalogue.add(Record('INBOX', message.as_bytes(), 'synthetic'))
            window.catalogue.commit()
            window.populate_folders(1)
            window.show()
            app.processEvents()
            cards = window.attachment_cards
            self.assertEqual(cards.count(), 3)
            self.assertTrue(cards.isVisible())
            self.assertEqual(cards.item(2).data(Qt.ItemDataRole.UserRole).kind[0], 'file_sheet')
            self.assertGreaterEqual(cards.mapTo(window, cards.rect().topLeft()).y(),
                                    window.details.mapTo(window, window.details.rect().bottomLeft()).y())
            output = Path(temp) / 'saved.pdf'
            with patch('viewer.app.QFileDialog.getSaveFileName', return_value=(str(output), '')):
                cards.itemClicked.emit(cards.item(1))
            self.assertEqual(output.read_bytes(), b'second pdf')
            with patch('viewer.app.QInputDialog.getItem', return_value=('1. proposal.pdf', True)), \
                 patch('viewer.app.QFileDialog.getSaveFileName', return_value=(str(output), '')):
                window.attachment_action.trigger()
            self.assertEqual(output.read_bytes(), b'first pdf')
            # Many cards wrap/scroll rather than pushing the email body away.
            cards.set_attachments(window.attachments * 10)
            window.resize(980, 680)
            app.processEvents()
            self.assertLessEqual(cards.height(), 200)
            window.reset_preview()
            self.assertEqual(cards.count(), 0)
            self.assertFalse(cards.isVisible())
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_attached_email_stays_one_file(self):
        inner = EmailMessage()
        inner['Subject'] = 'Original email'
        inner.set_content('Original body')
        inner.add_attachment(b'nested attachment', maintype='application', subtype='pdf', filename='inside.pdf')
        outer = EmailMessage()
        outer.set_content('An email is attached.')
        outer.add_attachment(inner, filename='original.eml')
        attachments = collect_attachments(outer)
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].kind, ('mail', 'Email'))
        recovered = BytesParser().parsebytes(attachments[0].data)
        self.assertEqual(recovered['Subject'], 'Original email')

    def test_suggested_name_removes_paths_and_windows_devices(self):
        self.assertEqual(suggested_filename(r'..\..\report.pdf'), 'report.pdf')
        self.assertEqual(suggested_filename('/tmp/report.pdf'), 'report.pdf')
        self.assertEqual(suggested_filename('CON.txt'), '_CON.txt')
        self.assertEqual(suggested_filename(''), 'attachment')
