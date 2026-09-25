"""Inspect and remove only this application's rebuildable mailbox databases."""
from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QTreeWidget, QTreeWidgetItem,
                               QHBoxLayout, QPushButton, QMessageBox)


@dataclass(frozen=True)
class CacheEntry:
    path: Path
    account: str
    source: str
    size: int
    state: str
    indexed_at: str


def valid_cache(root, path):
    root, path = Path(root).resolve(), Path(path)
    return (not path.is_symlink() and path.parent.resolve() == root
            and re.fullmatch(r'[0-9a-f]{20}\.sqlite3', path.name) is not None)


def cache_entries(root):
    entries = []
    for path in sorted(Path(root).glob('*.sqlite3')):
        if not valid_cache(root, path):
            continue
        size = sum(p.stat().st_size for p in (path, Path(str(path)+'-wal'), Path(str(path)+'-shm')) if p.is_file())
        meta = {}
        try:
            connection = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=0.2)
            try:
                meta = dict(connection.execute('SELECT key,value FROM metadata'))
            finally:
                connection.close()
        except sqlite3.Error:
            pass
        entries.append(CacheEntry(path, meta.get('account', 'Older cache'), meta.get('source', ''), size,
                                  'Ready' if meta.get('complete') == '1' else 'Incomplete', meta.get('indexed_at', '')))
    return entries


def delete_cache(root, path):
    path = Path(path)
    if not valid_cache(root, path):
        raise ValueError('This is not a mailbox cache owned by the application.')
    for suffix in ('', '-wal', '-shm'):
        candidate = Path(str(path) + suffix)
        if candidate.is_symlink():
            raise ValueError('Cache links cannot be deleted here.')
    for suffix in ('', '-wal', '-shm'):
        Path(str(path) + suffix).unlink(missing_ok=True)


def human_size(size):
    return f'{size / 1024 / 1024:.1f} MB' if size >= 1024 * 1024 else f'{size / 1024:.0f} KB'


class CacheDialog(QDialog):
    def __init__(self, root, current, close_current, parent=None):
        super().__init__(parent)
        self.root, self.current, self.close_current = root, current, close_current
        self.setWindowTitle('Cache manager')
        self.resize(760, 420)
        layout = QVBoxLayout(self)
        label = QLabel('Saved indexes make backups faster to reopen. Removing one does not change your backup.\n'
                       'The index will be rebuilt next time you open it. Removing the current index closes its mailbox.')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Mailbox', 'Size', 'Status', 'Last indexed', 'Backup location'])
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.ExtendedSelection)
        self.tree.setRootIsDecorated(False)
        layout.addWidget(self.tree)
        self.total = QLabel()
        layout.addWidget(self.total)
        buttons = QHBoxLayout()
        self.remove = QPushButton('Remove selected…')
        self.remove.clicked.connect(self.remove_selected)
        buttons.addWidget(self.remove)
        buttons.addStretch()
        done = QPushButton('Done')
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        layout.addLayout(buttons)
        self.tree.itemSelectionChanged.connect(lambda: self.remove.setEnabled(bool(self.tree.selectedItems())))
        self.refresh()

    def refresh(self):
        self.tree.clear()
        entries = cache_entries(self.root)
        for entry in entries:
            current = entry.path == self.current
            item = QTreeWidgetItem([entry.account, human_size(entry.size), entry.state + (' · open' if current else ''),
                                    entry.indexed_at[:16].replace('T', ' '), entry.source or str(entry.path)])
            item.setData(0, Qt.ItemDataRole.UserRole, str(entry.path))
            item.setToolTip(4, entry.source)
            self.tree.addTopLevelItem(item)
        for column in range(4):
            self.tree.resizeColumnToContents(column)
        self.total.setText(f'{len(entries)} saved indexes · {human_size(sum(e.size for e in entries))}')
        self.remove.setEnabled(False)

    def remove_selected(self):
        paths = [Path(item.data(0, Qt.ItemDataRole.UserRole)) for item in self.tree.selectedItems()]
        if not paths or QMessageBox.question(self, 'Remove saved indexes?',
                f'Remove {len(paths)} saved index(es)? Original backups will stay unchanged.') != QMessageBox.StandardButton.Yes:
            return
        try:
            for path in paths:
                if path == self.current:
                    self.close_current()
                    self.current = None
                delete_cache(self.root, path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, 'Cannot remove cache', str(exc))
        self.refresh()
