# Android Everything User Guide

This guide explains how to install Android Everything on Windows, connect an Android device, build a file index, and search and manage files on the device.

## 1. Before You Begin

### 1.1 System requirements

- Windows 10 or later
- An Android device with USB debugging enabled
- A USB cable that supports data transfer

The complete Windows ZIP includes ADB and does not require Python. Running from source requires Python 3.8 or later with Tkinter and Android SDK Platform Tools; the application itself uses only the Python standard library, so no `pip install` step is required.

### 1.2 Install Python

Skip this section when using `AndroidEverything.exe`.

Download and install Python from the [official Python website](https://www.python.org/downloads/windows/). Selecting **Add Python to PATH** during installation is recommended.

Verify the installation in PowerShell:

```powershell
python --version
python -c "import tkinter; print('Tkinter OK')"
```

Continue when both commands complete successfully.

### 1.3 Install ADB

Skip this section when using the complete Windows ZIP. Keep `adb.exe`, `AdbWinApi.dll`, and `AdbWinUsbApi.dll` beside `AndroidEverything.exe`.

Download the Windows package from [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) and extract it.

Configure ADB using either of the following methods.

#### Option 1: Add ADB to PATH

Add the extracted `platform-tools` directory to the Windows `PATH` environment variable. Open a new PowerShell window and run:

```powershell
adb version
```

#### Option 2: Set the path for the current PowerShell session

```powershell
$env:ANDROID_EVERYTHING_ADB = "C:\path\to\platform-tools\adb.exe"
```

When using this option, start Android Everything from the same PowerShell window. You can alternatively add `ANDROID_EVERYTHING_ADB` to your Windows user environment variables to make the setting persistent.

## 2. Enable USB Debugging on Android

Menu names vary slightly between manufacturers, but the usual steps are:

1. Open **Settings > About phone**.
2. Tap **Build number** or **Software version** about seven times to enable Developer options.
3. Return to Settings and open **System > Developer options**.
4. Enable **USB debugging**.
5. Connect the phone to the computer with a USB cable. If prompted, set the USB mode to **File transfer**.
6. When the phone asks whether to allow USB debugging, verify the computer fingerprint and tap **Allow**.

Check the connection in PowerShell:

```powershell
adb devices
```

A working connection looks similar to this:

```text
List of devices attached
XXXXXXXXXXXX    device
```

Common device states:

| State | Meaning and resolution |
| --- | --- |
| `device` | Connected and authorized; the device is ready to use |
| `unauthorized` | Unlock the phone and accept the USB debugging prompt |
| `offline` | Reconnect the USB cable or run `adb kill-server` followed by `adb start-server` |
| No device listed | Check the cable, USB mode, phone driver, and USB debugging setting |

## 3. Download and Start the Application

### 3.1 Windows executable

1. Open the [v0.1.2 release](https://github.com/luli395/android_everything/releases/tag/v0.1.2).
2. Download [AndroidEverything-v0.1.2-windows.zip](https://github.com/luli395/android_everything/releases/download/v0.1.2/AndroidEverything-v0.1.2-windows.zip) and [AndroidEverything-v0.1.2-windows-SHA256.txt](https://github.com/luli395/android_everything/releases/download/v0.1.2/AndroidEverything-v0.1.2-windows-SHA256.txt).
3. Verify the ZIP, then extract every file into the same directory.
4. Double-click `AndroidEverything.exe`.

Python is not required for the Windows executable. Windows may display a SmartScreen notice because this early release is not code-signed; review the publisher and checksum before choosing to run it.

To verify the downloaded executable, open PowerShell in its directory and run:

```powershell
$expected = (Get-Content .\AndroidEverything-v0.1.2-windows-SHA256.txt).Split()[0]
$actual = (Get-FileHash .\AndroidEverything-v0.1.2-windows.zip -Algorithm SHA256).Hash.ToLowerInvariant()
$actual -eq $expected
```

Continue when the command returns `True`. The release page also displays the expected SHA-256 value.

### 3.2 Run from source

```powershell
git clone https://github.com/luli395/android_everything.git
cd android_everything
python main.py
```

The top of the application window contains the search box, file-type filter, device selector, and **Refresh** and **Index** buttons. The bottom area displays status messages, indexing progress, and the number of files.

## 4. Build a File Index

The first time you connect a device, build an index before searching:

1. Make sure the phone is unlocked and USB debugging has been authorized.
2. Click **Refresh** to update the device list.
3. Select the target device from the **Device** list.
4. Click **Index**.
5. Wait for the progress indicator to finish. The status bar will show the number of indexed files.

While indexing, the button changes to **Stop**. Click it to request cancellation of the current indexing operation.

Android Everything automatically checks internal storage, SD cards, and several common external-storage paths. The index records file metadata—file name, device path, size, and modification time—but does not copy all file contents to the computer.

The generated index is stored as `%LOCALAPPDATA%\AndroidEverything\files.db`. Clicking **Index** again atomically replaces the old index for the selected device only after a complete scan succeeds.

> Some Android system directories are protected by platform permissions. A standard ADB session can index only files accessible to the current user. Android Everything also skips paths such as `/Android/data` and `.thumbnails`.

## 5. Search and Filter

### 5.1 Search for files

Enter a file name or a keyword from its path in the search box. Results update automatically after a short delay, or you can press Enter to search immediately.

Search uses prefix matching. For example:

- `photo` matches terms beginning with `photo`
- `report 2026` searches file names and paths for both term prefixes
- Clearing the search box displays files from the selected device's index

The application displays up to 10,000 results per search.

### 5.2 Filter by file type

Use the **Type** list next to the search box to filter by one of the most common extensions in the current index, such as JPG, MP4, or PDF. Select **All** to remove the type filter.

### 5.3 Sort results

Click a result-list column heading to sort by:

- Name
- Path
- Size
- Modified
- Type

Click the same heading again to switch between ascending and descending order.

## 6. File Operations

### 6.1 Download and open a file

Double-click a search result. Android Everything downloads the file to a system temporary directory and opens it with the default Windows application.

The temporary directory is normally:

```text
%TEMP%\android_everything
```

### 6.2 Save files to the computer

1. Select one or more results. Use Ctrl or Shift for multiple selection.
2. Right-click and choose **Pull to PC**.
3. For one file, choose a destination file name. For multiple files, choose a destination directory.

Files with identical names may overwrite one another when saved to the same directory. Prepare the destination directory accordingly.

### 6.3 Show a file in File Explorer

Right-click a result and choose **Show in Explorer**. Android Everything downloads the first selected file to the temporary directory and selects it in Windows File Explorer.

### 6.4 Copy device paths

Select one or more files, right-click, and choose **Copy Path**. Their full paths on the Android device are copied to the clipboard, one path per line.

### 6.5 Delete files from the device

1. Select one or more files.
2. Right-click and choose **Delete**.
3. Review the confirmation dialog carefully before confirming.

This operation deletes the original files from the Android device, not just their local index records. Successfully deleted files are also removed from the index. Deletion cannot be undone, so use **Pull to PC** to back up important files first.

## 7. Data and Privacy

- `%LOCALAPPDATA%\AndroidEverything\files.db` contains the device serial and indexed file names and paths. Treat it as private local data.
- Files downloaded from the phone and files in the temporary directory are not uploaded to GitHub.
- The project's `.gitignore` excludes `files.db`, `phone/`, `downloads/`, and Python caches. Check logs and screenshots for personal information before sharing them.
- To remove the index, close the application and delete `%LOCALAPPDATA%\AndroidEverything\files.db`. An empty database will be created the next time the application starts.
- Startup diagnostics are written to `%LOCALAPPDATA%\AndroidEverything\android-everything.log`.
- To remove files created by double-clicking or **Show in Explorer**, delete `%TEMP%\android_everything`.

## 8. Troubleshooting

### "ADB was not found" appears

Make sure `adb version` works in the current PowerShell session, or set the executable path explicitly before starting the application:

```powershell
$env:ANDROID_EVERYTHING_ADB = "C:\path\to\platform-tools\adb.exe"
python main.py
```

### The application displays "No devices found"

Check the following:

1. Confirm that `adb devices` lists the phone.
2. Unlock the phone and accept the debugging authorization prompt.
3. Use a USB cable that supports data transfer.
4. Install the manufacturer's Windows USB driver if required.
5. Click **Refresh** in Android Everything.

If the device still does not appear, restart ADB:

```powershell
adb kill-server
adb start-server
adb devices
```

### The index contains fewer files than the phone's file manager reports

Android scoped storage and application sandboxes restrict standard ADB access to some directories. Android Everything also deliberately skips some system and thumbnail paths, so the indexed count can be lower than the phone's file manager count.

### A newly copied file does not appear in search results

The current version does not monitor the device for live file changes. Click **Index** to rebuild the index, then search again.

### File size or modification time is missing

The exact `ls` output can vary between Android manufacturers. When Android Everything cannot parse a field, it can still index the file, but the size may display as `0 B` and the modification time as `-`.

### A file does not open after double-clicking it

Make sure an application supporting that file type is installed on the computer. You can also choose **Pull to PC**, save the file, and open it manually.

## 9. Exit Safely

Wait for active downloads or indexing to finish before closing the window and disconnecting the USB cable. The local index remains available the next time the same device is connected. Rebuild the index after files on the device change.
