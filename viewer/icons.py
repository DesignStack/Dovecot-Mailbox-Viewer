"""Original outline icons, shared by the toolbar, folders and action menus.

The paths are bundled as source so frozen builds do not depend on a system icon
theme, emoji font or external files. They use the project's source licence.
"""
from functools import lru_cache

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer


PATHS = {
    "sort": '<path d="M4 6h16M7 12h10M10 18h4"/>',
    "list_preview": '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 7h8M8 11h8M8 15h5"/>',
    "list_compact": '<path d="M4 5h16M4 10h16M4 15h16M4 20h16"/>',
    "zoom": '<circle cx="10" cy="10" r="6.5"/><path d="m15 15 6 6M7 10h6M10 7v6"/>',
    "code": '<path d="m7 6-5 6 5 6m10-12 5 6-5 6M14 3l-4 18"/>',
    "left": '<path d="m15 6-6 6 6 6"/>',
    "right": '<path d="m9 6 6 6-6 6"/>',
    "minus": '<path d="M5 12h14"/>',
    "plus": '<path d="M5 12h14M12 5v14"/>',
    "up": '<path d="m6 15 6-6 6 6"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "close": '<path d="m6 6 12 12M6 18 18 6"/>',
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
    "download": '<path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7v.1"/>',
    "print": '<path d="M6 8V3h12v5M6 17H3V8h18v9h-3M6 14h12v7H6ZM17 11h1"/>',
    "exit": '<path d="M10 3H4v18h6M9 12h12m-4-4 4 4-4 4"/>',
    "file": '<path d="M5 2h9l5 5v15H5ZM14 2v6h5"/>',
    "file_word": '<path d="M5 2h9l5 5v15H5ZM14 2v6h5M8 12l1 6 3-4 3 4 1-6"/>',
    "file_sheet": '<path d="M5 2h9l5 5v15H5ZM14 2v6h5M8 11h8v8H8ZM8 15h8M12 11v8"/>',
    "file_pdf": '<path d="M5 2h9l5 5v15H5ZM14 2v6h5M9 19v-8h3a2 2 0 0 1 0 4H9"/>',
    "file_slides": '<path d="M5 2h9l5 5v15H5ZM14 2v6h5M8 11h8v6H8ZM12 17v2m-2 0h4"/>',
    "file_zip": '<path d="M5 2h9l5 5v15H5ZM14 2v6h5M10 5h2m0 3h-2m0 3h2m0 3h-2m0 3h2v2h-2Z"/>',
    "file_audio": '<path d="M5 2h9l5 5v15H5ZM14 2v6h5M13 17v-6l3 1"/><circle cx="11" cy="17" r="2"/>',
    "file_video": '<path d="M5 2h9l5 5v15H5ZM14 2v6h5m-9 3 6 4-6 4Z"/>',
    "file_code": '<path d="M5 2h9l5 5v15H5ZM14 2v6h5m-9 4-2 3 2 3m4-6 2 3-2 3"/>',
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
