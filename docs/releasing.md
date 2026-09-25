# Publishing a version

`viewer/version.py` is the single source for the version number. Use
**MAJOR.MINOR.PATCH**: patch for fixes, minor for features, major for changes
that break compatibility. The `0.x` series is the initial development series.
The first numbered release is `0.1.0`; earlier builds were unversioned.

1. Update `__version__` in `viewer/version.py`.
2. Add a dated `## [x.y.z] - YYYY-MM-DD` section to `CHANGELOG.md` with the changes
   and known limitations. Keep an empty `Unreleased` section for upcoming work.
3. Commit the code, version and changelog together to `main`.
4. **Actions → Build Windows app** tests and builds a Windows x64 executable,
   verifies its Windows version metadata and launches it from a clean folder.
5. Only after those checks pass, the workflow creates `vx.y.z` and publishes a
   GitHub Release with the EXE, SHA-256 checksum and notes from the changelog.

The workflow can also be run manually on `main`. If that version is already
published, it leaves the release untouched. Build artifacts remain available
for development commits. To publish changed code, increment the version.
Never move a published version tag to a different commit or replace its EXE.

Uploads happen in a draft release. If uploading fails, rerun the failed workflow
for the same commit to resume that draft. A draft from a different commit must
be reviewed before publishing; the script deliberately stops in that case.

The workflow uses GitHub's temporary `GITHUB_TOKEN` with `contents: write` to
publish the release. No personal access token or stored publishing secret is needed.

The stable download link is:

https://github.com/DesignStack/Dovecot-Mailbox-Viewer/releases/latest/download/Dovecot-Mailbox-Viewer-Windows.exe

GitHub keeps each release's historical download under its own tag. The app's
local search cache is separate from the executable, so replacing the EXE does
not discard the cache or modify the original backup.
