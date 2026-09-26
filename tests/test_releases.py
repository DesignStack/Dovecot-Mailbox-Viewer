"""Protect published releases and ensure partial uploads never go public."""

from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import tempfile
import unittest
import zipfile
from urllib.error import HTTPError

from scripts import publish_release
from viewer.version import __version__


class ReleaseTests(unittest.TestCase):
    environment = {"GITHUB_REF": "refs/heads/main", "GH_TOKEN": "test-token",
                   "GITHUB_REPOSITORY": "example/viewer", "GITHUB_SHA": "1234"}

    def make_assets(self, root):
        output = root / "dist"
        output.mkdir(exist_ok=True)
        sources = []
        for name in ("qt-everywhere-src-test.tar.xz", "pyside-setup-everywhere-src-test.tar.xz"):
            (output / name).write_bytes(b"synthetic-library-source")
            sources.append({"file": name, "sha256": hashlib.sha256(b"synthetic-library-source").hexdigest()})
        (output / "Dovecot-Mailbox-Viewer-Windows.exe").write_bytes(b"synthetic-test-binary")
        (output / "Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage").write_bytes(b"synthetic-linux-binary")
        (output / "Bundled-files.txt").write_text("Qt6Core.dll\n")
        (output / "Bundled-files-Linux.txt").write_text("libQt6Core.so\n")
        with zipfile.ZipFile(output / "Third-party-notices.zip", "w") as bundle:
            bundle.writestr("DEPENDENCIES.json", json.dumps({"application": __version__, "sources": sources}))
        with zipfile.ZipFile(output / "Third-party-notices-Linux.zip", "w") as bundle:
            bundle.writestr("DEPENDENCIES.json", json.dumps({"application": __version__, "sources": sources}))
        checksums = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n"
                            for p in sorted(output.iterdir()))
        (output / "SHA256SUMS.txt").write_text(checksums)

    def test_missing_or_changed_source_asset_blocks_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_assets(root)
            self.assertEqual(len(publish_release.verified_assets(root)), 9)
            source = root / "dist/qt-everywhere-src-test.tar.xz"
            source.write_bytes(b"altered-source")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                publish_release.verified_assets(root)
            source.unlink()
            with self.assertRaises(FileNotFoundError):
                publish_release.verified_assets(root)

    def test_published_version_is_never_changed(self):
        with patch.dict("os.environ", self.environment), \
                patch.object(publish_release, "request", return_value={"draft": False}) as api:
            publish_release.publish()
        self.assertEqual(api.call_count, 1)
        self.assertNotIn("method", api.call_args.kwargs)

    def test_failed_asset_upload_keeps_release_as_draft(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "build").mkdir()
            self.make_assets(root)
            for platform in ('Windows', 'Linux'):
                (root / f"build/smoke-test-{platform}.json").write_text(json.dumps(
                    {"ok": True, "frozen": True, "version": __version__}))
            (root / "build/release-notes.md").write_text("Release notes")
            replies = [HTTPError("url", 404, "Not found", {}, None), [],
                       {"id": 1, "upload_url": "https://uploads.github.com/repos/example/viewer/releases/1/assets{?name}",
                        "assets": []}, OSError("upload interrupted")]
            with patch.dict("os.environ", self.environment), \
                    patch.object(publish_release, "ROOT", root), \
                    patch.object(publish_release, "request", side_effect=replies) as api:
                with self.assertRaisesRegex(OSError, "upload interrupted"):
                    publish_release.publish()
            self.assertTrue(api.call_args_list[2].kwargs["data"]["draft"])
            self.assertFalse(any(call.kwargs.get("method") == "PATCH" for call in api.call_args_list))

    def test_release_is_public_only_after_all_assets_are_uploaded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "build").mkdir()
            self.make_assets(root)
            for platform in ('Windows', 'Linux'):
                (root / f"build/smoke-test-{platform}.json").write_text(json.dumps(
                    {"ok": True, "frozen": True, "version": __version__}))
            (root / "build/release-notes.md").write_text("Release notes")
            replies = [HTTPError("url", 404, "Not found", {}, None), [],
                       {"id": 1, "upload_url": "https://uploads.github.com/repos/example/viewer/releases/1/assets{?name}",
                        "assets": []}] + [{}] * 9 + [{"html_url": "https://github.com/example/viewer/releases/test"}]
            with patch.dict("os.environ", self.environment), \
                    patch.object(publish_release, "ROOT", root), \
                    patch.object(publish_release, "request", side_effect=replies) as api:
                publish_release.publish()
            uploads = api.call_args_list[3:-1]
            self.assertEqual(len(uploads), 9)
            self.assertTrue(all(isinstance(call.kwargs["data"], Path) for call in uploads))
            self.assertEqual(api.call_args_list[-1].kwargs["data"], {"draft": False, "make_latest": "true"})
