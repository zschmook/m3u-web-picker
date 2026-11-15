# Xbox TV Guide app plan

Date: October 25, 2026

Status: researched proposal; implementation and console testing have not started.

## What we are building

An Xbox version of our existing Roku TV Guide: open the app, browse the saved lineup, press A, and watch. Keep the familiar categories, programme grid, dark colors, and clear selection outline. Use large text that can be read from the couch.

The current server remains responsible for the lineup, programme data, source selection, transcoding, and stream lifecycle. The Xbox supplies its own interface and player. Roku, browser, and Xbox viewers must be able to watch independently.

The first working milestone is deliberately small: one real channel playing on the Xbox with sound, a responsive Back button, and correct session cleanup. Playback compatibility is the first technical question to settle.

## Decisions

| Area | Proposed choice | Reason |
| --- | --- | --- |
| Installed app | C# UWP app, x64 package | Microsoft's documented Xbox app deployment path supports our console. |
| Main interface | Native UWP XAML | Gives us direct control over focus, guide rendering, and media controls. |
| Video | `Windows.Media.Playback.MediaPlayer` with `Windows.UI.Xaml.Controls.MediaPlayerElement` | Uses the platform player; test our HLS output before building the full guide. |
| Server connection | Configurable LAN address, saved on the Xbox | Reuse the running guide server and allow its address to change. |
| Initial API | Existing `/api/roku/*` guide and playback endpoints behind a client adapter | The names are Roku-specific, but the payloads and session operations are useful to another client. |
| Shared data | Server remains authoritative | Channel selections and order come from the server; the Xbox stores presentation preferences. |
| First deployment | Signed development package through Xbox Device Portal | A practical personal testing route; Store distribution is a separate project. |

Microsoft documents UWP media apps and manual package loading through Device Portal in [Xbox development options](https://learn.microsoft.com/en-us/windows/uwp/apps-for-xbox/development-options). The UWP [MediaPlayerElement reference](https://learn.microsoft.com/en-us/uwp/api/windows.ui.xaml.controls.mediaplayerelement) identifies the native rendering control. These establish available building blocks; they do not prove that our streams work on this Xbox.

### Web interface alternative

WebView2 is a real alternative if the native guide takes more work than its benefits justify. Microsoft [announced Xbox support in 2023](https://blogs.windows.com/msedgedev/2023/11/01/webview2-for-xbox-announcement/), and its [WinUI 2/UWP setup guide](https://learn.microsoft.com/en-us/microsoft-edge/webview2/get-started/winui2) explicitly covers Xbox. The older Xbox architecture article still says otherwise; use the newer guidance for this point.

An Edge browser test can quickly check the existing web page and local video playback. It will not validate the installed app's permissions, lifecycle, controller focus, or package dependencies. The current browser guide also includes Roku/Cast handoff and cleanup paths, so embedding it would require a separate Xbox playback adapter and a controller-friendly layout. A wrapper alone would not finish the job.

Keep the API models and session controller separate from the UI so a WebView2 alternative would not require redesigning playback ownership. The native version is the proposed default, subject to the first hardware test.

## What we can reuse

These observations come from the current working files, which already contain unrelated uncommitted changes. Future implementation must preserve that work.

| Existing file | Useful behavior |
| --- | --- |
| `api/roku_app.py` | Compact guide, generated sports identity validation, playback start, heartbeat, stop, and movie playback responses. |
| `api/guide.py` | Server configuration, health check, full lineup with revision, and media routes. |
| `media/hls.py` | Live HLS sessions, reference counting for shared producers, recovery, and cleanup. |
| `media/roku_movie.py` | Retained movie segments and private movie sessions that survive pause/resume. |
| `roku-receiver/components/GuideCategories.brs` | Category rules and ordering to reproduce in the Xbox client. |
| `roku-receiver/components/MainScene.brs` | Playback request generations, late-response cleanup, guide restoration, and pause capability handling. |
| `roku-receiver/components/PlaybackLeaseTask.brs` | One background renewal worker per active session. |
| `tests/test_roku_app.py`, `tests/test_shared_media_sessions.py` | Existing contracts and shared-stream behavior to protect when making server changes. |

The compact guide currently includes an eight-hour window. It carries channel numbers, names, groups, logos, play paths, generated sports IDs, movie/sports flags, and programme times. It omits some useful fields from the full guide, including the lineup revision and availability information.

BrightScript and SceneGraph UI code will be translated into Xbox behavior. The server-side Python and media pipeline can stay in place.

## First release experience

### Connect once

First launch shows a large server-address field, Connect, and a short error area. Validate the address, call `/api/guide/ping`, then load the guide. Save a successful address locally. Use the configured media origin when needed and verify that returned media URLs are reachable from the console; an API connection alone is not sufficient.

Subsequent launches open the guide using the saved address. An unavailable server gets Retry and Change server buttons. Previously cached listings may remain visible with an explicit stale indicator. Selecting a cached listing still requires current server validation.

No automatic subnet scan in the first release. The existing server address is sufficient for setup, and a manual field provides a recovery path.

### Browse

The screen has four areas:

1. Header: app name, server connection status, and clock.
2. Categories: All, Network TV, available sports leagues, TV Shows, and Movies, following the current category rules.
3. Guide: channel number/name on the left, programme blocks against a time ruler on the right.
4. Details: selected programme title, start/end, description, and available actions.

Start with roughly six visible channel rows and a two-to-three-hour horizontal window. Treat those as layout starting points. Fit fewer rows when Larger text is selected. Test both normal and larger settings from the actual viewing distance, including a 1080p display.

Use a strong outline plus a background change for focus. Keep the current selection in place across guide refreshes. Remember category, channel identity, and time position when returning from playback. Empty categories and missing programme data remain usable.

The guide should create UI elements for visible rows and the nearby scroll window. It should not create a focusable control for every programme in the entire lineup. Directional movement must choose the programme nearest the remembered time when moving between rows of different programme lengths.

### Watch

Selecting a current programme or channel starts full-screen playback. Selecting a future programme opens its details without launching a different currently airing programme by surprise.

Show Loading until the decoder reports playback and progress is observed. API success means the stream was allocated; it is not proof of picture or sound. Show useful channel-level errors with Retry and Back.

An overlay shows channel, programme, progress where meaningful, and controls that the current session actually supports. Ordinary live channels have no promised rewind. Pause requires both the server's `can_pause` permission and player support. Seeking also requires a real seekable range from the player; `can_pause` alone does not establish it.

Microsoft describes HLS support in its [adaptive streaming documentation](https://learn.microsoft.com/en-us/windows/apps/develop/media-playback/adaptive-streaming). Validate the actual manifests, segment formats, timestamps, live offset, and audio on our console before assuming complete compatibility.

### Controller behavior

| Input | Guide | Playback |
| --- | --- | --- |
| D-pad / left stick | Move through channels, programmes, and buttons | Move through overlay controls |
| A / Select | Play current selection or open future details | Show overlay; activate focused control |
| B / Back | Close a dialog; otherwise let normal app Back behavior apply | Close overlay first; when it is closed, stop this session and return to guide |
| LB / RB | Previous / next category | No hidden playback action |
| LT / RT | Previous / next channel page | No action in the first release |
| X | Programme details | Pause/resume when supported |
| Y | Jump guide to Now | No action in the first release |
| Menu | App settings | Playback options |
| Media Play/Pause/Stop | Applicable player action if a session exists | Through system media controls, respecting session capabilities |

Every shortcut must also have a visible, focusable action so a media remote can use the app. Centralize input routing to prevent one press from triggering both a custom handler and the default control action. Set an intentional repeat rate for held directions and restore focus after dialogs.

Microsoft's [gamepad and remote guidance](https://learn.microsoft.com/en-us/windows/uwp/ui-input/gamepad-and-remote-interactions) covers directional focus and disabling the default pointer mode. Controller use should work without moving a mouse cursor.

## Playback contract and ownership

### Existing calls

| Call | Client use |
| --- | --- |
| `GET /api/guide/ping` | Confirm a responding guide service. |
| `GET /api/guide/config` | Read advertised media origin and network configuration. |
| `GET /api/roku/guide` | Load the compact guide and server time. |
| `POST /api/roku/playback` | Allocate this viewer's stream. Send the supplied play path, mode, and generated identity when present. |
| `POST /api/roku/playback/heartbeat` | Renew this viewer's session using its token, kind, and lease when supplied. |
| `POST /api/roku/playback/stop` | Release exactly that session/lease. |
| Returned `media_url` | Supply to the player without constructing provider URLs on the Xbox. |

Keep `token`, `lease`, `kind`, `is_live`, `can_pause`, `live_delay_seconds`, and `media_url` together as one playback handle. Some successful responses omit capabilities; interpret missing pause capability as false. A response with an empty token has no client-owned allocation to release.

Illustrative request shape, using values obtained from the current guide:

```json
{
  "play_url": "<channel.play_url>",
  "mode": "live",
  "low_latency": false,
  "generated_id": "<channel.generated_id, when present>"
}
```

The session controller owns start, renew, and stop. It must:

- Renew immediately after allocation and then about every 30 seconds, including while paused. Retry renewal failures after about five seconds. These match the current Roku client; the server's current live-lease expiry is 180 seconds.
- Attach a generation number to each start. A late response for a superseded request must be released and must never replace the current channel.
- Release each playback handle at most once. Live producers can be reference counted across viewers, so duplicate token-only stops could harm another owner. Prefer the returned lease whenever available.
- Avoid automatically retrying playback allocation POSTs. An ambiguous timeout may have created a server session; an idempotency mechanism is needed before transparent retries are safe.
- Leave Roku, Cast, and other server playback controls alone. Stopping the Xbox viewer releases its allocation, even when the underlying encoder is shared.
- Keep renewal work off the UI thread and avoid overlapping heartbeat requests.
- Honor `live_delay_seconds` using the platform's live-offset/seekable-window information, clamped to media that actually exists. Measure sports latency separately. Do not copy Roku-specific offset adjustments into the Xbox player without testing them.
- Stop and dispose the player on exit/suspension, with a bounded best-effort release. Server expiry handles crashes or lost network access. On resume, refresh and revalidate before starting another stream.

Use server time to locate Now and programme boundaries, with local timezone formatting for display. Measure elapsed intervals using a monotonic clock so a wall-clock correction does not distort timeouts.

### Small server additions, only when needed

The first playback experiment should use existing routes unchanged. Before the full guide is finished, consider additive compact-guide fields for an opaque stable channel identity, lineup revision, availability, and reason. Never expose a provider URL or credentials as that identity. Resolve generated sports selections using `generated_id`; reusable channel numbers must never silently point a stale selection at another event. The existing playback route already rejects an expired generated identity with HTTP 410.

Keep server data authoritative. Store only server address, text size, and selection hints on the Xbox. A remembered channel must resolve against the fresh lineup. New shared favourites or channel edits, if added later, belong in server storage with revision checks.

## Proposed code layout

```text
xbox-receiver/
  TvGuide.sln
  TvGuide/
    App.xaml / App.xaml.cs
    Package.appxmanifest
    Models/                  Guide, Channel, Programme, PlaybackHandle
    Services/                GuideApiClient, PlaybackSessionController,
                             PlayerController, LocalPreferences
    ViewModels/              Connection, Guide, Player
    Views/                   ConnectionPage, GuidePage, PlayerPage
    Controls/                ProgrammeRow, CategoryStrip, PlayerOverlay
    Input/                   InputRouter, GuideFocusController
    Assets/
  TvGuide.Tests/              Contract/state tests supported by the chosen toolchain
  README.md                  Build, package, install, and rollback instructions
```

Use UWP project tooling and namespaces. A desktop WPF, WinUI 3, or MAUI project is not an interchangeable Xbox deployment target. Select the SDK minimum/target and package dependencies during the build experiment, then pin the working versions. Keep signing keys outside the repository.

Declare the required LAN/network capabilities and validate them on Xbox. The local server remains the boundary for provider credentials. Diagnostics should include player state, timings, HTTP status, and sanitized errors, with session tokens and provider URLs removed.

## Build and test sequence

| Stage | Deliverable | Exit condition |
| --- | --- | --- |
| 1. Build preparation | Minimal x64 UWP project, dependency inventory, package recipe | Clean build and signed package on a Windows development machine. |
| 2. Console playback experiment | Address field, one-channel list, native player, session controller | Real picture and sound; Back works; allocation is released correctly. |
| 3. Guide | Categories, programme grid, details, large text, focus restoration | Normal browsing and channel selection work with controller and remote. |
| 4. Reliability | Failure recovery, request cancellation, suspend/resume, sanitized diagnostics | Rapid channel changes, connection loss, and app exit do not leak sessions or stop other viewers. |
| 5. Movie behavior | Pause/seek where supported, restart from beginning, return to live | Retained and finite streams behave correctly across pauses and programme boundaries. |
| 6. Personal release | Release package, install instructions, known limitations | Repeatable install and update on the target Xbox; hardware checklist completed. |

Do not spend time reproducing Sports Map or lighting before Stage 2 passes. If native playback fails, capture the precise manifest/player error and compare with WebView2 playback before changing the server pipeline. Add an Xbox-specific media profile only for a measured incompatibility; preserve Roku behavior.

### Hardware verification checklist

- Normal live channel, generated sports feed, movie channel, and finite restarted movie tested separately.
- At least 30 minutes of continuous playback without an app crash or recurring client-caused stalls; record source failures separately.
- Measure time to first visible frame and audible sound over several starts. Record median and slow cases before setting a performance target.
- Confirm pause and seek against the actual retained window, including a pause long enough to cross a programme boundary.
- Run 20 rapid channel changes, cancel during allocation, and verify late responses cannot start the wrong channel.
- Watch the same channel on Roku and Xbox, then stop each viewer in turn; the remaining viewer continues.
- Test a missing channel, stale generated sports ID, empty guide, server timeout, lost Wi-Fi, and an expired session.
- Refresh the lineup while browsing; focus stays sensible and deleted entries are not revived.
- Test Xbox Home/app switching, suspension, termination, and reopening without persistent unwanted audio or session renewal.
- Test TV overscan, 1080p/4K output, larger text, held buttons, controller disconnect, and every screen using only directional/select/back input.

Use targeted automated tests for category parity, time-to-column calculations, generated identity handling, late-response cleanup, and exactly-once release. Reuse the existing server contract and shared-media tests if server code changes. This plan itself requires no running service tests.

## Installation and the main practical drawback

Microsoft's [Developer Mode activation documentation](https://learn.microsoft.com/en-us/previous-versions/windows/uwp/xbox-apps/devkit-activation) covers retail Xbox One and Series X|S consoles. Developer Mode uses a registered developer account and console activation. Switching between Developer and Retail modes restarts the console, and retail games/apps do not run in Developer Mode.

This makes personal sideloading suitable for testing but potentially awkward for everyday TV on a gaming console. If switching modes is unacceptable, evaluate the browser experience first and consider a properly published retail app separately. A retail release would need its own distribution and certification work; it is not promised by this plan.

When the user is ready: confirm account/activation prerequisites, confirm the exact console, and prepare the build before requesting any mode switch. Use the authenticated [Xbox Device Portal](https://learn.microsoft.com/en-us/previous-versions/windows/uwp/xbox-apps/device-portal-xbox) address shown by Dev Home to install the signed package and required dependencies. Existing DIAL/SmartGlass remote access does not establish deployment access.

## Later features

Sports Map using the existing map API; DVR library and recording actions after reviewing their contracts; search; optional channel-up/down shortcuts; and optional Afterglow movie/sports lighting.

Lighting needs an explicit Xbox-to-room association. The current Roku lighting integration uses Roku-specific configuration, so it cannot simply inherit the console's IP as a room choice. Keep lighting independent of buffering and retry events, and make restoration/session ownership work across multiple viewers before enabling it.

Multiview, background audio, phone-to-Xbox casting, cloud account integration, and Store publication are separate follow-ups.

## Current limits and next action

Previous network discovery identified a Series X and working YouTube app launch/control paths. It did not establish app-install access or test our guide streams. No Xbox guide package has been built or loaded.

The planning-time inventory found no `dotnet` or `msbuild` command on PATH, no Visual Studio Installer `vswhere.exe` at its standard location, and no Windows SDK Include directory at its standard location. This is a limited check, not proof that all build tools are absent. Build preparation must locate a working UWP toolchain or arrange one before a package is promised.

Only documentation was added for this task. No toolchain was installed, console mode changed, light controlled, stream started, or service restarted. Any later service restart requires the exact-port playback check and fresh explicit approval required by the user's restart instructions.

Next implementation action: make the smallest UWP playback client against the existing API, validate the build, then arrange the supervised console test. The rest of the app follows after that test supplies real playback evidence.
