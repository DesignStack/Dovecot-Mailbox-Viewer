"""Icon menus, read-only unread filtering, grouped selection and pagination."""
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import QDate, QSettings, Qt
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem
from viewer.app import Window
from viewer.catalog import Catalogue
from viewer.dovecot_index import SEEN, Status
from viewer.mail_widgets import DETAILS_ROLE
from viewer.preferences import read_preferences
from test_browsing import mail


class ListControlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root/'settings.ini'), QSettings.Format.IniFormat)
        self.w = Window(show_welcome=False, settings=self.settings)
        self.w.catalogue = Catalogue(self.root/'index.sqlite3')
        now = datetime.now().astimezone().replace(hour=12, minute=0, second=0)
        for i, (days, flags) in enumerate(((0, 0), (0, SEEN), (1, None), (8, 0), (15, SEEN), (40, 0))):
            sent = (now-timedelta(days=days)).strftime('%a, %d %b %Y %H:%M:%S %z')
            record = mail(f'Project {i}', sent, ident=f'{i}@x', refs='<0@x>' if i == 1 else '')
            self.w.catalogue.add(record, Status(i+1, flags) if flags is not None else None)
        self.w.catalogue.commit()
        self.w.populate_folders(6)
        self.w.show()
        self.app.processEvents()

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def rows(self):
        return [self.w.listing.item(i) for i in range(self.w.listing.count())]

    def test_unread_tab_combines_with_search_and_does_not_change_flags(self):
        w = self.w
        before = [tuple(r) for r in w.catalogue.conn.execute('SELECT id,status,raw FROM messages')]
        self.assertTrue(w.list_header.all_button.isChecked())
        w.list_header.unread_button.click()
        self.assertTrue(w.unread_only)
        self.assertEqual(w.listing.count(), 3)
        self.assertTrue(all(item.data(DETAILS_ROLE)['unread'] for item in self.rows()))
        w.search.setText('Project 3')
        self.assertEqual(w.listing.count(), 1)
        w.search.setText('Project 1')
        self.assertEqual(w.listing.count(), 0)
        self.assertEqual(w.heading.text(), 'No unread emails in this view')
        w.list_header.all_button.click()
        self.assertEqual(w.listing.count(), 1)
        w.search.clear()
        self.assertEqual(w.listing.count(), 6)
        self.assertEqual(before, [tuple(r) for r in w.catalogue.conn.execute('SELECT id,status,raw FROM messages')])

    def test_group_headers_never_become_selected_messages_or_export_ids(self):
        w = self.w
        groups = [item.data(DETAILS_ROLE)['group_header'] for item in self.rows()]
        self.assertEqual(groups[0], 'Today')
        self.assertEqual(groups[1], '')
        self.assertIn('Yesterday', groups)
        self.assertEqual(len([g for g in groups if g]), 5)
        w.listing.selectAll()
        with patch.object(w, 'start_export') as export:
            w.export_selected()
        self.assertEqual(set(export.call_args.kwargs['ids']), set(range(1, 7)))
        self.assertEqual(len(w.listing.selectedItems()), 6)
        w.preferences['page_size'] = 1  # Exercise a group split across page boundaries.
        w.refresh_messages()
        w.change_page(1)
        self.assertEqual(w.listing.count(), 1)
        self.assertEqual(w.listing.item(0).data(DETAILS_ROLE)['group_header'], 'Today')
        self.assertEqual(w.page_label.text(), '2–2 of 6')
        w.change_page(1)
        self.assertEqual(w.listing.item(0).data(DETAILS_ROLE)['group_header'], 'Yesterday')
        w._group_day = QDate.currentDate().addDays(-1)
        with patch.object(w, 'refresh_messages') as refresh:
            w.refresh_date_groups()
            refresh.assert_called_once()

    def test_icon_modes_preserve_selection_and_sort_actions_persist(self):
        w = self.w
        w.listing.setCurrentRow(2)
        ident = w.shown_message
        w.listing.item(3).setSelected(True)
        selected = {i.data(Qt.ItemDataRole.UserRole) for i in w.listing.selectedItems()}
        option = QStyleOptionViewItem()
        index = w.listing.model().index(0, 0)
        preview_height = w.listing.itemDelegate().sizeHint(option, index).height()
        w.list_header.mode_actions['compact'].trigger()
        self.assertEqual(w.preferences['list_mode'], 'compact')
        self.assertEqual(read_preferences(self.settings)['list_mode'], 'compact')
        self.assertEqual(w.shown_message, ident)
        self.assertEqual({i.data(Qt.ItemDataRole.UserRole) for i in w.listing.selectedItems()}, selected)
        self.assertLess(w.listing.itemDelegate().sizeHint(option, index).height(), preview_height)
        w.list_header.sort_actions['subject_desc'].trigger()
        self.assertTrue(w.list_header.sort_actions['subject_desc'].isChecked())
        self.assertEqual(read_preferences(self.settings)['sort'], 'subject_desc')
        self.assertEqual([item.data(DETAILS_ROLE)['subject'] for item in self.rows()], [f'Project {i}' for i in range(5, -1, -1)])
        self.assertFalse(any(item.data(DETAILS_ROLE)['group_header'] for item in self.rows()))
        w.list_header.sort_actions['date_asc'].trigger()
        self.assertEqual(self.rows()[-1].data(Qt.ItemDataRole.UserRole), 1)
        self.assertTrue(any(item.data(DETAILS_ROLE)['group_header'] for item in self.rows()))

    def test_unread_conversations_include_only_matching_rows_but_keep_context(self):
        w = self.w
        w.conversations_action.trigger()
        w.list_header.unread_button.click()
        self.assertEqual(w.listing.count(), 3)
        w.search.setText('Project 0')
        self.assertEqual(w.listing.count(), 1)
        self.assertEqual(w.conversation_picker.count(), 2)
        self.assertTrue(w.listing.item(0).data(DETAILS_ROLE)['unread'])

    def test_zoom_menu_and_html_icon_replace_controls_without_network_requests(self):
        w = self.w
        self.assertTrue(w.html_button.isChecked())
        self.assertFalse(read_preferences(self.settings)['plain_text'])
        self.assertIs(w.zoom_button.menu(), w.zoom_menu)
        with patch.object(w.preview.network, 'get') as download:
            w.zoom_actions[150].trigger()
            self.assertEqual(w.preferences['zoom'], 150)
            self.assertTrue(w.zoom_actions[150].isChecked())
            self.assertIn('150%', w.zoom_button.toolTip())
            with patch('viewer.reading.QInputDialog.getInt', return_value=(130, True)):
                w.custom_zoom_action.trigger()
            self.assertEqual(w.preferences['zoom'], 130)
            self.assertFalse(any(a.isChecked() for a in w.zoom_actions.values()))
            self.assertIn('130%', w.custom_zoom_action.text())
            with patch('viewer.reading.QInputDialog.getInt', return_value=(170, False)):
                w.custom_zoom()
            self.assertEqual(w.preferences['zoom'], 130)
            w.html_button.click()
            self.assertTrue(read_preferences(self.settings)['plain_text'])
            self.assertIn('<pre', w.preview.html_content)
            w.html_button.click()
            self.assertFalse(read_preferences(self.settings)['plain_text'])
            self.assertIn('<b>launch plan</b>', w.preview.html_content)
            download.assert_not_called()
