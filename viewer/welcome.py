"""Plain-language onboarding shared by the welcome dialog and empty mailbox view."""
from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QScrollArea,
    QSizePolicy, QVBoxLayout, QWidget,
)

from viewer.icons import line_icon
from viewer.recent import dropped_backup
from pathlib import Path


class BackupChoices(QWidget):
    archive_requested = Signal()
    folder_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Reserve enough height for wrapped copy before Qt polishes the font.
        # This also keeps the two choices aligned on the empty screen.
        self.setFixedHeight(284)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(16)
        self.archive_button = self._card(
            row, 'archive', 'A backup file', '.tar.gz or .tgz',
            'Choose the backup file you downloaded from JetBackup or cPanel. '
            'You can open it without extracting it first.', 'Open Archive', True,
        )
        self.folder_button = self._card(
            row, 'folder', 'An extracted folder', 'Already unpacked your backup?',
            'Choose the extracted backup folder, such as backup/email. '
            'The app will look inside it for your mailbox.', 'Open Folder', False,
        )
        self.archive_button.clicked.connect(self.archive_requested)
        self.folder_button.clicked.connect(self.folder_requested)

    def _card(self, row, icon_name, title, subtitle, description, button_text, primary):
        card = QFrame()
        card.setObjectName('choiceCard')
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(10)
        icon = QLabel()
        icon.setPixmap(line_icon(icon_name, '#357ddb').pixmap(30, 30))
        layout.addWidget(icon)
        heading = QLabel(title)
        heading.setObjectName('choiceTitle')
        layout.addWidget(heading)
        badge = QLabel(subtitle)
        badge.setObjectName('choiceSubtitle')
        badge.setWordWrap(True)
        badge.setFixedHeight(28)
        badge.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(badge)
        detail = QLabel(description)
        detail.setObjectName('choiceDescription')
        detail.setWordWrap(True)
        detail.setMinimumWidth(0)
        detail.setFixedHeight(76)
        detail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(detail, 1)
        button = QPushButton(button_text)
        button.setObjectName('primaryChoice' if primary else 'secondaryChoice')
        button.setIcon(line_icon(icon_name, '#ffffff' if primary else '#357ddb'))
        button.setIconSize(QSize(18, 18))
        button.setMinimumHeight(40)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setAccessibleDescription(description)
        button.setAutoDefault(False)
        layout.addWidget(button)
        row.addWidget(card, 1)
        return button


WELCOME_STYLE = """
    QWidget#welcomePage, QDialog#welcomeDialog { background: #f4f6fa; }
    QLabel { background: transparent; color: #303b4a; font-family: 'Segoe UI'; font-size: 13px; }
    QLabel#welcomeEyebrow { color: #5276a8; font-size: 11px; font-weight: 600; }
    QLabel#welcomeTitle { font-size: 25px; font-weight: 600; color: #263449; }
    QLabel#welcomeIntro { color: #788392; font-size: 14px; }
    QFrame#choiceCard { background: white; border: 1px solid #dce4ef; border-radius: 10px; }
    QLabel#choiceTitle { font-size: 16px; font-weight: 600; }
    QLabel#choiceSubtitle { color: #5276a8; font-size: 12px; }
    QLabel#choiceDescription { color: #707d8c; }
    QPushButton { font-family: 'Segoe UI'; font-size: 13px; border-radius: 6px; padding: 8px 14px; }
    QPushButton#primaryChoice { background: #357ddb; color: white; border: 1px solid #357ddb; }
    QPushButton#primaryChoice:hover { background: #296bbe; }
    QPushButton#secondaryChoice { background: #eef4fc; color: #2d619d; border: 1px solid #d6e3f3; }
    QPushButton#secondaryChoice:hover { background: #e0ecfa; }
    QPushButton:focus { border: 2px solid #244e81; }
    QPushButton#dismissWelcome { background: transparent; color: #657286; border: 1px solid #d9e1eb; }
    QFrame#nextSteps { background: #eaf0f8; border: 0; border-radius: 8px; }
    QLabel#stepNumber { background: #ffffff; color: #5276a8; border-radius: 11px; font-weight: 600; }
    QLabel#welcomeNote { color: #788392; font-size: 12px; }
    QScrollArea { border: 0; background: transparent; }
"""


class WelcomePage(QWidget):
    archive_requested = Signal()
    folder_requested = Signal()
    recent_requested = Signal(str)

    def __init__(self, parent=None, dialog=False):
        super().__init__(parent)
        self.setObjectName('welcomePage')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(WELCOME_STYLE)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(18)
        content = QWidget()
        content.setMaximumWidth(760)
        content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        body = QVBoxLayout(content)
        body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)
        top = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(line_icon('mail', '#357ddb').pixmap(42, 42))
        top.addWidget(icon)
        top.addSpacing(8)
        wording = QVBoxLayout()
        wording.setSpacing(4)
        eyebrow = QLabel('DOVECOT MAILBOX VIEWER')
        eyebrow.setObjectName('welcomeEyebrow')
        wording.addWidget(eyebrow)
        title = QLabel('Welcome. Let’s open your mail.' if dialog else 'Your emails are a backup away.')
        title.setObjectName('welcomeTitle')
        title.setWordWrap(True)
        wording.addWidget(title)
        top.addLayout(wording, 1)
        body.addLayout(top)
        intro = QLabel('Read and search a saved mailbox backup on your computer. '
                       'Choose what you have, or drag a backup file or folder into this window.')
        intro.setObjectName('welcomeIntro')
        intro.setWordWrap(True)
        body.addWidget(intro)
        self.choices = BackupChoices()
        self.choices.archive_requested.connect(self.archive_requested)
        self.choices.folder_requested.connect(self.folder_requested)
        body.addWidget(self.choices)
        self.recent_box = QWidget()
        self.recent_layout = QVBoxLayout(self.recent_box)
        self.recent_layout.setContentsMargins(0, 0, 0, 0)
        self.recent_box.hide()
        body.addWidget(self.recent_box)
        steps = QFrame()
        steps.setObjectName('nextSteps')
        steps_layout = QVBoxLayout(steps)
        steps_layout.setContentsMargins(16, 14, 16, 14)
        steps_layout.setSpacing(10)
        heading = QLabel('What happens next')
        heading.setStyleSheet('font-weight: 600;')
        steps_layout.addWidget(heading)
        for number, text in (
            ('1', 'We find your mailbox and prepare it for browsing.'),
            ('2', 'Emails appear as they are read. Larger backups take longer.'),
            ('3', 'Choose a folder, read your emails, search, and save attachments.'),
        ):
            step = QHBoxLayout()
            step.setSpacing(10)
            badge = QLabel(number)
            badge.setObjectName('stepNumber')
            badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            step.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            explanation = QLabel(text)
            explanation.setWordWrap(True)
            step.addWidget(explanation, 1)
            steps_layout.addLayout(step)
        body.addWidget(steps)
        reassurance = QLabel('Your original backup stays unchanged. The app makes a separate local copy '
                             'for browsing and searching. Reopening the same backup is quicker.')
        reassurance.setObjectName('welcomeNote')
        reassurance.setWordWrap(True)
        body.addWidget(reassurance)
        outer.addWidget(content, 0, Qt.AlignmentFlag.AlignHCenter)

    def set_recent(self, entries):
        while self.recent_layout.count():
            item = self.recent_layout.takeAt(0)
            item.widget().deleteLater()
        if entries:
            self.recent_layout.addWidget(QLabel('Recently opened'))
        for entry in entries[:3]:
            label = f"{Path(entry['path']).name} — {entry['account']}".replace('&', '&&')
            button = QPushButton(label)
            button.setText(QFontMetrics(button.font()).elidedText(label, Qt.TextElideMode.ElideMiddle, 490))
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.setIcon(line_icon('folder' if Path(entry['path']).is_dir() else 'archive'))
            button.setToolTip(entry['path'])
            button.setAutoDefault(False)
            button.clicked.connect(lambda checked=False, path=entry['path']: self.recent_requested.emit(path))
            self.recent_layout.addWidget(button)
        self.recent_box.setVisible(bool(entries))


class WelcomeDialog(QDialog):
    """One guided choice, then the familiar Windows file or folder picker."""
    archive_requested = Signal()
    folder_requested = Signal()
    recent_requested = Signal(str)
    backup_dropped = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName('welcomeDialog')
        self.setWindowTitle('Welcome to Dovecot Mailbox Viewer')
        self.setWindowIcon(line_icon('mail', '#357ddb'))
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.setStyleSheet(WELCOME_STYLE)
        self.setMinimumSize(540, 400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.page = WelcomePage(dialog=True)
        scroll.setWidget(self.page)
        layout.addWidget(scroll, 1)
        self.page.archive_requested.connect(self.archive_requested)
        self.page.folder_requested.connect(self.folder_requested)
        self.page.recent_requested.connect(self.recent_requested)
        footer = QHBoxLayout()
        footer.setContentsMargins(28, 0, 28, 18)
        footer.addStretch()
        later = QPushButton('Not now')
        later.setObjectName('dismissWelcome')
        later.setAutoDefault(False)
        later.clicked.connect(self.reject)
        footer.addWidget(later)
        layout.addLayout(footer)
        available = self.screen().availableGeometry()
        self.resize(min(740, available.width() - 60), min(690, available.height() - 70))
        self.page.choices.archive_button.setFocus()

    def dragEnterEvent(self, event):
        if dropped_backup(event.mimeData()) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = dropped_backup(event.mimeData())
        if path is not None:
            event.acceptProposedAction()
            self.backup_dropped.emit(str(path))
