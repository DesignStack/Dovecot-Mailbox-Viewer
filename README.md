# Dovecot Mailbox Viewer

A local, read-only Windows desktop viewer for JetBackup/cPanel **mdbox** mail backups. Open a `.tar.gz` archive or the extracted `backup/email` directory. Mail stays on your computer; the application makes a separate local cache for searching and previews.

## Run from source

Install Python 3.11+ on Windows, then in PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m viewer.app
```

Choose **Open archive** or **Open folder**. A background task scans the mailbox and builds a local SQLite search index. Select folders on the left, messages in the middle, and read the message on the right. Search terms match subject, sender, recipients and plain-text message content. Use **All mail** to search across folders. The search options button adds sender, subject, date and attachment filters. Save attachments with the button in the preview. **Clear cache** removes derived copies and search indexes from your computer.

HTML emails are displayed in the preview. Remote images are blocked by default;
the message banner offers **Download images** for that message only. Inline
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
folder. This installs build dependencies, runs the tests, and writes
`Dovecot-Mailbox-Viewer-Windows.zip`. Unzip the output and launch
`Dovecot Mailbox Viewer.exe`; the other files in its folder are
required. Python is not needed on the destination PC.

Alternatively, place the project at the root of a GitHub repository and run
**Actions → Build Windows app → Run workflow**. Download the Windows application
from the workflow's **Artifacts** section. The workflow needs Actions enabled
for the repository. Test the resulting app on Windows with your sample archive
before distributing it to others.

## Scope and limitations

- The source archive and extracted mailbox are never changed. The app writes only to `%LOCALAPPDATA%/DesignStack/DovecotMailboxViewer`.
- Recognises the dbox `m.*` container used by the supplied backup, including gzip-compressed message records and the `B<mailbox>` metadata. It discovers folders even if empty.
- `B` metadata labels the mailbox in this sample. This first release does **not** decode Dovecot's binary mailbox/map indexes, so it does not promise authoritative deletion state, read/unread flags, or accurate folder placement for every Dovecot version. Treat ambiguous records as review material, not an exact live-mailbox reconstruction.
- HTML messages render locally with external resources blocked by default. Loading external images is a per-message choice. Attachments are never run automatically.
- Archives are processed one storage file at a time; a large archive requires free disk space for the private search database. Initial indexing may take time. Tar paths are never extracted to arbitrary destinations.
- This is a first version verified with the supplied eight-message JetBackup sample; test against more backups before using it as a general-purpose forensic viewer.

## Code layout

- `viewer/mdbox.py` scans tar/folder inputs and yields validated message bytes and mailbox metadata.
- `viewer/catalog.py` builds and queries a private SQLite catalogue and full-text index.
- `viewer/app.py` contains the Qt interface and background import worker.
- `viewer/html_preview.py` handles safe HTML previews and the optional image requests.
- `tests/test_mdbox.py` tests dbox records without using private email fixtures.
- `tests/test_gui.py` checks the background import and GUI thread handoff.

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
with **File → Export selected email as .eml…** or the preview button; it writes the
original message bytes.
