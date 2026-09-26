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
import zipfile

from PySide6.QtCore import qVersion
from PySide6.QtNetwork import QSslSocket

from viewer.version import __version__
from viewer.printing import save_pdf
from viewer.exporting import export_messages


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
            plain = b"Subject: Uncompressed check\n\nUncompressed mailbox check\n"
            # Both records are type N: compression belongs to each payload.
            record = b"2 M1e C00000000\n"
            for payload in (compressed, plain):
                record += (b"\x01\x02N " + b" " * 8 + b" "
                           + f"{len(payload):016x}".encode() + b"\n" + payload
                           + b"\n\x01\x03\nBINBOX\n\n")
            (storage / "m.1").write_bytes(record)
            window.show()
            window.show_welcome()
            app.processEvents()
            assert window.welcome_dialog.isVisible(), "Welcome guide did not open"
            assert not window.windowIcon().isNull(), "Application icon is missing"
            if getattr(sys, "frozen", False):
                from viewer.licensing import notice_bundle
                with zipfile.ZipFile(notice_bundle()) as notices:
                    assert b"MIT License" in notices.read("LICENSE"), "Application licence is missing"
                    assert b"LGPLv3" in notices.read("README.txt"), "LGPL notice is missing"
                    assert any(n.endswith("/LGPL-3.0-only.txt") for n in notices.namelist()), "LGPL text is missing"
                    assert json.loads(notices.read("DEPENDENCIES.json"))["application"] == __version__, \
                        "Dependency manifest version is incorrect"
            assert QSslSocket.supportsSsl(), "Qt TLS backend is missing"
            assert window.open_source(root), "Synthetic backup could not be opened"
            deadline = time.monotonic() + 20
            while window.worker_thread is not None and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.01)
            assert window.worker_thread is None, "Import did not finish"
            assert window.listing.count() == 2, "Mixed-compression import lost a message"
            window.list_header.sort_actions['subject_asc'].trigger()
            window.listing.setCurrentRow(0)
            app.processEvents()
            assert "Portable mailbox check" in window.preview.toPlainText(), "HTML preview failed"
            window.list_header.unread_button.click()
            assert window.listing.count() == 0, "Unknown flags were treated as unread"
            window.list_header.all_button.click()
            assert window.listing.count() == 2, "All tab did not restore both messages"
            window.listing.setCurrentRow(0)
            window.list_header.mode_actions['compact'].trigger()
            assert window.preferences['list_mode'] == 'compact', "Compact layout menu failed"
            window.list_header.sort_actions['subject_asc'].trigger()
            assert window.preferences['sort'] == 'subject_asc', "Sort icon menu failed"
            window.zoom_actions[125].trigger()
            assert window.preferences['zoom'] == 125, "Zoom menu failed"
            window.html_button.click()
            assert not window.html_button.isChecked(), "HTML toggle failed"
            window.html_button.click()
            assert "Portable mailbox check" in window.preview.toPlainText(), "HTML toggle lost the body"
            assert len(window.catalogue.messages(None, "Portable")) == 1, "Search failed"
            assert len(window.catalogue.messages(None, "Uncompressed")) == 1, "Plain mail search failed"
            pdf = root / 'printed-email.pdf'
            save_pdf(window.print_document(), pdf, 'Portable check')
            assert pdf.read_bytes().startswith(b'%PDF-'), 'PDF export failed'
            exported, count, cancelled = export_messages(window.catalogue.path, root, folder='INBOX')
            assert count == 2 and not cancelled, 'Bulk export failed'
            assert {path.read_bytes() for path in exported.rglob('*.eml')} == {raw, plain}, \
                'Exported emails differ from the original message bytes'
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
