# macOS installer

The macOS package is a small zip containing `install.command` and `uninstall.command`.

`install.command` installs the host-Python edition under:

```text
~/Library/Application Support/M3U-Web-Picker
```

It downloads the current `main` source, creates a private virtual environment, writes host runtime paths, registers a per-user LaunchAgent, starts M3U Web Picker on port 9999, and opens the first-run wizard.

Requirements:

- Python 3.12+;
- FFmpeg.

If Homebrew is available, the installer can install missing `python@3.12` and `ffmpeg`. Otherwise it tells the user what is missing and exits without modifying the app install.

Running `install.command` again updates the application source and Python dependencies while preserving data/backups.

`uninstall.command` removes the LaunchAgent and application runtime, and asks whether the database/backups should be preserved.

This package is intentionally user-scoped and does not require Docker.
