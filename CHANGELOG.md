# Changelog

All notable changes to Android Everything are documented in this file.

## [0.1.0] - 2026-08-14

### Added

- Everything-style, search-as-you-type file search for Android devices on Windows.
- ADB device discovery, indexing, extension filters, sorting, file pull, open, reveal, path copy, and delete operations.
- Local SQLite/FTS5 metadata index with atomic per-device replacement.
- Standalone Windows executable build and SHA-256 checksum.
- English user guide and architecture documentation.

### Changed

- Store persistent data and logs under `%LOCALAPPDATA%\AndroidEverything` so the one-file Windows build can retain its index safely.

### Fixed

- Keep the main window usable when ADB is not installed and display setup guidance instead of exiting.
- Report packaged startup errors through a GUI dialog and persistent log rather than a console prompt.

[0.1.0]: https://github.com/luli395/android_everything/releases/tag/v0.1.0
