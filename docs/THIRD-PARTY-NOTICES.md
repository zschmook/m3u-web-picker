# Third-party notices

Settings → Open Source & Credits displays software, service and artwork credits.
Every Docker build generates `static/licenses/generated/index.json` and a
downloadable `third-party-notices.zip` from the installed Python distributions,
their embedded license files, Python's runtime license, Debian package copyright
files and shared license texts. The ZIP also includes the exact FFmpeg build
configuration, Dockerfile and bundled logo source manifests. It contains no
provider credentials, VPN configuration or application data.

The build fails when an installed Python or Debian package has no retained
notice. `http_ece` 1.2.1's wheel omits its MIT license, so its versioned upstream
license is preserved in `static/licenses/upstream`. A new version must be audited
before adding a new fallback. Bootstrap 5.3.3 and its bundled Popper 2.11.8 have
versioned local MIT notices. Gluetun's project notice is retained separately;
the optional sidecar is a separate upstream image with its own dependencies.

## Source distribution

Attribution is not a substitute for license obligations. The installed Debian
FFmpeg and Comskip builds are GPL-2.0-or-later. Their exact Debian source package
versions and source links are recorded in the inventory. Redistribution should
include corresponding source and build instructions as required by those
licenses, including covered libraries; an upstream homepage alone is not a
source bundle. These app changes do not retroactively provide source archives
for past published images. See https://ffmpeg.org/legal.html.

M3U Web Picker currently has no repository-level LICENSE file. These notices
describe dependency licenses; they do not choose a license for the project's
original code.

## Services and artwork

Google Cast is supplied under Google's SDK terms. ESPN, API-SPORTS,
IPTV-EPG.org and public playlist sources are credited as services, not relabeled
as open-source software. The IPTV-org playlist project uses the Unlicense;
that does not license broadcasts. All bundled league and hockey logo origins
are recorded in the linked `static/icons/*/sources.json` manifests. Logos and
trademarks remain their owners' property. Attribution does not itself establish
permission to redistribute an asset.

Build tools downloaded by developers and host tools such as Docker, Tailscale,
Roku, Jellyfin and Plex are not bundled app dependencies. Their own distributions
retain their licenses and terms. The inventory covers the Picker image; it does
not claim to audit independently supplied images or user-installed software.

Frozen installers include the exact installed Python and PyInstaller license
notices as a bundled text file, available via `--credits` without installing or
changing containers. Installer builds fail if either notice is missing.
