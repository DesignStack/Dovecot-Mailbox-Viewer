# Linux AppImage

Download `Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage` from the GitHub Release.
This contains Python, Qt and the viewer. Mark it executable in your file manager,
or run `chmod +x Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage`, then launch it.
Do not run the viewer with sudo.

The first release targets x86-64 desktop systems with glibc 2.35 or newer, such
as Ubuntu 22.04/24.04 and Debian 13. It uses X11, including XWayland on Wayland
desktops. It does not provide ARM or 32-bit binaries. Standard desktop libraries
(including the system glibc, graphics drivers, fontconfig, GLib, D-Bus and X11)
come from your distribution. It is not a headless-server package.

The modern type-2 runtime embeds its FUSE support. If your system disallows FUSE
mounts, use the fallback; it unpacks into a temporary directory automatically:

```bash
./Dovecot-Mailbox-Viewer-Linux-x86_64.AppImage --appimage-extract-and-run
```

Mail backups remain read-only. Caches and diagnostic logs normally live in
`~/.cache/DesignStack/DovecotMailboxViewer`. To update, close the app and replace
the AppImage, then make the new file executable. Your cache stays separate.
Help → Check for updates opens the release page and checks for the Linux asset.

## Build and verification

GitHub builds on Ubuntu 22.04 x86-64, runs the test suite, then checks the actual
AppImage under Xvfb. Separate clean Ubuntu 24.04 and Debian 13 containers run the
downloaded artifact without installing Python or PySide6. These checks cover
startup, the welcome screen, icons, TLS availability, mixed compressed/plain
mailbox import, HTML preview, search, PDF and `.eml` export. They do not exercise
every desktop theme, printer or hardware configuration.

On an Ubuntu 22.04 build machine, install Python 3.11+ with venv support and the
desktop dependencies listed in `.github/workflows/windows-build.yml`, then run
`bash build-linux.sh`. Output is in `dist/`. The build downloads pinned AppImage
tools and verifies their SHA-256 hashes; a changed upstream continuous asset
will stop the build until the pin is reviewed and updated.

Library sources and full notices accompany each release. `Third-party-notices-Linux.zip`
records the dependencies and AppImage runtime revision. `Bundled-files-Linux.txt`
lists native files. To modify the application or libraries, use the supplied
source, install your replacement bindings, run the PyInstaller Linux spec and
`python scripts/build_appimage.py package`. The runtime sources include rebuild
instructions and the libfuse patch; pass a replacement runtime to appimagetool
with `--runtime-file`. There is no signature or activation requirement preventing
you from running a modified version.
