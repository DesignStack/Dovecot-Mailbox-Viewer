"""Paint mail rows with separate text styles and responsive, elided columns."""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QIcon
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from viewer.icons import line_icon

DETAILS_ROLE = Qt.ItemDataRole.UserRole + 1


class FolderDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(220, 44)

    def paint(self, painter, option, index):
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        colour = QColor("#ffffff" if selected else "#444d59")
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.fillRect(option.rect, QColor("#357ddb" if selected else "#f7f8fa"))
        data = index.data(DETAILS_ROLE) or {}
        r = option.rect
        mode = QIcon.Mode.Selected if selected else QIcon.Mode.Normal
        line_icon(data.get("icon", "folder")).paint(painter, QRect(r.x() + 18, r.y() + 12, 20, 20), mode=mode)
        font = QFont(option.font)
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(colour)
        count = str(data.get("count", 0))
        metrics = QFontMetrics(font)
        width = max(24, metrics.horizontalAdvance(count) + 14)
        pill = QRect(r.right() - width - 14, r.y() + 12, width, 20)
        if data.get("count", 0):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffffff") if not selected else QColor("#5e97e3"))
            painter.drawRoundedRect(pill, 10, 10)
            painter.setPen(colour)
            painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, count)
        text_rect = QRect(r.x() + 50, r.y(), max(0, r.width() - width - 78), r.height())
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(data.get("label", ""), Qt.TextElideMode.ElideRight, text_rect.width()))
        painter.restore()


class MessageDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(360, 96)

    def paint(self, painter, option, index):
        data = index.data(DETAILS_ROLE) or {}
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        painter.save()
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        r = option.rect
        painter.fillRect(r, QColor("#357ddb" if selected else "#ffffff"))
        ink = "#ffffff" if selected else "#262d36"
        muted = "#e6efff" if selected else "#7b838e"
        left = r.x() + 22
        width = r.width() - 38

        def text(value, rect, size, colour, bold=False):
            font = QFont(option.font)
            font.setPixelSize(size)
            font.setBold(bold)
            painter.setFont(font)
            painter.setPen(QColor(colour))
            value = QFontMetrics(font).elidedText(value, Qt.TextElideMode.ElideRight, max(0, rect.width()))
            painter.drawText(rect, Qt.AlignmentFlag.AlignVCenter, value)

        if data.get("unread"):
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#ffffff" if selected else "#357ddb"))
            painter.drawEllipse(r.x() + 9, r.y() + 20, 5, 5)
        text(data.get("sender", ""), QRect(left, r.y() + 10, max(0, width - 100), 24), 14, ink, data.get("unread", False))
        text(data.get("date", ""), QRect(r.right() - 108, r.y() + 10, 94, 24), 11, muted)
        attachment_space = 24 if data.get("attachment") else 0
        text(data.get("subject", ""), QRect(left, r.y() + 34, width - attachment_space, 21), 13, ink, data.get("unread", False))
        if attachment_space:
            line_icon("attachment").paint(painter, QRect(r.right() - 32, r.y() + 36, 16, 16),
                                          mode=QIcon.Mode.Selected if selected else QIcon.Mode.Normal)
        text(data.get("snippet", ""), QRect(left, r.y() + 57, width, 23), 12, muted)
        painter.setPen(QColor("#ffffff" if selected else "#eef0f3"))
        painter.drawLine(r.bottomLeft(), r.bottomRight())
        painter.restore()
