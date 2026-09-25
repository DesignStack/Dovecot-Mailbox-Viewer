# Third-party software

The [MIT licence](LICENSE) covers DesignStack's original application code,
documentation and original outline icons. It does not replace the licences of
dependencies or grant rights to third-party email content, logos or trademarks
visible in example screenshots.

## Runtime and build components

| Component | Purpose | Licence information |
| --- | --- | --- |
| Python | Interpreter and standard library | [Python Software Foundation licence and bundled notices](https://docs.python.org/3/license.html) |
| PySide6 / Shiboken6 (Qt for Python) | Python bindings for the desktop interface | [LGPLv3/GPLv3 and commercial licensing options](https://doc.qt.io/qtforpython-6/); the open-source LGPLv3 option is used for the applicable components in this project |
| Qt | Widgets, HTML/text rendering, networking, SVG icons and printing | [Qt licensing and third-party notices](https://doc.qt.io/qt-6/licensing.html); the applicable LGPLv3 components retain their upstream terms |
| PyInstaller | Builds the portable Windows executable | [GPLv2 with a distribution exception, plus Apache-2.0 for specified files](https://pyinstaller.org/en/stable/license.html) |

Copyright in these components belongs to their respective authors. Python and
Qt can contain additional libraries with their own notices. Preserve the full
licence texts and acknowledgements supplied with the exact versions distributed.
This table is an overview, not a replacement for those texts or a complete
inventory of a particular executable.

## Building and redistributing

The repository contains the application source and `build-windows.ps1`, which
can rebuild the executable with compatible replacement dependencies. See
[Run from source](README.md#run-from-source) and
[Build the Windows application](README.md#build-the-windows-application).
No application licence term restricts modification of LGPL libraries or reverse
engineering needed to debug those modifications.

When distributing a binary, include the application MIT licence and the applicable
dependency licence texts and copyright notices. For LGPL components, retain the
user's ability to replace/rebuild them and provide the corresponding library source
and installation information through a method permitted by their licences.
An application-source ZIP alone does not contain Qt's or PySide's source.

Upstream source is available from [Qt](https://code.qt.io/cgit/qt/) and
[Qt for Python](https://code.qt.io/cgit/pyside/pyside-setup.git/).
Record the exact component versions used for each binary and preserve access to
their corresponding source. These links are starting points, not a release-specific
source bundle or a written source offer.

See [Qt's LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations)
for the upstream distribution guidance. Check the modules actually included in a
build: not every Qt module is available under LGPL.

## Independent project

Dovecot Mailbox Viewer is an independent DesignStack project. It is not an
official Dovecot, JetBackup or cPanel product. Those names identify the backup
formats and products with which the viewer is intended to work.
