# Changelog

All notable changes to Android Everything are documented in this file.

## [Unreleased]

## [0.1.5] - 2026-08-26

### Fixed

- Serialize indexing and deletion for each device so an in-flight index
  snapshot cannot restore a path that was deleted from the phone.
- Disable index startup while deletion is active and reject deletion while an
  index refresh is running.
- Remove successfully deleted paths from SQLite before releasing the device
  mutation lock.

### Tests

- Add a deterministic concurrency regression test covering the sequence where
  indexing captures a file immediately before the user deletes it.

## [0.1.4] - 2026-08-25

### Added

- Add true case-insensitive substring matching for indexed file names and
  Android paths using the SQLite FTS5 trigram tokenizer.
- Support one- and two-character substring searches with a Unicode-aware
  fallback while retaining safe handling of FTS5 punctuation and operators.

### Changed

- Automatically migrate the existing `unicode61` search index to the trigram
  schema without discarding indexed file metadata.
- Normalize indexed search text in application code so substring matching
  remains case- and diacritic-insensitive on every supported Python version.

### Tests

- Add coverage for middle-of-name and middle-of-path matching, mixed long and
  short search terms, and migration from the v0.1.3 database schema.

## [0.1.3] - 2026-08-20

### Fixed

- Bind every device-scoped ADB command to an explicit device serial instead of
  relying on shared mutable selection state.
- Lock device selection and refresh controls until all active indexing or file
  operations finish, including overlapping background operations.
- Apply indexing and deletion results to the device that started the operation
  even if the visible UI state changes before completion.
- Quote Android paths before passing them to remote shell file operations.
- Sanitize Android filenames used on Windows and prevent batch downloads from
  overwriting existing or same-named files.
- Keep temporary downloads for same-named files in separate cache paths.
- Report the actual success and failure counts for batch downloads.

### Tests

- Add regression coverage for explicit ADB device binding, UI operation locks,
  Android shell-path quoting, and Windows download-path handling.

## [0.1.2] - 2026-08-18

### Added

- Publish a complete Windows ZIP containing Android Everything, ADB, its required DLLs, and the Android Platform Tools notice.
- Automatically discover `adb.exe` next to the packaged application or inside a sibling `platform-tools` directory.

### Fixed

- Show a status-bar message instead of allowing SQLite search failures to escape the Tkinter callback.
- Accept usable Android listings when protected child directories make recursive `ls` return status 1.
- Require a scan-completion marker so interrupted ADB output cannot replace the previous index.

### Tests

- Add regression coverage for packaged ADB discovery, partial Android listings, and interrupted scans.

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

[Unreleased]: https://github.com/luli395/android_everything/compare/v0.1.5...HEAD
[0.1.5]: https://github.com/luli395/android_everything/compare/v0.1.4...v0.1.5
[0.1.4]: https://github.com/luli395/android_everything/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/luli395/android_everything/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/luli395/android_everything/releases/tag/v0.1.2
[0.1.1]: https://github.com/luli395/android_everything/releases/tag/v0.1.1
[0.1.0]: https://github.com/luli395/android_everything/releases/tag/v0.1.0
