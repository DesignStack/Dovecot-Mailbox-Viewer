"""Underlined mail tabs and keyboard-accessible icon menus for the message list."""
from PySide6.QtCore import Signal, QSize
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QButtonGroup, QToolButton, QMenu
from viewer.catalog import SORTS
from viewer.icons import line_icon


class MessageListHeader(QFrame):
    unread_changed = Signal(bool)
    sort_changed = Signal(str)
    mode_changed = Signal(str)

    def __init__(self, preferences, parent=None):
        super().__init__(parent)
        self.setObjectName('messageHeader')
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(6)
        self.tabs = QButtonGroup(self)
        self.tabs.setExclusive(True)
        self.unread_button = QPushButton('Unread')
        self.all_button = QPushButton('All')
        for button in (self.unread_button, self.all_button):
            button.setObjectName('mailTab')
            button.setCheckable(True)
            button.setFixedHeight(46)
            self.tabs.addButton(button)
            layout.addWidget(button)
        self.all_button.setChecked(True)
        self.unread_button.setToolTip('Show emails marked unread in this backup')
        self.all_button.setToolTip('Show all emails in the current folder and search')
        self.unread_button.clicked.connect(lambda: self.unread_changed.emit(True))
        self.all_button.clicked.connect(lambda: self.unread_changed.emit(False))
        layout.addStretch(1)
        self.mode_button, self.mode_menu = self._menu_button('List layout', 'list_preview')
        self.mode_group = QActionGroup(self)
        self.mode_actions = {}
        for key, label, icon in (('preview', 'Preview', 'list_preview'), ('compact', 'Compact', 'list_compact')):
            action = self.mode_menu.addAction(line_icon(icon), label)
            action.setCheckable(True)
            self.mode_group.addAction(action)
            action.triggered.connect(lambda checked=False, mode=key: self.mode_changed.emit(mode))
            self.mode_actions[key] = action
        layout.addWidget(self.mode_button)
        self.sort_button, self.sort_menu = self._menu_button('Sort emails', 'sort')
        self.sort_group = QActionGroup(self)
        self.sort_actions = {}
        for key, (label, _) in SORTS.items():
            if key in ('sender_asc', 'subject_asc'):
                self.sort_menu.addSeparator()
            action = self.sort_menu.addAction(label)
            action.setCheckable(True)
            self.sort_group.addAction(action)
            action.triggered.connect(lambda checked=False, order=key: self.sort_changed.emit(order))
            self.sort_actions[key] = action
        layout.addWidget(self.sort_button)
        self.sync(preferences)

    def _menu_button(self, label, icon):
        button = QToolButton()
        button.setObjectName('mailTool')
        button.setIcon(line_icon(icon))
        button.setIconSize(QSize(19, 19))
        button.setFixedSize(32, 34)
        button.setAccessibleName(label)
        menu = QMenu(button)
        button.setMenu(menu)
        button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        return button, menu

    def sync(self, preferences):
        """Update checkmarks without firing user-change signals."""
        sort, mode = preferences['sort'], preferences['list_mode']
        self.sort_actions[sort].setChecked(True)
        self.mode_actions[mode].setChecked(True)
        self.sort_button.setToolTip(f"Sort emails · {SORTS[sort][0]}")
        self.mode_button.setToolTip(f'List layout · {mode.title()}')
        self.mode_button.setIcon(line_icon('list_' + mode))
