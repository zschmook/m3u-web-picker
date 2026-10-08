# VPN branch: setup tests and port-9998 startup activation

Development target: port **9998** (`m3u-picker-setup`). Port 9999 remains on v41.

Implemented in this milestone:

- Settings / Network and the first-run source screen can import a WireGuard configuration.
- SQLite stores provider selection, LAN subnet exceptions, profile identity, and timestamps.
- Secret uploads live only in Linux tmpfs (`/dev/shm`), with mode 0600, scoped to the current page session. They expire after 20 minutes, are removed on replacement/clear, and disappear on container recreation. The browser keeps its selected File in page memory; no localStorage, sessionStorage, or IndexedDB is used. SQLite stores no keys. Refreshing requires uploading again.
- Imports accept one peer with a public IPv4 endpoint and a full IPv4 route. Hooks and unknown options are rejected. IPv6 addresses and imported DNS are omitted for this IPv4-only preview; Gluetun owns DNS. LAN exceptions cannot be public ranges or overlap the tunnel address.
- `scripts/prepare_vpn_test.py` produces a Compose overlay for port 9998 only. It does not run Docker or restart anything. The overlay moves port publishing to Gluetun, moves its internal health listener to 9990, and preserves application mounts inherited from the base Compose definition.

Selecting a file uploads and validates it, then automatically queues an isolated connection test. The wizard shows a spinner, then **VPN tests passed** on success, and keeps Continue disabled until the current file passes. Changes invalidate previous results; failed tests can be retried. Passing a test alone **does not enable the app VPN**. Continuing setup with VPN selected queues a host-side handoff before provider validation. The authorized startup helper attaches Picker to Gluetun’s network, moves port 9998 publishing to Gluetun, and keeps existing data volumes. It checks real Picker egress before declaring activation successful.

## Preparing a real tunnel test

Download a WireGuard configuration from your provider with a public IPv4 endpoint. Keep the file private. Do not send its private key through chat.

Run from the repository root with the actual LAN subnet:

```powershell
python scripts/prepare_vpn_test.py --wireguard-config C:/private/vpn.conf --lan-subnet 10.0.0.0/24
```

The output includes a private `wg0.conf` and a public Compose overlay. Protect the output directory with the current user's Windows permissions; Linux file modes alone do not enforce Windows ACLs. The command does not fetch or start Gluetun. The generated image uses `qmcgaw/gluetun:latest` for initial testing; pin a verified image digest before release.

This manual preparation command explicitly writes private output files; it is separate from the wizard’s memory-only upload/test flow. A future installer handoff must apply the temporary upload without returning keys through public APIs.

## Startup activation on the isolated test host

`scripts/vpn_startup_host.py --watch --allow-restart-9998 --image m3u-web-picker:TAG` consumes non-secret startup handoffs from SQLite. It requires explicit authorization for port-9998 restarts, checks active playback, and never touches port 9999. The web app does not have a Docker socket. A passing current upload is required before a handoff is queued. Requested but unapplied or disconnected VPN playback is blocked.

Test uploads remain temporary. Applying a passing configuration copies the canonical WireGuard profile and private control authentication through stdin into an owned Docker volume. Its directories have mode 0700 and files 0600. Only Gluetun mounts this volume. The app, SQLite, Compose manifest, environment variables, command arguments and host deployment logs contain no WireGuard keys. Applied credentials intentionally survive container recreation and updates; explicit installer CL removes its owned credential volume. Recording volumes are retained.

The complete managed graph is `runtime/vpn/docker-compose.vpn.json`. It uses existing external app volumes, the private credential volume, an updated image supplied by `.env`, and `unless-stopped` restart policies. In VPN mode, Compose waits for Gluetun health before starting Picker; Docker engine restarts may start both together, so the WSGI gate blocks provider work until the tunnel is healthy. Gluetun automatically retries failed connections. UP retains this graph and its volumes and rejects VPN-incapable images before interrupting the existing deployment.

The Settings checkbox **Use a VPN** saves a separate `feature_enabled` preference in SQLite. Disabling it returns the app to normal networking and hides the sidebar control; the saved private configuration is retained for re-enabling. Existing applied installations default to enabled. Enabling Settings with a saved profile starts that connection. The sidebar power button changes `desired_on` independently: powering off leaves the checkbox checked and the red control visible. An unavailable manager rejects settings changes that cannot be applied; a failed settings disable restores the previous protected graph and keeps its control available.

The sidebar power icon polls live health independently of the main status request and appears only while Use a VPN is enabled. It is green when protected and healthy, red when explicitly powered off, and blinks yellow when powered on but disconnected (or while switching). Reduced-motion users get a steady yellow icon. The outage page retains the same control without importing the scheduler. Off deliberately returns Picker to the normal Docker network and Docker DNS; On restores the Gluetun network and local VPN DNS. A stopped or failed tunnel while On never enables normal-network fallback.

On/off clicks queue a non-secret SQLite request for the authorized host manager. Switching requires idle playback and briefly recreates the app/network graph; the manifest retains the applied mode for reboots and upgrades. Failed switches restore the previous graph and show an error. Switching On from a failed/off deployment remains blocked until protected networking is verified. The control is disabled when the host manager heartbeat expires. Initial setup activation and interactive switching currently require the port-9998 development host helper; shipping and supervising that manager on other installation targets remains release work. Docker restart recovery of an already applied connection does not require the helper.

## Protected media output

When VPN mode is active, exported M3U URLs and upstream redirects go through opaque Picker relay URLs, including the direct playlist with FFmpeg disabled. HLS master/media playlists rewrite nested playlists, segments, maps, encryption keys, and URI attributes. Range/HEAD requests are supported and safe filename extensions are preserved for HLS clients. Provider URLs stay server-side. Unsupported content-steering/variable HLS extensions fail instead of returning unrewritten playlists. Relay tokens live in process memory; clients must reload their playlist after an app restart.

The live 9998 run verified Picker’s Proton egress, local Gluetun DNS, WGAL master/media/segments and 720p H.264/AAC decoding with app FFmpeg disabled, and LAN/Tailscale host access. With the real app’s VPN stopped, previously resolved HTTPS egress was blocked and playback returned 503 while LAN/Tailscale access remained reachable. VPN recovery was checked afterward. This does not establish remote-device playback or MagicDNS coverage.

Remaining release work includes authenticated and supervised host apply management beyond port 9998, replacement of an already saved VPN profile, OpenVPN imports, full host reboot verification, and broader provider/device testing.

## Provider resources

The provider selector loads `static/vpn-provider-links.json`, a reviewed resource list dated October 8, 2026. Each commercial option has an official website, setup/configuration resource, and pricing page (or the official website's plan section). Custom WireGuard links to protocol and Gluetun documentation without a pricing link.

Links open in a new tab and do not change the uploaded profile or LAN choice. No affiliate codes or fixed prices are stored. Notes distinguish downloadable WireGuard configs, paid-account requirements, and guides that cover other protocols. Gluetun provider support is broader than this preview's WireGuard file importer; the resource list does not claim that every commercial provider's files are accepted today.

Most pages were checked directly. NordVPN, TorGuard, parts of CyberGhost and PrivateVPN, and IPVanish block some automated HTTP checks. Search-indexed official pages were used where available; IPVanish's configuration-guide URL is also referenced by TP-Link's official configuration-file documentation. Its plan link leads to the official website rather than guessing an unverified checkout URL. URLs should be re-reviewed as provider sites change.

## Isolated connection-test helper

`scripts/vpn_test_host.py` runs a disposable Gluetun container and a Python probe sharing its network stack. The containers publish no ports, never mount Picker's database or recordings, and are identified by a unique test label. The helper reads the selected temporary upload through Docker stdin/stdout in process memory and feeds it to Gluetun through stdin. The profile and private control-auth configuration live in the probe’s `/gluetun` tmpfs, without host files or credentials in Docker arguments/environment. Removing the probe removes its memory storage.

The helper checks healthy connectivity, public-IP change against normal host egress, Gluetun's local DNS resolver, LAN access, the host's Tailscale address when available, firewall blocking with the disposable VPN stopped, and recovery afterward. The VPN-down probe uses a previously resolved public IP with TLS hostname verification, so DNS failure alone cannot produce a passing kill-switch result. Healthy recovery includes another successful public-IP request.

Tests and results are stored in `vpn_connection_tests` in SQLite, separate from VPN activation. Changing the saved profile or LAN exceptions marks old results stale. Passing a test never changes Picker's network or implies media-relay coverage. Tailscale checks the host's tailnet address from the probe; it does not establish a remote-device playback session or verify MagicDNS.

Run a single test against the saved port-9998 profile:

```powershell
python scripts/vpn_test_host.py
```

For the wizard’s automatic connection tests, start the narrowly scoped host helper:

```powershell
python scripts/vpn_test_host.py --watch
```

The browser only queues tests and reads results; it cannot execute Docker commands. The helper requires the `m3u-picker-setup` instance to advertise port 9998 and only removes its own uniquely labelled probe containers. Start it on the Docker host, not inside Picker. The VPN modules and routes must be deployed before using automatic tests. Gluetun must already be pulled; each test resolves and records its image digest before creating the probe.
