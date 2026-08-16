# Windows Python installer

This is the packaged Windows installer for the host-Python edition of M3U Web Picker.

It uses the installer implementation that was tested on a clean Windows 11 machine: no Docker, WSL, Git, or Go is required at runtime.

The packaged EXE:

1. runs from the Python runtime embedded by PyInstaller;
2. finds or installs Python 3.12 for the application host;
3. downloads and verifies the pinned FFmpeg build;
4. downloads M3U Web Picker from the `main` branch;
5. creates a private virtual environment and installs `requirements.txt`;
6. stores application state below `%LOCALAPPDATA%\\M3U-Web-Picker`;
7. registers startup/uninstall integration for the current Windows user;
8. starts Waitress on port 9999 and opens the first-run wizard.

Updates and uninstall are supported by the installed launcher with `--update` and `--uninstall`. Uninstall asks whether the database and backups should be preserved.

## Build

On Windows with Python 3.12+:

```text
python build.py
```

The output is:

```text
dist\\M3U-Web-Picker-Windows-Setup.exe
```

GitHub's packaging workflow builds the same file for release downloads.
