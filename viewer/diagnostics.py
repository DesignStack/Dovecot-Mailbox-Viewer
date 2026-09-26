"""Local diagnostics and a shareable bundle that never includes a mailbox cache."""
import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import platform
from pathlib import Path
import sqlite3
import sys
import threading
import zipfile
from PySide6.QtCore import QTimer, qVersion, qInstallMessageHandler
from viewer.catalog import cache_path
from viewer.version import __version__

_fault_stream = None
_watchdog = None


def log_path():
    return cache_path(Path('diagnostics'), 'app').parent / 'viewer.log'


def system_info():
    return dict(version=__version__, platform=platform.platform(), architecture=platform.machine(),
                python=platform.python_version(), qt=qVersion(), sqlite=sqlite3.sqlite_version,
                packaged=bool(getattr(sys, 'frozen', False)))


def configure_logging():
    global _fault_stream
    target = log_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger('viewer')
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(target, maxBytes=2_000_000, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s [%(threadName)s] %(message)s'))
        logger.addHandler(handler)
    logger.info('Application starting: %s', json.dumps(system_info()))
    if _fault_stream is None:
        faults = target.with_name('viewer-fault.log')
        if faults.exists():
            faults.replace(target.with_name('viewer-fault-previous.log'))
        _fault_stream = faults.open('w', encoding='utf-8', buffering=1)
        _fault_stream.write('Native crash / GUI heartbeat diagnostics. A timeout can also mean a native file dialog is open.\n')
        faulthandler.enable(file=_fault_stream, all_threads=True)
    def uncaught(kind, value, traceback):
        logger.error('Unhandled application error', exc_info=(kind, value, traceback))
    sys.excepthook = uncaught
    threading.excepthook = lambda args: uncaught(args.exc_type, args.exc_value, args.exc_traceback)
    # Qt warnings and native exceptions otherwise disappear in a windowed EXE.
    qInstallMessageHandler(lambda kind, context, message: logger.warning('Qt: %s', message))


def start_watchdog(app):
    """A native watchdog can dump stacks even when Python/Qt stops responding."""
    global _watchdog
    def heartbeat():
        faulthandler.dump_traceback_later(30, repeat=True, file=_fault_stream)
    _watchdog = QTimer(app)
    _watchdog.setInterval(2000)
    _watchdog.timeout.connect(heartbeat)
    _watchdog.start()
    heartbeat()
    app.aboutToQuit.connect(faulthandler.cancel_dump_traceback_later)


def save_diagnostics(destination, *, folder=None, activity=None):
    """Allowlist files: never copy SQLite, emails, attachments or backup contents."""
    folder = Path(folder) if folder is not None else log_path().parent
    for handler in logging.getLogger('viewer').handlers:
        handler.flush()
    if _fault_stream is not None:
        _fault_stream.flush()
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in ('viewer.log', 'viewer.log.1', 'viewer.log.2',
                     'viewer-fault.log', 'viewer-fault-previous.log'):
            path = folder / name
            if path.is_file() and not path.is_symlink():
                # Limit pathological native traces while keeping the most recent events.
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 2_000_000))
                    bundle.writestr(name, stream.read())
        bundle.writestr('system.json', json.dumps(system_info(), indent=2))
        if activity:
            bundle.writestr('last-import.json', json.dumps(activity, indent=2))
