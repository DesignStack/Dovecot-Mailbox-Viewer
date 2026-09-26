"""Background discovery, cache validation and incremental mailbox import."""
import json
import logging
import threading
import time
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from viewer.catalog import Catalogue, cache_path, source_fingerprint
from viewer.mdbox import discover, read_account
from viewer.dovecot_index import read_statuses
from viewer.operations import Cancellation, Cancelled
from viewer.progress import ImportProgress


class OpenWorker(QThread):
    phase = Signal(str)
    choose_account = Signal(object)
    prepared = Signal(str, str)
    progress = Signal(int, int)
    batch = Signal(int)
    completed = Signal(int, bool)
    cancelled = Signal(int)
    failed = Signal(str)

    def __init__(self, source, preferred_account='', parent=None):
        super().__init__(parent)
        self.source = Path(source)
        self.preferred_account = preferred_account
        self.cancellation = Cancellation()
        self.choice_ready = threading.Event()
        self.account = ''
        self.cache_factory = cache_path
        self.activity = ImportProgress()

    def set_phase(self, text):
        self.activity.phase(text)
        self.phase.emit(text)

    def cancel(self):
        self.cancellation.cancel()
        self.choice_ready.set()

    def select_account(self, account):
        self.account = account
        self.choice_ready.set()

    def _cached_info(self):
        """A recent archive can skip discovery when its identity is unchanged."""
        if not self.preferred_account or not self.source.is_file():
            return None
        database = self.cache_factory(self.source, self.preferred_account)
        if not database.exists():
            return None
        catalogue = Catalogue(database)
        try:
            meta = catalogue.metadata()
            info = json.loads(meta.get('source_info', 'null'))
            if (info and meta.get('source') == str(self.source.resolve())
                    and catalogue.reusable(source_fingerprint(self.source, info, self.cancellation.check))):
                return info
        except (ValueError, TypeError, KeyError):
            pass
        finally:
            catalogue.close()
        return None

    def run(self):
        catalogue = None
        count = 0
        check = self.cancellation.check
        try:
            logging.getLogger("viewer").info("Opening backup: type=%s archive_bytes=%s",
                "folder" if self.source.is_dir() else "archive",
                self.source.stat().st_size if self.source.is_file() else "unknown")
            self.set_phase('Checking for a saved index…')
            info = self._cached_info()
            check()
            if info:
                self.account = self.preferred_account
            else:
                self.set_phase('Finding mailboxes in your backup…')
                accounts = discover(self.source, check, progress=self.activity.update)
                check()
                if not accounts:
                    raise ValueError('No supported mailbox was found. Choose a JetBackup/cPanel .tar.gz backup '
                                     'or its extracted folder. If needed, try the parent folder.')
                if len(accounts) == 1:
                    self.account = next(iter(accounts))
                else:
                    self.set_phase('Choose a mailbox to open…')
                    self.choose_account.emit(sorted(accounts))
                    while not self.choice_ready.wait(0.1):
                        check()
                    check()
                info = accounts[self.account]
            database = self.cache_factory(self.source, self.account)
            self.set_phase('Checking the backup for changes…')
            fingerprint = source_fingerprint(self.source, info, check, progress=self.activity.update)
            catalogue = Catalogue(database)
            if catalogue.reusable(fingerprint):
                check()
                self.activity.update(available=catalogue.count(), messages=catalogue.count())
                logging.getLogger("viewer").info("Reusing complete index: %d messages", catalogue.count())
                self.prepared.emit(self.account, str(database))
                self.completed.emit(catalogue.count(), True)
                return
            self.set_phase("Preparing the local search index…")
            catalogue.reset()
            for folder in sorted(info['folders']):
                catalogue.add_folder(folder)
            catalogue.conn.executemany('INSERT OR REPLACE INTO metadata VALUES (?, ?)', [
                ('source', str(self.source.resolve())), ('account', self.account), ('complete', '0')])
            catalogue.commit()
            self.prepared.emit(self.account, str(database))
            self.set_phase('Reading Dovecot folder and message status…')
            statuses = read_statuses(self.source, info, check, progress=self.activity.update)
            self.set_phase('Reading emails · you can browse as they appear…')
            last_commit = time.monotonic()
            last_update = 0
            for record in read_account(self.source, info, check, progress=self.activity.update):
                check()
                match = statuses.get(record.guid) if record.guid else None
                self.activity.update(detail=f"Processing email {count + 1:,} ({len(record.raw):,} bytes)")
                catalogue.add(record, match[1] if match else None,
                              folder=match[0] if match else None)
                count += 1
                self.activity.update(messages=count)
                now = time.monotonic()
                if now - last_update >= 0.15:
                    self.progress.emit(min(99, int(100 * record.bytes_done / max(1, record.bytes_total))), count)
                    last_update = now
                if count == 1 or now - last_commit >= 0.5:
                    catalogue.commit()
                    self.activity.update(available=count)
                    self.batch.emit(count)
                    last_commit = now
            check()
            self.set_phase("Finishing the search index…")
            catalogue.finish(fingerprint, source=str(self.source.resolve()), account=self.account,
                             source_info=json.dumps(info, default=lambda value: sorted(value)))
            self.activity.update(available=count)
            self.batch.emit(count)
            self.completed.emit(count, False)
            logging.getLogger('viewer').info('Import complete: %d messages', count)
        except Cancelled:
            if catalogue is not None:
                catalogue.commit()
                if count:
                    self.batch.emit(count)
            self.activity.update(available=count)
            logging.getLogger("viewer").info("Import cancelled: %d messages", count)
            self.cancelled.emit(count)
        except Exception as exc:
            logging.getLogger('viewer').exception('Cannot open mailbox')
            self.failed.emit(str(exc))
        finally:
            if catalogue is not None:
                catalogue.close()

