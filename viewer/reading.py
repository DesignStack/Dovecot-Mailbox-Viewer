"""Reading controls kept separate from mailbox loading and list navigation."""
import re
import tempfile
from pathlib import Path
from html import escape
from PySide6.QtGui import QAction, QColor, QTextCursor, QTextDocument, QFont, QTextCharFormat
from PySide6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QWidget, QPlainTextEdit, QPushButton, QTextEdit, QFileDialog, QMessageBox)
from viewer.catalog import html_to_text
from viewer.attachments import suggested_filename
from viewer.preferences import SettingsDialog, write_preferences
from viewer.cache_manager import CacheDialog


def original_headers(raw):
    """Preserve original order, folding and duplicate fields without MIME rewriting."""
    return re.split(br'\r?\n\r?\n', raw, maxsplit=1)[0].decode('utf-8', 'replace')


class ReadingMixin:
    def setup_reading(self, layout):
        self.conversation_row = QWidget()
        thread_layout = QHBoxLayout(self.conversation_row)
        thread_layout.setContentsMargins(0, 0, 0, 0)
        self.conversation_label = QLabel('Conversation')
        thread_layout.addWidget(self.conversation_label)
        self.conversation_picker = QComboBox()
        self.conversation_picker.setMinimumContentsLength(8)
        self.conversation_picker.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.conversation_picker.setAccessibleName('Messages in this conversation')
        self.conversation_picker.activated.connect(self.pick_conversation_message)
        thread_layout.addWidget(self.conversation_picker, 1)
        self.conversation_row.hide()
        layout.addWidget(self.conversation_row)

        row = QHBoxLayout()
        self.body_mode = QComboBox()
        self.body_mode.addItems(['HTML view', 'Plain text'])
        self.body_mode.setCurrentIndex(int(self.preferences['plain_text']))
        self.body_mode.setAccessibleName('Email display format')
        self.body_mode.currentIndexChanged.connect(self.render_body)
        row.addWidget(self.body_mode)
        self.find_button = self._tool('Find in this email (Ctrl+G)', 'search', self.show_find)
        row.addWidget(self.find_button)
        row.addStretch()
        row.addWidget(self._tool('Zoom out', 'minus', lambda: self.change_zoom(-10)))
        self.zoom_label = QLabel()
        row.addWidget(self.zoom_label)
        row.addWidget(self._tool('Zoom in', 'plus', lambda: self.change_zoom(10)))
        layout.addLayout(row)
        self.find_bar = QWidget()
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(0, 0, 0, 0)
        self.find_text = QLineEdit()
        self.find_text.setPlaceholderText('Find in this email…')
        self.find_text.textChanged.connect(self.find_changed)
        self.find_text.returnPressed.connect(self.find_next)
        find_layout.addWidget(self.find_text, 1)
        self.find_count = QLabel()
        find_layout.addWidget(self.find_count)
        find_layout.addWidget(self._tool('Previous match (Shift+F3)', 'up', lambda: self.find_next(True)))
        find_layout.addWidget(self._tool('Next match (F3)', 'down', self.find_next))
        find_layout.addWidget(self._tool('Close find', 'close', self.close_find))
        self.find_bar.hide()
        layout.addWidget(self.find_bar)
        self._zoom = 100
        self.zoom_label.setText(f"{self.preferences['zoom']}%")
        self._body = None
        for title, shortcut, handler in [('Find in email', 'Ctrl+G', self.show_find),
                ('Next match', 'F3', self.find_next), ('Previous match', 'Shift+F3', lambda: self.find_next(True))]:
            action = QAction(title, self)
            action.setShortcut(shortcut)
            action.triggered.connect(handler)
            self.addAction(action)
        self.headers_action = QAction('View original headers…', self)
        self.headers_action.triggered.connect(self.view_headers)
        self.save_all_action = QAction('Save all attachments…', self)
        self.save_all_action.triggered.connect(self.save_all_attachments)
        self.headers_action.setEnabled(False)
        self.save_all_action.setEnabled(False)
        self.message_menu.addSeparator()
        self.message_menu.addActions([self.headers_action, self.save_all_action])

    def render_body(self, _=None):
        if not self._body:
            return
        msg, plain, html = self._body
        self.preview.display(html if html and self.body_mode.currentIndex() == 0 else
            f"<pre style='white-space:pre-wrap'>{escape(plain or html_to_text(html) or '(No readable text body)')}</pre>", msg)
        self._font_runs = []
        block = self.preview.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    font = fragment.charFormat().font()
                    if font.pointSizeF() <= 0 and font.pixelSize() <= 0:
                        font = self.preview.document().defaultFont()
                    self._font_runs.append((fragment.position(), fragment.length(), font))
                iterator += 1
            block = block.next()
        self.apply_zoom()
        self.highlight_matches()

    def apply_zoom(self):
        target = self.preferences['zoom']
        # Scale each original font so headings and explicit HTML pixel sizes
        # keep their proportions. Keep original runs to avoid rounding drift.
        for position, length, original in getattr(self, '_font_runs', []):
            font = QFont(original)
            if font.pixelSize() > 0:
                font.setPixelSize(max(1, round(font.pixelSize() * target / 100)))
            else:
                font.setPointSizeF(max(1, font.pointSizeF() * target / 100))
            cursor = QTextCursor(self.preview.document())
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            format = QTextCharFormat()
            format.setFont(font)
            cursor.mergeCharFormat(format)
        self._zoom = target
        self.zoom_label.setText(f'{target}%')

    def change_zoom(self, delta):
        self.preferences['zoom'] = max(60, min(200, self.preferences['zoom'] + delta))
        self.apply_zoom()
        write_preferences(self.settings, self.preferences)

    def show_find(self):
        self.find_bar.show()
        self.find_text.setFocus()
        self.find_text.selectAll()
        self.highlight_matches()

    def close_find(self):
        self.find_bar.hide()
        self.find_text.clear()
        self.preview.setFocus()
        self.highlight_matches()

    def find_changed(self):
        self.preview.moveCursor(QTextCursor.MoveOperation.Start)
        self.highlight_matches()
        if self.find_text.text():
            self.find_next()

    def find_next(self, backwards=False):
        text = self.find_text.text()
        if not text:
            return
        flags = QTextDocument.FindFlag.FindBackward if backwards else QTextDocument.FindFlag(0)
        if not self.preview.find(text, flags):
            self.preview.moveCursor(QTextCursor.MoveOperation.End if backwards else QTextCursor.MoveOperation.Start)
            self.preview.find(text, flags)

    def highlight_matches(self):
        if not hasattr(self, 'preview'):
            return
        terms = []
        if self.preferences['highlight']:
            terms = re.findall(r'\w+', ' '.join([self.search.text(),
                self.search_options.get('sender', ''), self.search_options.get('subject', '')]))
        find = self.find_text.text() if not self.find_bar.isHidden() else ''
        if find:
            terms.append(find)
        selections = []
        find_count = 0
        for term in dict.fromkeys(terms):
            cursor = QTextCursor(self.preview.document())
            while len(selections) < 1000:
                cursor = self.preview.document().find(term, cursor)
                if cursor.isNull():
                    break
                selection = QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format.setBackground(QColor('#ffdf89' if term == find else '#fff0b0'))
                selection.format.setForeground(QColor('#292d33'))
                selections.append(selection)
                if term == find:
                    find_count += 1
        self.preview.setExtraSelections(selections)
        self.find_count.setText(f'{find_count}{"+" if len(selections) == 1000 else ""} matches' if find else '')

    def populate_conversation(self, ident):
        rows = self.catalogue.conversation(ident) if self.preferences['conversations'] else []
        self.conversation_picker.blockSignals(True)
        self.conversation_picker.clear()
        for i, row in enumerate(rows):
            self.conversation_picker.addItem(f"{i+1}. {row['sender']} · {row['date']} · {row['folder']}", row['id'])
        self.conversation_picker.setCurrentIndex(max(0, self.conversation_picker.findData(ident)))
        self.conversation_picker.blockSignals(False)
        self.conversation_label.setText(f'Conversation ({len(rows)})')
        self.conversation_row.setVisible(len(rows) > 1)

    def pick_conversation_message(self, index):
        ident = self.conversation_picker.itemData(index)
        if ident is not None:
            self.display_message(ident)

    def view_headers(self):
        if self.shown_message is None or self.catalogue is None:
            return
        row = self.catalogue.message(self.shown_message)
        if row is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle('Original message headers')
        dialog.resize(760, 500)
        layout = QVBoxLayout(dialog)
        editor = QPlainTextEdit(original_headers(row['raw']))
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        editor.setStyleSheet('font-family: Consolas, monospace; font-size: 12px;')
        layout.addWidget(editor)
        buttons = QHBoxLayout()
        copy = QPushButton('Copy all')
        copy.clicked.connect(lambda: QApplication.clipboard().setText(editor.toPlainText()))
        buttons.addWidget(copy)
        buttons.addStretch()
        close = QPushButton('Close')
        close.clicked.connect(dialog.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        dialog.exec()

    def save_all_attachments(self):
        if not self.attachments:
            return
        destination = QFileDialog.getExistingDirectory(self, 'Choose where to save all attachments')
        if not destination or not self.allowed_destination(destination):
            return
        try:
            folder = Path(tempfile.mkdtemp(prefix='Attachments-', dir=destination))
            for i, attachment in enumerate(self.attachments, 1):
                name = f'{i:03d}-' + suggested_filename(attachment.filename)
                (folder / name).write_bytes(attachment.data)
            self.statusBar().showMessage(f'Saved {len(self.attachments)} attachments to {folder}')
            # Show a concise success message, leaving folder opening to the user.
            QMessageBox.information(self, 'Attachments saved', f'Saved {len(self.attachments)} attachments to:\n{folder}')
        except OSError as exc:
            QMessageBox.warning(self, 'Cannot save attachments', str(exc))

    def allowed_destination(self, destination):
        if self.source is not None and (Path(destination).resolve() == self.source.resolve() or
                self.source.is_dir() and Path(destination).resolve().is_relative_to(self.source.resolve())):
            QMessageBox.information(self, 'Choose another location', 'Save outside the original backup to keep it unchanged.')
            return False
        return True

    def show_settings(self):
        dialog = SettingsDialog(self.preferences, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.preferences = dialog.values()
        write_preferences(self.settings, self.preferences)
        if not self.preferences['remember_recent']:
            self.clear_recent()
        if not self.preferences['remember_layout']:
            self.settings.remove('layout')
        self.sort_picker.blockSignals(True)
        self.sort_picker.setCurrentIndex(self.sort_picker.findData(self.preferences['sort']))
        self.sort_picker.blockSignals(False)
        self.conversations_action.setChecked(self.preferences['conversations'])
        self.body_mode.setCurrentIndex(int(self.preferences['plain_text']))
        self.apply_zoom()
        self.page_offset = 0
        self.refresh_messages()
        if self.shown_message is not None:
            self.populate_conversation(self.shown_message)
        self.highlight_matches()

    def show_cache_manager(self):
        if self.worker_thread is not None or self.export_worker is not None:
            QMessageBox.information(self, 'Operation in progress', 'Finish or cancel the current operation before managing indexes.')
            return
        from viewer.catalog import cache_path
        root = cache_path(Path('preferences'), 'app').parent
        current = self.catalogue.path if self.catalogue else None
        CacheDialog(root, current, self.close_mailbox, self).exec()
        if self.catalogue is None:
            self._show_empty_page()
