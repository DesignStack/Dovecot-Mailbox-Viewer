# Dovecot Mailbox Viewer

A local, read-only Windows desktop viewer for JetBackup/cPanel **mdbox** mail backups. Open a `.tar.gz` archive or the extracted `backup/email` directory. Mailbox content stays on your computer; the application makes a separate local cache for searching and previews.

## Download for Windows

**[Download the Windows app (.exe)](https://github.com/DesignStack/Dovecot-Mailbox-Viewer/releases/latest/download/Dovecot-Mailbox-Viewer-Windows.exe)**

For Windows 10/11, 64-bit. Save the file and double-click it. There is no installer,
no ZIP to extract and no Python installation needed. All required application
files are bundled inside the EXE; there is no separate `_internal` folder to copy.
It may take a few seconds to open while it automatically unpacks support files
into a temporary folder, which is normally removed when the app closes.

Choose **Open Archive** or **Open Folder** in the welcome guide to get started.
You can also drag a `.tar.gz`, `.tgz` or extracted folder onto the app or welcome
window. **File → Recent backups** and the welcome screen let you reopen recent
backups. The history stays on your computer; **Clear recent backups** removes
only the history, not the original files or search caches.

To update, close the app and replace your old EXE with the new download. Your
original backup and reusable search cache are kept separately.
**Help → Check for updates** checks GitHub only when you request it and opens
the release page if a newer version is available. It does not send your emails,
account names or backup paths, and does not download or install updates automatically.

[Release notes and older downloads](https://github.com/DesignStack/Dovecot-Mailbox-Viewer/releases)
· [Changelog](CHANGELOG.md)

Check the installed version in **Help → About** or the EXE's Windows **Properties
→ Details**. Each release also includes `SHA256SUMS.txt` for checking its download.
The **Source code** ZIP links on release pages are for developers.

![Dovecot Mailbox Viewer welcome screen with Open Archive and Open Folder choices](docs/images/welcome-screen.png)

## Export, print and save as PDF

- **One email:** use the email's three-dot menu or File → Export email as .eml.
- **Several emails:** hold Ctrl or Shift to select messages, then choose
  **File → Export selected emails**. Ctrl+A selects the emails currently listed.
- **A complete folder:** select it on the left, then choose **File → Export entire
  folder**. With All mail selected, the action becomes **Export all mail**.
  These exports ignore search filters and the list's 5,000-message display limit.
  Wait for indexing to finish before exporting a complete folder.

Bulk exports create a new `Mail-export-…` folder at your chosen destination,
with a separate subfolder for each mailbox folder and the original `.eml` bytes.
Filenames are made safe for Windows. Existing exports are never overwritten.
The progress window offers Cancel; completed emails are kept if you cancel.

Use **Save email as PDF** or **Print email** from File or the email's three-dot
menu. **Ctrl+P** opens the print dialogue. The output includes the subject,
sender, recipients, date, attachment names and email body. Attachments themselves
are saved separately; they are not embedded in the PDF. Printing does not download
remote images: use Download images first if you want them included.

**File → Exit** or **Ctrl+Q** closes the app. Finish indexing or finish/cancel
any running export first.

## Run from source

Install Python 3.11+ on Windows, then in PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m viewer.app
```

On startup, a welcome guide explains the two ways to open your mailbox:

- **Open Archive**: choose the `.tar.gz` or `.tgz` backup file. There is no need to extract it first.
- **Open Folder**: choose the extracted backup folder, such as `backup/email`.

The guide explains how emails appear as the backup is prepared and that your original
backup is never changed. Cancelling a file picker keeps the guide open. **Not now**
closes the guide and leaves the same choices on the empty screen. Reopen it with
**Help → Getting started**. It also returns when the current cache is cleared or an
import fails before any mail is loaded.

Choose **Open archive** or **Open folder**. A background task scans the mailbox and builds a local SQLite search index. Select folders on the left, messages in the middle, and read the message on the right. Search terms match subject, sender, recipients and plain-text message content. Use **All mail** to search across folders. The search options button adds sender, subject, date and attachment filters. Attachments also appear as cards below the message date, showing a file-type icon, filename and size. Click a card to save that file. Use the **three-dot menu** at the top right of an email to save attachments, export the original `.eml`, or download images. The top toolbar has Search options followed by Open archive and Open folder, all with matching outline icons. The app opens one mailbox at a time; a multi-account backup asks which mailbox to open. **Clear cache** removes derived copies and search indexes from your computer.

HTML emails are displayed in the preview. Remote images are blocked by default;
the message banner offers **Download images** for that message only. Each request has a
20-second total deadline, including redirects and connection setup. **Stop** cancels
outstanding requests; successfully loaded images stay visible. If some fail, the banner
shows how many loaded and offers **Try again**. Inline
images stored inside the email are displayed locally. External links open only
after confirmation. Qt's HTML renderer supports common email layouts, but
complex CSS may look different from a browser.

The progress bar shows how much of the selected mailbox's `m.*` storage has been
processed, with a running message count. If an import fails, use **File → Open
diagnostic log**. The log lives at
`%LOCALAPPDATA%\DesignStack\DovecotMailboxViewer\viewer.log`; it records paths,
counts and error details, but does not intentionally log email bodies. Check
the log before sharing it, since file paths can contain account names.

## Build the Windows application

On Windows, right-click `build-windows.ps1` and run it in PowerShell, or run
`powershell -ExecutionPolicy Bypass -File .\build-windows.ps1` from the project
folder. This installs build dependencies, runs the tests and builds
`dist/Dovecot-Mailbox-Viewer-Windows.exe` using PyInstaller's one-file mode.
It embeds the app version in Windows file properties, checks the actual EXE
from a clean folder and writes `dist/SHA256SUMS.txt`. No supporting folder is
needed beside this executable.

**Actions → Build Windows app → Run workflow** also builds it on Windows.
New versions on `main` automatically become public GitHub Releases after all
checks pass. The workflow never overwrites an already published version.
See [Publishing a version](docs/releasing.md) for the version and changelog steps.
Development builds are available as Actions artifacts; end users should use the
direct **Download the Windows app** link above.

Older, unversioned builds used a ZIP containing an EXE and `_internal` folder.
That folder is required by those older builds. The new portable EXE includes
those dependencies and can be kept on its own.

## Scope and limitations

- The source archive and extracted mailbox are never changed. Search caches and logs are stored in `%LOCALAPPDATA%/DesignStack/DovecotMailboxViewer`. The portable EXE also unpacks its runtime into a temporary folder; exports are written only where you choose to save them.
- Recognises the dbox `m.*` container used by the supplied backup, including gzip-compressed message records and the `B<mailbox>` metadata. It discovers folders even if empty.
- `B` metadata labels the mailbox in this sample. Supported transaction logs supply read/deleted flags, but main index snapshots and map indexes are not yet supported, so it does not promise authoritative state or folder placement for every Dovecot version. Treat ambiguous records as review material, not an exact live-mailbox reconstruction.
- HTML messages render locally with external resources blocked by default. Loading external images is a per-message choice. Failed downloads keep the notice visible with a **Try again** link and diagnostic logging. Attachments are never run automatically.
- Archives are processed one storage file at a time; a large archive requires free disk space for the private search database. Initial indexing may take time. Tar paths are never extracted to arbitrary destinations.
- This is a first version verified with the supplied eight-message JetBackup sample; test against more backups before using it as a general-purpose forensic viewer.

## Code layout

- `viewer/mdbox.py` scans tar/folder inputs and yields validated message bytes and mailbox metadata.
- `viewer/catalog.py` builds and queries a private SQLite catalogue and full-text index.
- `viewer/app.py` contains the Qt interface and background import worker.
- `viewer/version.py` is the single source for the app and release version.
- `viewer/exporting.py` streams complete or selected email exports on a worker thread.
- `viewer/printing.py` prepares safe email printouts and PDF exports.
- `viewer/recent.py` keeps local backup history and validates dropped paths.
- `viewer/updates.py` checks GitHub releases only on request.
- `scripts/prepare_release.py` generates Windows version metadata and release notes.
- `scripts/publish_release.py` publishes tested binaries through GitHub Releases.
- `viewer/welcome.py` provides the startup guide and empty-state opening choices.
- `viewer/html_preview.py` handles safe HTML previews and the optional image requests.
- `tests/test_mdbox.py` tests dbox records without using private email fixtures.
- `viewer/icons.py` provides the original single-colour outline icons; `viewer/mail_widgets.py` paints folder and message rows.
- `tests/test_gui.py` checks the background import, GUI thread handoff and export action.
- `tests/test_image_downloads.py` uses a local HTTP server to test downloads, encoded URLs, redirects, repainting, retries and cancellation.

Do not commit client email archives or generated cache folders to GitHub.

### Progressive viewing and cache

Messages appear as storage records are parsed and committed, while indexing continues.
The percentage describes bytes read from the mailbox storage files; reading a compressed
archive and discovering accounts may take time before this progress starts. A completed
index opens immediately on the next launch if the source path, size and modification
time match. **File → Clear current cache** removes the local catalogue and causes a
rebuild next time the account is opened. For extracted folders, changes to any file
invalidate the cache. Email content and search terms remain in a private local SQLite
cache; delete it via the menu if the backup contains sensitive mail.

The app reads supported Dovecot transaction logs and matches GUIDs with stored messages
to display seen, deleted and expunged state. A filled circle marks known unread mail.
When an index cannot establish a message's status, the app leaves it unmarked rather
than claiming it is unread. Main index snapshots and older transaction log formats are
not yet supported; those backups may show unknown flags. Expunged messages still present
in storage are displayed with an Expunged label for recovery. Export the selected email
with **File → Export email as .eml…** or the email’s three-dot menu; it writes the
original message bytes.


## About the author

Created by [DesignStack](https://designstack.co.uk), a web design agency in Weymouth, Dorset.

Questions or feedback? [Contact the author](mailto:hello@designstack.co.uk). You can also use **Help → Contact author** or **Help → About** in the app.
