# Rebuilding with replacement libraries

The portable EXE contains dynamically loaded Qt and PySide/Shiboken libraries.
You can modify these libraries, rebuild the application and run your modified
version. No signature, activation service or integrity check blocks this.
Reverse engineering for debugging library modifications is permitted.

Each release from v0.4.1 includes `Third-party-notices.zip`, with exact package
versions in `DEPENDENCIES.json`, licence texts and `requirements-build.txt`.
The complete, unmodified Qt and PySide/Shiboken source archives are separate
downloads on the same release page. `SHA256SUMS.txt` covers all release assets.
`Bundled-files.txt` lists native files included by PyInstaller. Qt's full source
archive and notice collection also contain optional modules this app does not use.

1. Download the application source for the release tag and extract it.
2. Install the Python version in `DEPENDENCIES.json` on Windows and create a
   virtual environment: `py -m venv .venv`.
3. Activate it with `.venv\Scripts\Activate.ps1`. Install the recorded packages
   with `python -m pip install -r requirements-build.txt` (use the path to the
   file from the notices ZIP).
4. To modify Qt, extract the supplied Qt source archive and follow its included
   build instructions (`qtbase/README.md` and `configure -help`) with Microsoft's
   C++ build tools, CMake and Ninja. Build shared libraries for x64. The app uses
   Qt Core, Gui, Widgets, Network, Svg and PrintSupport. Include the relevant
   image-format, platform and TLS plugins.
5. Extract the PySide/Shiboken source archive and follow its `README.md` and
   `setup.py --help` instructions. Build matching wheels against your Qt build
   using its `qtpaths` executable, then install those wheels into the environment.
   Replace any Qt dependencies/plugins with those from the same modified build.
6. Run `python -m viewer.app`. This runs the full viewer directly with the
   replacement libraries. Run `python -m unittest discover -s tests -v` to check it.
7. To make a new portable EXE without reinstalling stock libraries, run
   `python scripts/prepare_release.py`, then the command below. The resulting
   `dist/Dovecot-Mailbox-Viewer-Windows.exe` can be run normally.

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed --noupx `
  --name Dovecot-Mailbox-Viewer-Windows --icon assets/mailbox.ico `
  --version-file build/windows-version.txt --paths . viewer/app.py
```

For redistribution, include notices and corresponding source for your modified
libraries as required by their licences. The normal `build-windows.ps1` script
installs stock dependencies, so use the manual steps above for replacements.
The upstream archives include their own build documentation and licence terms.
