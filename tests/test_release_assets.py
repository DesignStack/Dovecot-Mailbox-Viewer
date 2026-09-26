"""Release notices include referenced texts without extracting unsafe paths."""

from io import BytesIO
from pathlib import Path
import json
import tarfile
import tempfile
import unittest

from scripts.release_assets import copy_notices


class NoticeTests(unittest.TestCase):
    def test_referenced_licence_and_unsafe_paths(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "source.tar.xz"
            entries = {
                "src/qt_attribution.json": json.dumps({"LicenseFile": "odd/name.h"}).encode(),
                "src/odd/name.h": b"Copyright: synthetic licence",
                "src/LICENSE": b"Synthetic licence",
                "../LICENSE": b"Must not escape",
            }
            with tarfile.open(archive, "w:xz") as output:
                for name, content in entries.items():
                    entry = tarfile.TarInfo(name)
                    entry.size = len(content)
                    output.addfile(entry, BytesIO(content))
                link = tarfile.TarInfo("src/COPYING")
                link.type = tarfile.SYMTYPE
                link.linkname = "../../outside"
                output.addfile(link)
            copy_notices(archive, root / "notices")
            self.assertEqual((root / "notices/src/odd/name.h").read_bytes(), entries["src/odd/name.h"])
            self.assertFalse((root / "LICENSE").exists())
            self.assertFalse((root / "notices/src/COPYING").exists())
