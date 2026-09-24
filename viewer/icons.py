"""Original outline icons, shared by the toolbar, folders and action menus.

The paths are bundled as source so frozen builds do not depend on a system icon
theme, emoji font or external files. They use the project's source licence.
"""
from functools import lru_cache

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


PATHS = {
    "mail": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m4 7 8 6 8-6"/>',
    "archive": '<rect x="3" y="3" width="18" height="5" rx="1"/><path d="M5 8v12h14V8M9 12h6"/>',
    "folder": '<path d="M3 7V5h6l2 2h10v13H3Z"/>',
    "inbox": '<path d="M3 14 6 4h12l3 10v6H3Zm0 0h5l2 3h4l2-3h5"/>',
    "sent": '<path d="m3 3 18 9-18 9 3-9Zm3 9h15"/>',
    "draft": '<path d="M6 3h8l4 4v14H6ZM14 3v5h4M9 12h6M9 16h6"/>',
    "trash": '<path d="M3 6h18M9 3h6M6 6l1 15h10l1-15M10 10v7M14 10v7"/>',
    "warning": '<path d="m12 3 10 18H2ZM12 9v5M12 17v.1"/>',
    "search": '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
    "more": '<circle cx="12" cy="5" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="12" cy="19" r="1"/>',
    "attachment": '<path d="m8 13 7-7a3 3 0 0 1 4 4L9 20a5 5 0 0 1-7-7L13 2m-7 13 9-9"/>',
    "export": '<path d="M14 3H5v18h14v-9M12 12 21 3M15 3h6v6"/>',
    "image": '<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8" cy="8" r="1.5"/><path d="m3 17 6-6 4 4 3-3 5 5"/>',
    "log": '<path d="M6 3h12v18H6ZM9 7h6M9 11h6M9 15h4"/>',
    "refresh": '<path d="M20 8a8 8 0 1 0 0 8M20 3v5h-5"/>',
}


@lru_cache(maxsize=64)
def line_icon(name: str, colour: str = "#59616d") -> QIcon:
    """Render at multiple sizes for crisp strokes on Windows display scaling."""
    icon = QIcon()
    for mode, ink in ((QIcon.Mode.Normal, colour), (QIcon.Mode.Active, colour),
                      (QIcon.Mode.Disabled, "#b4bac3"), (QIcon.Mode.Selected, "#ffffff")):
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
               f'fill="none" stroke="{ink}" stroke-width="1.65" '
               f'stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>')
        renderer = QSvgRenderer(QByteArray(svg.encode()))
        for size in (16, 20, 24, 32, 40, 48, 64, 128, 256):
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon
