"""Package notices, exact dependency versions and unmodified library sources.

Source archives are separate release downloads, never embedded in the executable.
Licence texts are also embedded so a lone portable EXE retains its notices.
"""

from concurrent.futures import ThreadPoolExecutor
from importlib import metadata
from pathlib import Path, PurePosixPath
from urllib.request import urlopen
import hashlib
import json
import platform
import posixpath
import re
import shutil
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from viewer.version import __version__


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_source(url, destination, expected=None):
    """Verify the exact upstream archive against its published SHA-256."""
    if expected is None:
        with urlopen(url + ".sha256", timeout=60) as response:
            expected = response.read(4096).decode("ascii").split()[0].lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Invalid upstream source checksum")
    if not destination.exists() or digest(destination) != expected:
        temporary = destination.with_suffix(destination.suffix + ".part")
        with urlopen(url, timeout=120) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if digest(temporary) != expected:
            temporary.unlink()
            raise ValueError(f"Source checksum mismatch: {destination.name}")
        temporary.replace(destination)
    return {"file": destination.name, "url": url, "sha256": expected}


def copy_notices(archive, target):
    """Copy notice files only; never extract archive paths or links unchecked."""
    count = 0
    references = set()
    with tarfile.open(archive, "r|*") as source:
        for entry in source:
            path = PurePosixPath(entry.name)
            if not entry.isfile() or path.is_absolute() or ".." in path.parts:
                continue
            name = path.name.lower()
            if not ("licenses" in [part.lower() for part in path.parts]
                    or "license" in name or "licence" in name
                    or name.startswith(("copying", "copyright", "notice", "lgpl", "gpl", "mit.", "psf-", "readme"))
                    or name == "bufferprocs_py37.h"
                    or name == "qt_attribution.json"):
                continue
            if entry.size > 8 * 1024 * 1024:
                raise ValueError(f"Unexpectedly large notice: {entry.name}")
            destination = target.joinpath(*path.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with source.extractfile(entry) as content, destination.open("wb") as output:
                shutil.copyfileobj(content, output)
            if name == "qt_attribution.json":
                # Some upstream attribution files contain literal line breaks.
                entries = json.loads(destination.read_text(encoding="utf-8"), strict=False)
                for item in entries if isinstance(entries, list) else [entries]:
                    files = item.get("LicenseFile", [])
                    for filename in [files] if isinstance(files, str) else files:
                        reference = PurePosixPath(posixpath.normpath(str(path.parent / filename)))
                        if reference.is_absolute() or ".." in reference.parts or reference.parts[0] != path.parts[0]:
                            raise ValueError("Unsafe upstream licence reference")
                        references.add(reference.as_posix())
            count += 1
    if not count:
        raise ValueError(f"No notices found in {archive.name}")
    missing = {name for name in references if not target.joinpath(*PurePosixPath(name).parts).is_file()}
    if missing:
        # A licence may be embedded in a header or other unusually named file.
        with tarfile.open(archive, "r|*") as source:
            for entry in source:
                if entry.name not in missing or not entry.isfile() or entry.size > 8 * 1024 * 1024:
                    continue
                destination = target.joinpath(*PurePosixPath(entry.name).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(entry) as content, destination.open("wb") as output:
                    shutil.copyfileobj(content, output)
                missing.remove(entry.name)
        if missing:
            raise ValueError(f"Missing upstream licence files: {sorted(missing)}")


def prepare():
    from PySide6.QtCore import qVersion

    output = ROOT / "dist"
    notices = ROOT / "build/notices"
    output.mkdir(exist_ok=True)
    if notices.exists():
        shutil.rmtree(notices)
    notices.mkdir(parents=True)
    qt, pyside = qVersion(), metadata.version("PySide6")
    if metadata.version("shiboken6") != pyside:
        raise ValueError("PySide6 and Shiboken6 must match")
    series = ".".join(qt.split(".")[:2])
    urls = [
        f"https://download.qt.io/archive/qt/{series}/{qt}/single/qt-everywhere-src-{qt}.tar.xz",
        f"https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-{pyside}-src/"
        f"pyside-setup-everywhere-src-{pyside}.tar.xz",
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        sources = list(pool.map(lambda url: download_source(url, output / url.rsplit("/", 1)[1]), urls))
    for item in sources:
        print(f"Verified source: {item['file']}", flush=True)
        copy_notices(output / item["file"], notices / "upstream")
    for filename in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copyfile(ROOT / filename, notices / filename)
    shutil.copyfile(ROOT / "docs/rebuilding.md", notices / "REBUILDING.md")
    # CPython's full licence includes the notices for bundled standard libraries.
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, notices / "Python-LICENSE.txt")
    else:
        url = f"https://raw.githubusercontent.com/python/cpython/v{platform.python_version()}/LICENSE"
        with urlopen(url, timeout=60) as response:
            (notices / "Python-LICENSE.txt").write_bytes(response.read())
    builder = metadata.distribution("pyinstaller")
    copying = next(path for path in builder.files if path.name == "COPYING.txt")
    shutil.copyfile(builder.locate_file(copying), notices / "PyInstaller-COPYING.txt")
    versions = {name: metadata.version(name) for name in
                ("PySide6", "PySide6_Essentials", "PySide6_Addons", "shiboken6", "pyinstaller")}
    manifest = {"application": __version__, "python": platform.python_version(),
                "platform": platform.platform(), "qt": qt, "packages": versions,
                "sources": sources, "library_modifications": "None",
                "application_source": f"https://github.com/DesignStack/Dovecot-Mailbox-Viewer/tree/v{__version__}"}
    (notices / "DEPENDENCIES.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (notices / "requirements-build.txt").write_text(
        "".join(f"{name}=={value}\n" for name, value in versions.items()), encoding="utf-8")
    (notices / "README.txt").write_text(
        "Dovecot Mailbox Viewer uses Qt, PySide6 and Shiboken6 under LGPLv3.\n"
        "The original application is MIT-licensed. Dependency licences remain separate.\n"
        "See LICENSE, THIRD_PARTY_NOTICES.md, REBUILDING.md and the upstream licence texts.\n"
        "DEPENDENCIES.json records the exact versions and source-archive checksums.\n"
        "The complete unmodified Qt and PySide/Shiboken source archives are separate assets\n"
        f"at https://github.com/DesignStack/Dovecot-Mailbox-Viewer/releases/tag/v{__version__}\n"
        "You may modify/rebuild the libraries and run the app with your replacements.\n"
        "Reverse engineering for debugging those modifications is not restricted.\n"
        "The upstream notice collection includes optional Qt modules not used by this app.\n",
        encoding="utf-8")
    with zipfile.ZipFile(output / "Third-party-notices.zip", "w", zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(notices.rglob("*")):
            if path.is_file():
                bundle.write(path, path.relative_to(notices).as_posix())


def finalise():
    """Record the bundled file inventory and checksum every release asset."""
    import ast
    # PyInstaller's TOC is a Python literal, not executable code.
    linux = sys.platform.startswith("linux")
    target = "Linux" if linux else "Windows"
    toc = ROOT / f"build/Dovecot-Mailbox-Viewer-{target}/Analysis-00.toc"
    analysis = ast.literal_eval(toc.read_text(encoding="utf-8"))
    binaries = []
    def walk(value):
        if isinstance(value, (list, tuple)):
            if len(value) == 3 and value[2] in ("BINARY", "EXTENSION") and isinstance(value[0], str):
                binaries.append(value[0])
            else:
                for child in value:
                    walk(child)
    walk(analysis)
    if not binaries:
        raise ValueError("Bundled binary inventory is empty")
    output = ROOT / "dist"
    inventory = "Bundled-files-Linux.txt" if linux else "Bundled-files.txt"
    (output / inventory).write_text("\n".join(sorted(set(binaries))) + "\n", encoding="utf-8")
    manifest = json.loads((ROOT / "build/notices/DEPENDENCIES.json").read_text(encoding="utf-8"))
    if linux:
        (output / "Third-party-notices.zip").replace(output / "Third-party-notices-Linux.zip")
    names = (["Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage", "Third-party-notices-Linux.zip", inventory]
             if linux else ["Dovecot-Mailbox-Viewer-Windows.exe", "Third-party-notices.zip", inventory])
    names += [item["file"] for item in manifest["sources"]]
    checksums = "".join(f"{digest(output / name)}  {name}\n" for name in names)
    (output / "SHA256SUMS.txt").write_text(checksums, encoding="ascii")


if __name__ == "__main__":
    {"prepare": prepare, "finalise": finalise}[sys.argv[1]]()
