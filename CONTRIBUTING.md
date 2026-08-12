# Contributing

Thank you for helping improve Android Everything.

## Development setup

1. Fork and clone the repository.
2. Use Python 3.8 or later.
3. Install Android SDK Platform Tools and make `adb` available on `PATH`, or set `ANDROID_EVERYTHING_ADB`.
4. Create a topic branch from `main`.
5. Keep generated indexes, device exports, and personal files out of commits.

## Before opening a pull request

Run the syntax check:

```powershell
python -m compileall -q .
```

If a change interacts with a real device, describe the Android version, connection type, and storage layout you tested. Avoid including device serials, private paths, screenshots with personal data, or a generated `files.db`.

Keep pull requests focused and explain the user-visible behavior, testing performed, and any compatibility considerations.

