# DesignStack Dovecot Mailbox Viewer

A local, read-only Windows desktop viewer for JetBackup/cPanel **mdbox** mail backups. Open a `.tar.gz` archive or the extracted `backup/email` directory. Mail stays on your computer; the application makes a separate local cache for searching and previews.

## Run from source

Install Python 3.11+ on Windows, then in PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m viewer.app
```

Choose **Open archive** or **Open folder**. A background task scans the mailbox and builds a local SQLite search index. Select folders on the left, messages in the middle, and read the message on the right. Search terms match subject, sender, recipients and plain-text message content. Use **All folders** to search across folders. Save attachments with the button in the preview. **Clear cache** removes derived copies and search indexes from your computer.

## Build the Windows application

On Windows, right-click `build-windows.ps1` and run it in PowerShell, or run
`powershell -ExecutionPolicy Bypass -File .\build-windows.ps1` from the project
folder. This installs build dependencies, runs the tests, and writes
`DesignStack-Dovecot-Mailbox-Viewer-Windows.zip`. Unzip the output and launch
`DesignStack Dovecot Mailbox Viewer.exe`; the other files in its folder are
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
- HTML-only messages are shown as extracted text, so remote tracking images and active content cannot load. Attachments are never run automatically.
- Archives are processed one storage file at a time; a large archive requires free disk space for the extracted `m.*` storage files and search database. Initial indexing may take time. Tar paths are never extracted to arbitrary destinations.
- This is a first version verified with the supplied eight-message JetBackup sample; test against more backups before using it as a general-purpose forensic viewer.

## Code layout

- `viewer/mdbox.py` scans tar/folder inputs and yields validated message bytes and mailbox metadata.
- `viewer/catalog.py` builds and queries a private SQLite catalogue and full-text index.
- `viewer/app.py` contains the Qt interface and background import worker.
- `tests/test_mdbox.py` tests dbox records without using private email fixtures.

Do not commit client email archives or generated cache folders to GitHub.
