"""A small, local-only history of successfully opened backups."""
import json
from pathlib import Path

from PySide6.QtCore import QSettings
from viewer.catalog import cache_path


class RecentBackups:
    def __init__(self, settings=None):
        self.settings = settings if settings is not None else QSettings(
            str(cache_path(Path('preferences'), 'app').parent / 'settings.ini'), QSettings.Format.IniFormat)

    def entries(self):
        try:
            entries = json.loads(self.settings.value('recentBackups', '[]'))
            if not isinstance(entries, list):
                return []
            return [item for item in entries if isinstance(item, dict)
                    and isinstance(item.get('path'), str) and isinstance(item.get('account'), str)][:8]
        except (ValueError, TypeError):
            return []

    def remember(self, source, account):
        path = str(Path(source).resolve())
        entries = [item for item in self.entries() if item['path'].casefold() != path.casefold()]
        self.settings.setValue('recentBackups', json.dumps([{'path': path, 'account': account}] + entries[:7]))
        self.settings.sync()

    def clear(self):
        self.settings.remove('recentBackups')
        self.settings.sync()


def dropped_backup(mime):
    """Accept exactly one local archive or folder, never text or remote URLs."""
    urls = mime.urls()
    if len(urls) != 1 or not urls[0].isLocalFile():
        return None
    path = Path(urls[0].toLocalFile())
    if path.is_dir() or (path.is_file() and path.name.lower().endswith(('.tar.gz', '.tgz'))):
        return path
    return None
