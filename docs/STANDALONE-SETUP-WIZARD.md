# Standalone Setup Wizard

This document describes the standalone first-run setup flow. A working,
isolated implementation runs on port `9998` through
`docker-compose.setup.yml`; the production installer and normal port `9999`
runtime remain unchanged while the flow is tested.

## Goal

Run setup before the normal application so the user chooses features first and
only the host folders and Docker bind mounts required by those features are
created. The user should experience one continuous browser wizard at the normal
application address.

The setup application must not receive the Docker socket and must not mount an
entire host drive. A small host-side launcher owns Docker lifecycle operations,
folder creation, and host-path validation.

## Current isolated flow

```mermaid
flowchart TD
    A[Start] --> B{Choose source}
    B -- Just Testing --> C[Load free public provider]
    B -- Use My Provider --> D[Validate M3U or Xtream provider]
    D --> E[Choose channels]
    C --> E
    E -- Just Testing --> K[Build configuration]
    E -- Provider --> F[Choose Sports Automation rules]
    F --> G[Optionally configure API-SPORTS]
    G --> H[Optionally configure DVR]
    H --> I{Choose media server}
    I -- None --> K
    I -- Jellyfin --> J[Configure Jellyfin cache]
    I -- Plex with DVR --> L[Configure recording library folder]
    J --> K
    L --> K
    K --> M[Run first Master Update]
    M -- Success --> N[Open configured application]
    M -- Failure --> O[Show error and remain in Setup]
    O --> K
```

Just Testing deliberately skips Sports Automation, API-SPORTS, DVR, and media
server configuration. Paid and free sources continue through the same channel,
guide, and playback pipeline after setup.

For a clean Windows PowerShell test:

```powershell
Set-Location C:\m3u-web-picker
docker compose -f docker-compose.setup.yml down -v
docker compose -f docker-compose.setup.yml up -d --build setup
```

The `-v` option is destructive only to the named volumes belonging to the
isolated `m3u-picker-setup` project. Never add `-v` to a normal production
update command.

## Earlier production-installer design sketch

[Download the large PNG](standalone-setup-wizard-flow-large.png) ·
[Download the scalable SVG](standalone-setup-wizard-flow.svg)

```mermaid
flowchart TD
    A[User runs Windows or Unix installer] --> B[Host launcher checks prerequisites]
    B --> B1{Docker available?}
    B1 -- No --> B2[Explain how to install or start Docker]
    B2 --> B
    B1 -- Yes --> C[Detect OS, LAN address, ports, and GPU capability]

    C --> D[Start setup-only web app on the final host port]
    D --> E[Open browser to Setup]
    E --> F[Choose optional features: provider, Sports API, Jellyfin, DVR]

    F --> G[Always request application backup folder]
    F -- Jellyfin selected --> G1[Also request Jellyfin cache folder]
    F -- DVR selected --> G2[Also request DVR folder and optional media server folder]
    G --> H[Host launcher validates and creates only required folders]
    G1 --> H
    G2 --> H
    H --> H1{All required paths valid and writable?}
    H1 -- No --> H2[Return a specific error to the setup page]
    H2 --> G
    H1 -- Yes --> I[Write .env and generated Compose override]

    I --> J{Provider selected?}
    J -- No --> J0[Use the built-in free public provider]
    J -- Yes --> J1[Configure and validate the selected provider]
    J1 -- Invalid --> J2[Show validation error and remain in provider setup]
    J2 --> J1
    J0 --> K[Configure update schedule and timezone]
    J1 -- Valid --> K
    K --> L[Select manual channels]
    L --> M[Choose sports automation rules]
    M --> N{Sports API selected?}
    N -- Yes --> N1[Configure and validate Sports API]
    N -- No --> O{Jellyfin selected?}
    N1 --> O
    O -- Yes --> O1[Confirm cache folder and acknowledge deletion risk]
    O -- No --> P{DVR selected?}
    O1 --> P
    P -- Yes --> P1[Configure DVR, media server handoff, and recording limits]
    P -- No --> Q[Choose direct streaming or tested encoding mode]
    P1 --> Q

    Q --> R[Persist application settings in m3u-picker-data]
    R --> S[Setup writes completion marker]
    S --> T[Browser displays Starting M3U Web Picker]
    T --> U[Host launcher replaces Setup with the normal Compose stack]
    U --> V{Startup checks pass?}

    V -- No --> V1[Show recovery page with failed mount or service]
    V1 --> D
    V -- Yes --> W[Browser detects application health endpoint]
    W --> X[Browser reloads into the normal application]
    X --> Y[Run first Master Update]
    Y --> Z[Open configured TV Guide]
```

## Configuration ownership

The standalone setup stage should generate or populate the same configuration
the application uses today.

| Destination | Setup-owned values |
| --- | --- |
| `.env` | Host port, LAN host, backup directory, encoder preference, and only the selected Jellyfin/DVR paths |
| Generated Compose override | GPU reservation and explicit bind mounts required by selected features |
| `m3u-picker-data` | Provider or free-source selection, update schedule, manual channels, sports rules, optional API settings, optional DVR settings, encoding settings |
| Setup completion marker | Schema version, completion state, and non-secret startup diagnostics |

Provider credentials remain in the persistent application database as they do
today. They should not be duplicated into `.env` or the generated Compose file.

## Host launcher responsibilities

The Windows PowerShell and Unix shell launchers should:

1. Detect Docker, Docker Compose, LAN addressing, and supported GPU runtime.
2. Start and monitor the setup-only application.
3. Validate requested host paths without granting that access to the container.
4. Create dedicated directories only after the user selects them.
5. Generate `.env` and a Compose override using explicit paths.
6. Stop Setup and start the normal application after completion.
7. Preserve existing configuration during upgrades and recovery runs.

The launcher should communicate with Setup through narrowly scoped files in a
dedicated setup-state directory. It should never execute arbitrary commands
received from the browser.

## Seamless browser handoff

Setup and the normal application use the same host and port, but never bind it
simultaneously. After the user finishes:

1. The setup page changes to a self-contained **Starting…** screen.
2. That page polls a stable health endpoint with exponential backoff.
3. The host launcher replaces the setup container with the normal container.
4. When the health response identifies the normal application, the page reloads.

This produces a short loading transition without exposing Docker controls to
the application or requiring the user to run another command.

## Existing behavior to preserve

- The free public provider remains available for first-run testing.
- Paid and free providers use the same downstream guide pipeline.
- DVR media never lives in the container layer.
- Media server output may remain inside the DVR mount initially; support for a
  separate library bind mount can be added without changing the handoff design.
- Jellyfin cache cleanup remains optional and requires explicit acknowledgement.
- Direct streaming remains the safe default when hardware encoding is not
  tested or available.
- Existing installations skip setup unless the user explicitly launches repair
  or reconfiguration mode.

## Recovery paths

Setup state should be resumable. A restart during setup returns to the last
completed step. If the normal application fails its startup checks, the launcher
returns to Setup with the exact failure instead of repeatedly restarting the
container. Existing `.env`, generated Compose, and database files should be
backed up before Setup replaces them.
