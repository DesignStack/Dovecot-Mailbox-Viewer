"""Publish tested Windows binaries from Actions, without replacing public releases.

Create a draft first, upload both files, then make the release public. A failed
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from viewer.version import __version__


def request(url, token, *, method="GET", data=None, content_type="application/json"):
    payload = json.dumps(data).encode() if isinstance(data, dict) else data
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "Content-Type": content_type, "User-Agent": "Dovecot-Mailbox-Viewer-release"}
    with urlopen(Request(url, data=payload, headers=headers, method=method), timeout=120) as response:
        body = response.read()
        return json.loads(body) if body else None


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

    executable = ROOT / "dist/Dovecot-Mailbox-Viewer-Windows.exe"
    checksums = ROOT / "dist/SHA256SUMS.txt"
    report = json.loads((ROOT / "build/smoke-test.json").read_text(encoding="utf-8"))
    if not report.get("ok") or not report.get("frozen") or report.get("version") != __version__:
        raise RuntimeError("A passing packaged-app check is required before publishing")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    if checksums.read_text(encoding="ascii").strip() != f"{digest}  {executable.name}":
        raise RuntimeError("The executable no longer matches its checksum")

    if release is None:
        release = request(f"{api}/releases", token, method="POST", data={
            "tag_name": tag, "target_commitish": commit,
            "name": f"Dovecot Mailbox Viewer {tag}", "draft": True,
            "prerelease": False, "body": (ROOT / "build/release-notes.md").read_text(encoding="utf-8"),
        })
    upload_url = release["upload_url"].split("{", 1)[0]
    if not upload_url.startswith(f"https://uploads.github.com/repos/{repository}/releases/"):
        raise RuntimeError("Unexpected release upload URL")
    for path, mime in ((executable, "application/vnd.microsoft.portable-executable"),
                       (checksums, "text/plain")):
        # Only incomplete drafts can reach this code; public assets are immutable here.
        for asset in release.get("assets", []):
            if asset["name"] == path.name:
                request(f"{api}/releases/assets/{asset['id']}", token, method="DELETE")
        request(f"{upload_url}?name={quote(path.name)}", token, method="POST",
                data=path.read_bytes(), content_type=mime)
    published = request(f"{api}/releases/{release['id']}", token, method="PATCH",
                        data={"draft": False, "make_latest": "true"})
    print(f"Published {published['html_url']}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
            summary.write(f"### {tag} is ready\n\n[Download the Windows app]({published['html_url']})\n")


if __name__ == "__main__":
    publish()
