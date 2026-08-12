# Android Everything

Android Everything is a lightweight desktop file search tool for Android devices. It connects through Android Debug Bridge (ADB), builds a local SQLite/FTS5 index, and lets you find files on a connected phone from a responsive Tkinter interface.

> The application is currently aimed at Windows desktop users and is under active development.

## Documentation

- [English User Guide](docs/USER_GUIDE.md)
- [中文使用说明](docs/USER_GUIDE.zh-CN.md)

## Features

- Discover connected Android devices through ADB
- Detect internal storage, SD cards, and common external-storage paths
- Build a local index of file names, paths, sizes, and modification times
- Search instantly with SQLite FTS5 and prefix matching
- Filter results by file extension and sort by column
- Pull files to the computer, open them, or reveal them in Explorer
- Copy Android paths and delete selected device files after confirmation
- Dark desktop interface with no third-party Python dependencies

## Requirements

- Windows 10 or later
- Python 3.8 or later (with Tkinter)
- [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools)
- An Android device with USB debugging enabled

## Quick start

1. Clone the repository:

   ```powershell
   git clone https://github.com/luli395/android_everything.git
   cd android_everything
   ```

2. Make `adb` available using either approach:

   - Add the Platform Tools directory to `PATH`; or
   - Set the executable explicitly:

     ```powershell
     $env:ANDROID_EVERYTHING_ADB = "C:\path\to\platform-tools\adb.exe"
     ```

3. Connect the phone over USB, enable **Developer options > USB debugging**, and approve the computer on the device.

4. Confirm that ADB sees the device:

   ```powershell
   adb devices
   ```

5. Start the application:

   ```powershell
   python main.py
   ```

Select a device, click **Index**, and then search by file name or path.

## Configuration

The defaults are defined in [`config.py`](config.py). The most useful runtime setting is:

| Environment variable | Purpose |
| --- | --- |
| `ANDROID_EVERYTHING_ADB` | Absolute path to the `adb` executable. If unset, the application searches `PATH`. |

The SQLite index is generated locally as `files.db`. Device content, exported files, database indexes, caches, and local environment files are intentionally excluded from version control.

## Project structure

```text
android_everything/
├── main.py             # Application entry point
├── adb_wrapper.py      # ADB discovery and file operations
├── file_indexer.py     # Device scanner and indexing pipeline
├── database.py         # SQLite and FTS5 persistence
├── search_engine.py    # Search API and query cache
├── config.py           # Application defaults
├── docs/               # User documentation
└── ui/                 # Tkinter window, file list, and styling
```

## Privacy

Android Everything processes device metadata locally. The generated index can include device serials and file paths, so `files.db` must not be committed or shared. Files pulled from a phone should likewise remain outside the repository.

## Development

The current code uses only the Python standard library. Run the syntax check and unit tests with:

```powershell
python -m compileall -q .
python -m unittest discover -s tests -v
```

Bug reports and focused pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Licensed under the [MIT License](LICENSE).
