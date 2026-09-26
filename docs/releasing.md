# Publishing a version

`viewer/version.py` is the single source for the version number. Use
**MAJOR.MINOR.PATCH**: patch for fixes, minor for features, major for changes
that break compatibility. The `0.x` series is the initial development series.
The first numbered release is `0.1.0`; earlier builds were unversioned.

1. Update `__version__` in `viewer/version.py`.
2. Add a dated `## [x.y.z] - YYYY-MM-DD` section to `CHANGELOG.md` with the changes
   and known limitations. Keep an empty `Unreleased` section for upcoming work.
3. Commit the code, version and changelog together to `main`.
4. **Actions → Build Windows and Linux apps** builds both packages and runs the
   standalone checks, including clean Debian/Ubuntu AppImage checks.
5. Only after all platform checks pass, the workflow creates `vx.y.z` and publishes a
   GitHub Release with the EXE, AppImage, sources, notices, checksums and release notes.

The workflow can also be run manually on `main`. If that version is already
published, it leaves the release untouched. Build artifacts remain available
for development commits. To publish changed code, increment the version.
Never move a published version tag to a different commit or replace its EXE.

Before publishing binaries, review [third-party software](../THIRD_PARTY_NOTICES.md).
Include the application licence and the exact dependency licence texts/notices,
record bundled versions, and fulfil the corresponding-source and rebuild/replacement
requirements of the LGPL components. Adding a licence to this repository does not
retroactively add those materials to an already published executable.

From v0.4.1, `scripts/release_assets.py` verifies upstream source checksums,
embeds notices in the EXE and prepares the matching Qt and PySide/Shiboken source
archives as separate release assets. It records exact versions and the native
file inventory. The publisher checks all asset hashes and uploads every file to
the draft before making it public. Allow additional download time and disk space
for Qt's full source archive (around 1 GB). See [rebuilding](rebuilding.md).

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
