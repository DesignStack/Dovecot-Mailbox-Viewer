"""User-initiated release checks. No account, mail content or file paths are sent."""
import json
import re
import sys

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QPushButton, QVBoxLayout

from viewer.version import __version__

REPOSITORY_URL = 'https://github.com/DesignStack/Dovecot-Mailbox-Viewer'
LATEST_API = 'https://api.github.com/repos/DesignStack/Dovecot-Mailbox-Viewer/releases/latest'
MAX_RESPONSE = 256_000


def version_tuple(version):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', version) if isinstance(version, str) else None
    if not match:
        raise ValueError('The release has an unsupported version number.')
    return tuple(int(part) for part in match.groups())


def release_version(payload, platform=None):
    """Only advertise a stable release with a download for this platform."""
    platform = platform or sys.platform
    filename = ('Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage' if platform.startswith('linux')
                else 'Dovecot-Mailbox-Viewer-Windows.exe')
    if not isinstance(payload, dict) or payload.get('draft') or payload.get('prerelease'):
        raise ValueError('No stable release was found.')
    tag = payload.get('tag_name', '')
    version_tuple(tag)
    if not any(isinstance(asset, dict) and asset.get('name') == filename
               and asset.get('state') == 'uploaded' for asset in payload.get('assets', [])):
        raise ValueError('The latest release does not have a download for your platform yet. Please try again later.')
    return tag.lstrip('v')


class UpdateChecker(QObject):
    checked = Signal(str)
    failed = Signal(str)

    def __init__(self, parent=None, *, endpoint=LATEST_API, deadline_ms=10_000):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.endpoint, self.deadline_ms = endpoint, deadline_ms
        self.reply = None
        self.data = bytearray()

    def start(self):
        self.cancel()
        self.data = bytearray()
        request = QNetworkRequest(QUrl(self.endpoint))
        request.setRawHeader(b'Accept', b'application/vnd.github+json')
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, f'Dovecot-Mailbox-Viewer/{__version__}')
        request.setTransferTimeout(self.deadline_ms)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                             QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
        reply = self.network.get(request)
        self.reply = reply
        reply.readyRead.connect(lambda: self._read(reply))
        reply.finished.connect(lambda: self._finished(reply))
        deadline = QTimer(reply)
        deadline.setSingleShot(True)
        deadline.timeout.connect(lambda: self._timeout(reply))
        reply.finished.connect(deadline.stop)
        deadline.start(self.deadline_ms)

    def _timeout(self, reply):
        if reply is self.reply:
            reply.setProperty('checkError', 'The update check timed out. Please try again.')
            reply.abort()

    def _read(self, reply):
        if reply is not self.reply or not reply.isOpen():
            return
        self.data.extend(bytes(reply.read(MAX_RESPONSE + 1 - len(self.data))))
        if len(self.data) > MAX_RESPONSE or reply.bytesAvailable() > 0:
            reply.setProperty('checkError', 'The update response was too large.')
            reply.abort()

    def _finished(self, reply):
        if reply is not self.reply:
            reply.deleteLater()
            return
        self._read(reply)
        self.reply = None
        try:
            if reply.property('checkError'):
                raise ValueError(reply.property('checkError'))
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise ValueError('Could not check GitHub for updates. Check your connection or try again later.')
            self.checked.emit(release_version(json.loads(self.data)))
        except (ValueError, TypeError, KeyError) as exc:
            self.failed.emit(str(exc))
        finally:
            reply.deleteLater()

    def cancel(self):
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()


class UpdateDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Check for updates')
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(QLabel(f'Installed version: {__version__}'))
        self.status = QLabel('Checking GitHub for a newer version…')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        note = QLabel('Checks run only when requested. Your emails and backup paths stay on this computer.')
        note.setWordWrap(True)
        note.setStyleSheet('color: #78818f;')
        layout.addWidget(note)
        self.download = QPushButton('Open download page')
        self.download.setEnabled(False)
        self.download.clicked.connect(self.open_release)
        layout.addWidget(self.download)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.retry = buttons.addButton('Check again', QDialogButtonBox.ButtonRole.ActionRole)
        self.retry.clicked.connect(self.check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.checker = UpdateChecker(self)
        self.checker.checked.connect(self.result)
        self.checker.failed.connect(self.error)
        self.finished.connect(self.checker.cancel)
        self.release = None

    def check(self):
        self.status.setText('Checking GitHub for a newer version…')
        self.download.setEnabled(False)
        self.retry.setEnabled(False)
        self.checker.start()

    def result(self, version):
        self.retry.setEnabled(True)
        self.release = version
        newer = version_tuple(version) > version_tuple(__version__)
        self.status.setText(f'Version {version} is available. Close the app before replacing your application file.'
                            if newer else f'You are up to date. Latest published version: {version}.')
        self.download.setEnabled(newer)

    def error(self, message):
        self.status.setText(message)
        self.retry.setEnabled(True)

    def open_release(self):
        if self.release:
            QDesktopServices.openUrl(QUrl(f'{REPOSITORY_URL}/releases/tag/v{self.release}'))
