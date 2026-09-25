"""Protect published releases and ensure partial uploads never go public."""

from pathlib import Path
from unittest.mock import patch
import hashlib
import json
import tempfile
import unittest
from urllib.error import HTTPError

from scripts import publish_release
from viewer.version import __version__


class ReleaseTests(unittest.TestCase):
    environment = {"GITHUB_REF": "refs/heads/main", "GH_TOKEN": "test-token",
                   "GITHUB_REPOSITORY": "example/viewer", "GITHUB_SHA": "1234"}

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
            (root / "dist").mkdir()
            executable = root / "dist/Dovecot-Mailbox-Viewer-Windows.exe"
            executable.write_bytes(b"synthetic-test-binary")
            digest = hashlib.sha256(executable.read_bytes()).hexdigest()
            (root / "dist/SHA256SUMS.txt").write_text(f"{digest}  {executable.name}\n")
            (root / "build/smoke-test.json").write_text(json.dumps(
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
