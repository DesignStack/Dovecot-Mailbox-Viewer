# Changelog

Versions use **MAJOR.MINOR.PATCH**. Every published version has a GitHub Release
with its own notes and Windows download. Earlier development builds were unversioned.

## [Unreleased]

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
