"""Stream original emails to a new export folder without replacing existing files."""
from datetime import datetime
from contextlib import closing
from pathlib import Path
import hashlib
import re
import sqlite3
import tempfile

from PySide6.QtCore import QThread, Signal


def safe_name(value, fallback='message', length=90):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', str(value or ''))[:length].strip(' .') or fallback
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL',
                                    *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        name = '_' + name
    return name


def export_messages(database, destination, *, ids=None, folder=None, progress=lambda done, total: None,
                    cancelled=lambda: False):
    """Return (new folder, written count, cancelled); never use the UI's 5,000-row limit.

Read a consistent SQLite snapshot on the worker's own connection. A new directory
and exclusive file creation protect existing exports; one raw email is held at a time.
"""
    output = Path(tempfile.mkdtemp(prefix=f'Mail-export-{datetime.now():%Y%m%d-%H%M%S}-', dir=destination))
    written = 0
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        if ids is not None:
            # A temporary ID table avoids SQLite's bound-parameter limit for large selections.
            conn.execute('CREATE TEMP TABLE chosen(id INTEGER PRIMARY KEY)')
            conn.executemany('INSERT OR IGNORE INTO chosen VALUES (?)', ((int(i),) for i in ids))
            where, params = ' WHERE id IN (SELECT id FROM chosen)', ()
        else:
            where, params = (' WHERE folder=?', (folder,)) if folder is not None else ('', ())
        total = conn.execute('SELECT COUNT(*) FROM messages' + where, params).fetchone()[0]
        progress(0, total)
        folders = {}
        used_names = set()
        try:
            for row in conn.execute('SELECT id, folder, subject, raw FROM messages' + where + ' ORDER BY id', params):
                if cancelled():
                    return output, written, True
                original = row['folder']
                if original not in folders:
                    # Keep mailbox folders separate, including names that Windows treats as identical.
                    name = safe_name(original, 'Unfiled', 60)
                    if name.casefold() in used_names:
                        name += '-' + hashlib.sha256(original.encode()).hexdigest()[:10]
                    used_names.add(name.casefold())
                    folders[original] = output / name
                    folders[original].mkdir()
                path = folders[original] / f"{row['id']:06d} - {safe_name(row['subject'])}.eml"
                created = False
                try:
                    with path.open('xb') as stream:
                        created = True
                        stream.write(row['raw'])
                except OSError:
                    # A failed write must not leave an apparently complete email behind.
                    if created:
                        path.unlink(missing_ok=True)
                    raise
                written += 1
                if written % 25 == 0 or written == total:
                    progress(written, total)
        except Exception as exc:
            raise RuntimeError(f'{written} emails saved in {output}. Export stopped: {exc}') from exc
    return output, written, False


class ExportWorker(QThread):
    progress = Signal(int, int)
    completed = Signal(str, int, bool)
    failed = Signal(str)

    def __init__(self, database, destination, *, ids=None, folder=None, parent=None):
        super().__init__(parent)
        self.database, self.destination, self.ids, self.folder = database, destination, ids, folder

    def run(self):
        try:
            output, count, cancelled = export_messages(
                self.database, self.destination, ids=self.ids, folder=self.folder,
                progress=self.progress.emit, cancelled=self.isInterruptionRequested)
            self.completed.emit(str(output), count, cancelled)
        except Exception as exc:
            self.failed.emit(str(exc))
