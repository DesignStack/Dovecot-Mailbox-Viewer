"""Offline check of the real packaged app, using only synthetic mailbox data.

The Windows build runs this from a clean directory to catch missing Qt plugins,
SQLite support or dependencies accidentally left beside the executable.
"""

from pathlib import Path
import gzip
import json
import sys
import tempfile
import time

from PySide6.QtCore import qVersion
from PySide6.QtNetwork import QSslSocket

from viewer.version import __version__


def run(app, window_class, report_path: Path) -> int:
    report = {"version": __version__, "frozen": bool(getattr(sys, "frozen", False)),
              "qt_version": qVersion(), "ok": False}
    window = window_class(show_welcome=False)
    try:
        with tempfile.TemporaryDirectory(prefix="mail-viewer-check-") as temporary:
            root = Path(temporary)
            storage = root / "backup/email/example.com/test/storage"
            storage.mkdir(parents=True)
            raw = (b"From: Example <test@example.com>\nSubject: Portable check\n"
                   b"MIME-Version: 1.0\nContent-Type: text/html; charset=utf-8\n\n"
                   b"<p>Portable mailbox check</p>\n")
            compressed = gzip.compress(raw)
            record = (b"2 M1e C00000000\n\x01\x02N " + f"{len(compressed):016X}".encode()
                      + b"\n" + compressed + b"\n\x01\x03\nBINBOX\n\n")
            (storage / "m.1").write_bytes(record)
            window.show()
            window.show_welcome()
            app.processEvents()
            assert window.welcome_dialog.isVisible(), "Welcome guide did not open"
            assert not window.windowIcon().isNull(), "Application icon is missing"
            assert QSslSocket.supportsSsl(), "Qt TLS backend is missing"
            assert window.open_source(root), "Synthetic backup could not be opened"
            deadline = time.monotonic() + 20
            while window.worker_thread is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert window.worker_thread is None, "Import did not finish"
            assert window.listing.count() == 1, "Imported message is missing"
            window.listing.setCurrentRow(0)
            app.processEvents()
            assert "Portable mailbox check" in window.preview.toPlainText(), "HTML preview failed"
            assert len(window.catalogue.messages(None, "Portable")) == 1, "Search failed"
            assert (storage / "m.1").read_bytes() == record, "Source backup was changed"
            # Remove only the derived synthetic cache, then close before temp cleanup.
            window.clear_cache()
            window.close()
            report["ok"] = True
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0 if report["ok"] else 1
