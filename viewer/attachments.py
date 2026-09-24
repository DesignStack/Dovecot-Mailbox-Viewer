"""Read attachment metadata and paint compact, clickable file cards."""
from dataclasses import dataclass
from email import policy
from pathlib import PurePosixPath
import math
import re

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen
from PySide6.QtWidgets import QListView, QListWidget, QListWidgetItem, QStyle, QStyledItemDelegate

from viewer.icons import line_icon


@dataclass(frozen=True)
class Attachment:
    filename: str
    content_type: str
    data: bytes

    @property
    def size_label(self):
        size = len(self.data)
        if size < 1024:
            return f'{size} bytes'
        if size < 1024 * 1024:
            return f'{size / 1024:.1f} KB'
        return f'{size / (1024 * 1024):.1f} MB'

    @property
    def kind(self):
        """Return the outline icon and plain language file type."""
        extension = PurePosixPath(self.filename).suffix.lower()
        groups = (
            ({'.doc', '.docx', '.odt', '.rtf'}, 'file_word', 'Document'),
            ({'.xls', '.xlsx', '.ods', '.csv'}, 'file_sheet', 'Spreadsheet'),
            ({'.ppt', '.pptx', '.odp'}, 'file_slides', 'Presentation'),
            ({'.pdf'}, 'file_pdf', 'PDF'),
            ({'.zip', '.gz', '.tar', '.7z', '.rar'}, 'file_zip', 'Archive'),
            ({'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.tif', '.tiff', '.heic'}, 'image', 'Image'),
            ({'.mp3', '.wav', '.m4a', '.ogg', '.flac'}, 'file_audio', 'Audio'),
            ({'.mp4', '.mov', '.avi', '.mkv', '.webm'}, 'file_video', 'Video'),
            ({'.eml', '.msg'}, 'mail', 'Email'),
            ({'.html', '.htm', '.xml', '.json', '.py', '.js', '.css'}, 'file_code', 'Code'),
            ({'.txt', '.log'}, 'log', 'Text file'),
        )
        for extensions, icon, label in groups:
            if extension in extensions:
                return icon, label
        if self.content_type.startswith('image/'):
            return 'image', 'Image'
        return 'file', extension.lstrip('.').upper() or 'File'


def collect_attachments(message):
    """Stop at attached containers so an attached email stays one attachment."""
    result = []

    def visit(part):
        if part.get_filename() or part.get_content_disposition() == 'attachment':
            data = part.get_payload(decode=True)
            if data is None:
                payload = part.get_payload()
                # email.message treats message/rfc822 as a list of messages.
                data = b'\r\n'.join(p.as_bytes(policy=policy.SMTP) for p in payload) if isinstance(payload, list) else str(payload or '').encode('utf-8')
            result.append(Attachment(str(part.get_filename() or f'attachment-{len(result) + 1}'),
                                     part.get_content_type(), data))
        elif part.is_multipart():
            for child in part.get_payload():
                visit(child)

    visit(message)
    return result


def suggested_filename(name):
    """Keep source-supplied names out of directory paths and Windows device names."""
    name = name.replace('\\', '/').rsplit('/', 1)[-1]
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .') or 'attachment'
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        name = '_' + name
    return name


class AttachmentDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        attachment = index.data(Qt.ItemDataRole.UserRole)
        if attachment is None:
            return
        r = option.rect.adjusted(1, 1, -7, -7)
        selected = bool(option.state & (QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver))
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setBrush(QColor('#eef4fc' if selected else '#fafbfc'))
        painter.setPen(QPen(QColor('#8bb0df' if selected else '#dce1e8'), 1))
        painter.drawRoundedRect(r, 5, 5)
        icon, kind = attachment.kind
        line_icon(icon).paint(painter, QRect(r.x() + 11, r.y() + 14, 28, 28))
        line_icon('download').paint(painter, QRect(r.right() - 29, r.y() + 20, 16, 16))
        font = QFont(option.font)
        font.setPixelSize(12)
        painter.setFont(font)
        painter.setPen(QColor('#303742'))
        text_rect = QRect(r.x() + 50, r.y() + 8, max(1, r.width() - 88), 22)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(font).elidedText(attachment.filename, Qt.TextElideMode.ElideMiddle, text_rect.width()))
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor('#7b838e'))
        detail = f'{kind} · {attachment.size_label}'
        painter.drawText(text_rect.translated(0, 22), Qt.AlignmentFlag.AlignVCenter,
                         QFontMetrics(font).elidedText(detail, Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()


class AttachmentList(QListWidget):
    """Wrap cards to the pane width; scroll after three rows to leave body space."""
    save_requested = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('attachmentCards')
        self.setAccessibleName('Email attachments; click a file to save it')
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setItemDelegate(AttachmentDelegate(self))
        self.itemClicked.connect(lambda item: self.save_requested.emit(self.row(item)))
        self.hide()

    def set_attachments(self, attachments):
        self.clear()
        for attachment in attachments:
            item = QListWidgetItem(f'{attachment.filename} — {attachment.size_label}')
            item.setData(Qt.ItemDataRole.UserRole, attachment)
            item.setToolTip(f'{attachment.filename}\n{attachment.size_label}\nClick to save attachment')
            self.addItem(item)
        self.setVisible(bool(attachments))
        self._fit_cards()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_cards()

    def _fit_cards(self):
        # Reserve scrollbar width even when hidden to avoid a wrap/resize loop.
        available = max(1, self.width() - 20)
        columns = max(1, available // 270)
        cell_width = max(1, available // columns)
        rows = max(1, math.ceil(self.count() / columns))
        self.setGridSize(QSize(cell_width, 66))
        self.setFixedHeight(min(3, rows) * 66 + 2)
        for index in range(self.count()):
            self.item(index).setSizeHint(QSize(cell_width, 66))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentRow() >= 0:
            self.save_requested.emit(self.currentRow())
            event.accept()
        else:
            super().keyPressEvent(event)
