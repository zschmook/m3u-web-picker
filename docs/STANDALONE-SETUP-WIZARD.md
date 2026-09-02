# Standalone Setup Wizard

This document describes a future first-run setup flow based on the current
Docker installation, onboarding wizard, DVR, Jellyfin cache integration, and
provider configuration. It is a design document only; the current runtime is
unchanged.

## Goal

Run setup before the normal application so every host folder and Docker bind
mount exists when the real container starts. The user should experience one
continuous browser wizard at the normal application address.

The setup application must not receive the Docker socket and must not mount an
entire host drive. A small host-side launcher owns Docker lifecycle operations,
folder creation, and host-path validation.

## Proposed first-run flow

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
    E --> F[Choose host storage]

    F --> F1[Application backup folder]
    F --> F2[DVR recording folder]
    F --> F3[Optional Plex destination]
    F --> F4[Optional Jellyfin cache folder]

    F1 --> G[Setup writes a storage request]
    F2 --> G
    F3 --> G
    F4 --> G
    G --> H[Host launcher validates and creates allowed folders]
    H --> H1{All required paths valid and writable?}
    H1 -- No --> H2[Return a specific error to the setup page]
    H2 --> F
    H1 -- Yes --> I[Write .env and generated Compose override]

    I --> J[Configure primary provider]
    J --> J1{Provider validates?}
    J1 -- No --> J2[Show validation error and remain in Setup]
    J2 --> J
    J1 -- Yes --> K[Configure update schedule and timezone]
    K --> L[Select manual channels]
    L --> M{Enable Sports Automation?}
    M -- Yes --> M1[Choose sports rules and optional schedule API]
    M -- No --> N
    M1 --> N{Use Jellyfin cache cleanup?}
    N -- Yes --> N1[Confirm exact cache folder and acknowledge deletion risk]
    N -- No --> O
    N1 --> O[Choose direct streaming or tested encoding mode]

    O --> P[Persist application settings in m3u-picker-data]
    P --> Q[Setup writes completion marker]
    Q --> R[Browser displays Starting M3U Web Picker]
    R --> S[Host launcher stops Setup]
    S --> T[Host launcher starts the normal Compose stack]
    T --> U{Startup checks pass?}

    U -- No --> V[Show recovery page with failed mount or service]
    V --> D
    U -- Yes --> W[Browser detects application health endpoint]
    W --> X[Browser reloads into the normal application]
    X --> Y[Run first Master Update]
    Y --> Z[Open configured TV Guide]
```

## Configuration ownership

The standalone setup stage should generate or populate the same configuration
the application uses today.

| Destination | Setup-owned values |
| --- | --- |
| `.env` | Host port, LAN host, backup directory, DVR directory, Jellyfin cache directory, encoder preference |
| Generated Compose override | GPU reservation and explicit host bind mounts |
| `m3u-picker-data` | Provider, update schedule, manual channels, sports rules, optional API settings, DVR settings, encoding settings |
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
- Plex output may remain inside the DVR mount initially; support for a separate
  Plex bind mount can be added without changing the handoff design.
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
