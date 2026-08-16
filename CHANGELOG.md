# Changelog

All notable changes to Android Everything are documented in this file.

## [0.1.1] - 2026-08-16

### Fixed

- Treat FTS5 punctuation, quotes, parentheses, and operators as ordinary search input instead of allowing malformed queries to reach SQLite.
- Return an empty result for punctuation-only searches rather than raising an FTS5 syntax error.
- Determine ADB command success from its process exit code instead of matching words in stderr.
- Preserve the previous device index when an ADB scan fails, times out, or the device goes offline.

### Tests

- Add regression coverage for FTS5 special-character searches and ADB command exit handling.

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

[0.1.1]: https://github.com/luli395/android_everything/releases/tag/v0.1.1
[0.1.0]: https://github.com/luli395/android_everything/releases/tag/v0.1.0
