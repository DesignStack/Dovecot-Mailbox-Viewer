"""Publish tested Windows binaries from Actions, without replacing public releases.

Create a draft first, upload every verified asset, then make the release public. A failed
upload leaves a draft which the same commit can safely resume on a workflow retry.
The short-lived Actions token is used only here and never written to disk.
"""

from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import quote
import hashlib
import json
import os
import sys
import zipfile
from contextlib import nullcontext

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from viewer.version import __version__


def request(url, token, *, method="GET", data=None, content_type="application/json"):
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "Content-Type": content_type, "User-Agent": "Dovecot-Mailbox-Viewer-release"}
    # Stream large source archives; do not read a gigabyte into memory per upload.
    if isinstance(data, Path):
        headers["Content-Length"] = str(data.stat().st_size)
    payload = json.dumps(data).encode() if isinstance(data, dict) else data
    with data.open("rb") if isinstance(data, Path) else nullcontext(payload) as body:
        with urlopen(Request(url, data=body, headers=headers, method=method), timeout=300) as response:
            result = response.read()
            return json.loads(result) if result else None


def verified_assets(root):
    """No partial or altered licence/source bundle may be published."""
    output = root / "dist"
    sources = []
    for name in ("Third-party-notices.zip", "Third-party-notices-Linux.zip"):
        with zipfile.ZipFile(output / name) as notices:
            manifest = json.loads(notices.read("DEPENDENCIES.json"))
        if manifest["application"] != __version__:
            raise RuntimeError("The dependency manifest is for a different application version")
        if not any(s["file"].startswith("qt-everywhere-src-") for s in manifest["sources"]) \
                or not any(s["file"].startswith("pyside-setup-everywhere-src-") for s in manifest["sources"]):
            raise RuntimeError("Both Qt and PySide/Shiboken source archives are required")
        sources.extend(manifest["sources"])
    expected = {"Dovecot-Mailbox-Viewer-Windows.exe", "Third-party-notices.zip", "Bundled-files.txt",
                "Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage", "Third-party-notices-Linux.zip", "Bundled-files-Linux.txt"}
    expected.update(item["file"] for item in sources)
    listed = {}
    for line in (output / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        checksum, filename = line.split("  ", 1)
        if filename in listed or Path(filename).name != filename or "/" in filename or "\\" in filename:
            raise RuntimeError("Invalid or duplicate release asset name")
        listed[filename] = checksum
    if set(listed) != expected:
        raise RuntimeError("Release assets do not match the required checksums")
    for filename, checksum in listed.items():
        with (output / filename).open("rb") as stream:
            actual = hashlib.file_digest(stream, "sha256").hexdigest()
        if actual != checksum:
            raise RuntimeError(f"Release checksum mismatch: {filename}")
    if any(listed[source["file"]] != source["sha256"] for source in sources):
        raise RuntimeError("Library source differs from its verified upstream archive")
    return [output / filename for filename in listed] + [output / "SHA256SUMS.txt"]


def publish():
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise RuntimeError("Releases must be published from main")
    token = os.environ["GH_TOKEN"]
    repository = os.environ["GITHUB_REPOSITORY"]
    commit = os.environ["GITHUB_SHA"]
    api = f"https://api.github.com/repos/{repository}"
    tag = f"v{__version__}"
    try:
        release = request(f"{api}/releases/tags/{tag}", token)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        release = None
        # The tag endpoint may omit drafts, but authenticated release lists include them.
        page = 1
        while release is None:
            releases = request(f"{api}/releases?per_page=100&page={page}", token)
            release = next((item for item in releases if item["tag_name"] == tag), None)
            if len(releases) < 100:
                break
            page += 1
    if release and not release["draft"]:
        print(f"{tag} is already published; its files will not be changed. Bump the version for a new release.")
        return
    if release and release["target_commitish"] != commit:
        raise RuntimeError(f"An unfinished {tag} draft belongs to another commit; review it before publishing")

    for platform in ("Windows", "Linux"):
        report = json.loads((ROOT / f"build/smoke-test-{platform}.json").read_text(encoding="utf-8"))
        if not report.get("ok") or not report.get("frozen") or report.get("version") != __version__:
            raise RuntimeError(f"A passing {platform} packaged-app check is required before publishing")
    assets = verified_assets(ROOT)

    if release is None:
        release = request(f"{api}/releases", token, method="POST", data={
            "tag_name": tag, "target_commitish": commit,
            "name": f"Dovecot Mailbox Viewer {tag}", "draft": True,
            "prerelease": False, "body": (ROOT / "build/release-notes.md").read_text(encoding="utf-8"),
        })
    upload_url = release["upload_url"].split("{", 1)[0]
    if not upload_url.startswith(f"https://uploads.github.com/repos/{repository}/releases/"):
        raise RuntimeError("Unexpected release upload URL")
    for path in assets:
        mime = {".exe": "application/vnd.microsoft.portable-executable", ".zip": "application/zip",
                ".txt": "text/plain", ".xz": "application/x-xz", ".gz": "application/gzip",
                ".AppImage": "application/octet-stream"}[path.suffix]
        # Only incomplete drafts can reach this code; public assets are immutable here.
        for asset in release.get("assets", []):
            if asset["name"] == path.name:
                request(f"{api}/releases/assets/{asset['id']}", token, method="DELETE")
        request(f"{upload_url}?name={quote(path.name)}", token, method="POST",
                data=path, content_type=mime)
    published = request(f"{api}/releases/{release['id']}", token, method="PATCH",
                        data={"draft": False, "make_latest": "true"})
    print(f"Published {published['html_url']}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(f"### {tag} is ready\n\n[Download the Windows app]({published['html_url']})\n")


if __name__ == "__main__":
    publish()
