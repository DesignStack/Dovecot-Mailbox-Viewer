# Changelog

Versions use **MAJOR.MINOR.PATCH**. Every published version has a GitHub Release
with its own notes and Windows download. Earlier development builds were unversioned.

## [Unreleased]

## [0.3.0] - 2026-09-25

### Added

- Date, sender and subject sorting, plus pagination with 100/200/500 emails per
  page so every message is browsable beyond the previous 5,000-message limit.
- Optional conversation view using Message-ID/References/In-Reply-To relationships,
  with chronological navigation across folders and complete conversation export.
- Search highlighting in message rows and the reader; Find in email (Ctrl+G),
  next/previous matches (F3/Shift+F3), HTML/plain-text selection and reading zoom.
- Original headers window with Copy all, and Save all attachments with collision-safe
  filenames in a fresh output folder.
- Settings for reading, sorting, conversations, page size, highlighting, local
  recent-backup history and remembering window/panel sizes.
- Cache manager showing saved indexes, source paths, disk usage and completion state,
  with selected-cache removal that preserves original backups and other app files.
- Support for validated little-endian Dovecot 7.x GUID index snapshots, 1.0–1.3
  transaction logs, contiguous rotated logs, batched records and protected expunges.

### Changed

- Discovery, cache validation and indexing run off the GUI thread, with immediate
  activity feedback, cancellation and safe stopping when the window is closed.
- Compressed archives are read in physical order, avoiding repeated gzip seeks.
  Recently opened unchanged archives can skip discovery and reuse their saved index.
- Known Dovecot GUID mappings take precedence over stored fallback folder metadata.
  Unsupported/incomplete/ambiguous index state stays unknown instead of being guessed.
- Cancelled imports keep committed messages available, but cannot be mistaken for
  complete caches or used for whole-folder export until rebuilt.
- Older caches rebuild once to add normalised timestamps and conversation links.
- Single-email export and printing follow the displayed conversation message.
- Added regression coverage for pagination beyond 5,000 messages, late-parent
  threading, snapshot/log replay, cancellation, cache reuse/removal and reading tools.

### Remaining limitations

- Conversation grouping depends on usable message relationship headers.
- Separate mdbox map indexes and unsupported Dovecot layouts are not interpreted.
- Complex HTML email styling can differ from a web browser.

## [0.2.0] - 2026-09-25

### Added

- Bulk `.eml` export for selected emails, an entire folder or all mail, with
  progress and cancellation. Whole-folder exports include messages beyond the
  list's 5,000-message display limit and ignore current search filters.
- Ctrl/Shift multi-selection, preserved as indexing adds messages.
- Drag-and-drop opening of one archive or extracted folder on the main window
  or welcome guide.
- Recent backups in the File menu and welcome screen, saved locally with an
  option to clear the history without deleting backups or search caches.
- Printing (Ctrl+P) and saving individual emails as PDF, including message
  headers and attachment names. Only inline or already downloaded images are used.
- Help → Check for updates, with an on-demand GitHub release check and a link
  to the newer version's download page. No automatic installation or background checks.
- File → Exit (Ctrl+Q), with the existing protection for an unfinished import
  and a guard while an export is running.

### Changed

- Removed the repeated “Not sure? If you…” text from the welcome guide.
- Added tests for exports over 5,000 messages, cancellation, filename conflicts,
  recent history, drag and drop, PDF/print and update-check failures/timeouts.
- The packaged Windows check now also verifies bulk export and PDF generation.

## [0.1.0] - 2026-09-25

### Added

- Version number in Help → About, diagnostic logs and Windows file properties.
- A single portable Windows executable with all application dependencies bundled.
- Public GitHub Release downloads, release notes and a SHA-256 checksum.
- A packaged application check covering startup, the welcome guide, icons, TLS,
  mailbox import, HTML preview and search from a folder containing only the EXE.

### Included from the earlier development builds

- Read-only browsing of supported JetBackup/cPanel Dovecot mdbox backups.
- Archive and extracted-folder opening, with a friendly getting-started guide.
- Folder navigation, progressive message loading and reusable local search indexes.
- Search filters, supported Dovecot transaction-log status flags and diagnostic logs.
- HTML previews, inline images and optional remote images with timeouts and retry.
- Attachment cards and saving; export of individual emails as `.eml`.
- Help, contact details and information about DesignStack.

### Packaging changes

- The direct EXE replaces the previous ZIP that required an `_internal` folder.
- Updated GitHub build actions to versions using Node.js 24.

### Known limitations

- Main Dovecot index snapshots and map indexes are not yet supported; some backups
  have unknown status flags or ambiguous folder placement.
- Complex HTML email styling may differ from a browser.
- One-file startup unpacks support files into a temporary folder automatically.
