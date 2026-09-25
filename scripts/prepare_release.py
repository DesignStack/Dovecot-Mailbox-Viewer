"""Generate Windows version information and release notes from maintained sources."""

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from viewer.version import __version__


def prepare() -> None:
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", __version__):
        raise ValueError("Version must be MAJOR.MINOR.PATCH without a v prefix")
    components = tuple(int(part) for part in __version__.split(".")) + (0,)
    if any(part > 65535 for part in components):
        raise ValueError("Windows version components must be at most 65535")
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(rf"^## \[{re.escape(__version__)}\] - \d{{4}}-\d{{2}}-\d{{2}}\n"
                        r"(.*?)(?=^## |\Z)", changelog, re.MULTILINE | re.DOTALL)
    if not section or not section.group(1).strip():
        raise ValueError(f"CHANGELOG.md needs release notes for {__version__}")
    output = ROOT / "build"
    output.mkdir(exist_ok=True)
    strings = {
        "CompanyName": "DesignStack",
        "FileDescription": "Dovecot Mailbox Viewer",
        "FileVersion": __version__,
        "InternalName": "DovecotMailboxViewer",
        "OriginalFilename": "Dovecot-Mailbox-Viewer-Windows.exe",
        "ProductName": "Dovecot Mailbox Viewer",
        "ProductVersion": __version__,
    }
    entries = ",\n".join(f"        StringStruct({key!r}, {value!r})" for key, value in strings.items())
    # PyInstaller reads this standard VERSIONINFO representation during the build.
    metadata = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={components!r}, prodvers={components!r},
                   mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                   subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('080904b0', [
{entries}
  ])]), VarFileInfo([VarStruct('Translation', [2057, 1200])])]
)
"""
    (output / "windows-version.txt").write_text(metadata, encoding="utf-8")
    notes = ("Download **Dovecot-Mailbox-Viewer-Windows.exe** below and double-click it. "
             "No installer, ZIP extraction, Python installation or separate `_internal` folder is needed. "
             "For Windows 10/11 (64-bit). Startup can take a few seconds while bundled files unpack "
             "automatically. Your original backup stays unchanged.\n\n"
             + section.group(1).strip() + "\n\n"
             "`SHA256SUMS.txt` contains the download checksum. "
             "GitHub's Source code downloads are for developers.\n")
    (output / "release-notes.md").write_text(notes, encoding="utf-8")
    print(__version__)


if __name__ == "__main__":
    prepare()
