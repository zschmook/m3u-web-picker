# M3U Web Picker

M3U Web Picker turns a large IPTV provider catalog into a small, curated M3U/XMLTV lineup, with optional sports automation, a browser TV guide, Roku/Cast playback helpers, and a virtual HDHomeRun surface for compatible clients.

This branch is the **bare host-Python runtime**. It intentionally does **not** use Docker or WSL.

## Windows quick start

The Windows bootstrap installer is under `installer/windows-bare`.

It installs/runs the application directly on Windows Python and keeps its managed files under:

```text
%LOCALAPPDATA%\M3U-Web-Picker
```

The installer handles Python 3.12, a private FFmpeg copy, the application source, a private virtual environment, startup registration, updates, and uninstall. Open the app at:

```text
http://localhost:9999
```

A new install opens the first-run wizard.

## Running from source without Docker

Python 3.12+ and FFmpeg are required.

```bash
python -m venv .venv
```

Activate the virtual environment, then:

```bash
python -m pip install -r requirements.txt
python host_runtime.py
```

By default, a source checkout stores runtime data in `./data` and backups in `./backups`. Set `M3U_DATA_DIR`, `M3U_BACKUP_CONTAINER_DIR`, `M3U_CAST_HLS_DIR`, `M3U_FFMPEG`, and `M3U_LAN_HOST` when custom host paths are needed.

## First-run setup

The setup flow can configure:

- a primary M3U or Xtream provider;
- manual channels to keep in the curated lineup;
- Sports Automation and team/league rules;
- optional API-SPORTS schedule support for supported leagues;
- the automatic Master Update schedule;
- optional Jellyfin cache cleanup.

Application state is stored in the configured host data directory, separate from the application source.

## Main outputs

- M3U: `/playlist/channels.m3u`
- Combined XMLTV: `/epg/epg.xml`

Sports-only output and diagnostic/status endpoints are also exposed by the application.

## Sports Automation

Sports Automation scans provider channels and XMLTV data, matches configured teams/leagues, and publishes temporary event channels. Optional API-SPORTS schedule data can provide canonical schedules for MLB, NFL, and NCAA Football selections.

Manual channels and generated sports channels are separate namespaces. A generated sports feed must not remove or replace a saved manual channel even when both point to the same underlying stream.

## TV Guide and LAN playback

The built-in TV Guide uses the curated lineup and Combined XMLTV output. Cast/Roku media normalization uses FFmpeg on the host.

Set `M3U_LAN_HOST` to the host computer's LAN IPv4 address for LAN relay URLs. The Windows installer detects this automatically for normal Wi-Fi/Ethernet setups.

## Roku receiver

The optional Roku developer receiver remains at:

```text
roku-receiver/dist/m3u-web-picker-roku-receiver.zip
```

Do not extract that ZIP before sideloading it. This is an optional edge-case path and is not required for normal browser/Cast use.

## Persistent data and backups

The Windows installer keeps runtime data and application code separate:

```text
%LOCALAPPDATA%\M3U-Web-Picker\data
%LOCALAPPDATA%\M3U-Web-Picker\backups
```

Updating replaces application source/dependencies but does not remove runtime data.

## Tests

```bash
python -m unittest discover -s tests
python -m compileall -q .
```

JavaScript syntax checks can also be run with Node when available:

```bash
find static -name '*.js' -print0 | xargs -0 -n1 node --check
```

## Current known cleanup items

- update-state indicators can visibly finish a fraction of a cycle apart because the sidebar/status layer and Master Update lifecycle layer still have overlapping rendering responsibilities;
- provider events that lose a recognized league classification can fall into generic `football`/`sports` numbering blocks, producing very high sports channel numbers and occasional duplicate event rows;
- the bare Windows runtime still needs full clean-machine validation, especially Windows Firewall/virtual-HDHomeRun behavior.

See `USER-GUIDE.md` for operator-oriented feature notes. The user guide still contains some Docker-era wording that will be normalized as this branch is validated.
