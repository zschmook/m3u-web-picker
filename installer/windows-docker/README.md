# Windows Docker bootstrap scripts

These two scripts perform the same installation. Use whichever is easier to run:

- `install.ps1` from Windows PowerShell; or
- `python install.py` from a terminal with Python installed.

Both scripts download the `main` branch directly from GitHub as a ZIP. Git is not required. Docker Desktop must already be installed and running.

Both scripts install source under `%LOCALAPPDATA%\m3u-web-picker`; no elevated terminal is required. When that directory exists, they ask for an installation mode before changing anything:

- `UPGRADE (UP)` replaces the application source and preserves its production data volume and saved setup.
- `CLEAN (CL)` displays a destructive-data warning, requires typing `CLEAN` to confirm, then replaces the source, deletes only the production `m3u-picker` data volume, and opens a fresh setup. Saved channels, settings, providers, DVR schedules, and sports automation selections are erased. Recording files in the separate DVR directory are retained.

Both modes force the production app onto port `9999`. Neither mode targets the separate `m3u-picker-dev` stack on port `9998`. The recording directory remains outside the reset volume; an existing custom path is preserved, otherwise `%USERPROFILE%\Videos\M3U-Web-Picker-DVR` is created and used.

When the production install directory does not exist, the scripts skip both the unavailable upgrade choice and the redundant destructive-data confirmation, then proceed directly with a fresh clean installation.

PowerShell:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
```

Python:

```powershell
python .\install.py
```

For unattended use, pass `--mode UP` or `--mode CL`. PowerShell accepts the equivalent `-Mode UP` or `-Mode CL`.
