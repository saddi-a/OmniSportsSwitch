# OmniSportsSwitch — Plan

## 1. Project Overview & Objective

**OmniSportsSwitch** is an automated multi-stream sports switching system for a multi-monitor setup. It acts as an automated live broadcast director: it continuously monitors crowd noise and visual game state across multiple browser streams, calculates a dynamic excitement score, and automatically promotes the most intense game onto the user's main center display.

**Requirement: everything runs locally.** No cloud services, accounts or API calls. Models (audio classifier, OCR) run on the user's own GPU. The only network use is one-time downloads of libraries and model files, after which the system works offline apart from the streams themselves.

## 2. Hardware & Environment Specifications

* **Operating System:** Windows 11 Pro (build 26300, 64-bit)
* **Motherboard:** ASRock B650 LiveMixer
* **CPU:** AMD Ryzen 7 9800X3D, 8 cores / 16 threads (low-latency multi-threaded backend logic and WebSocket data sync)
* **RAM:** 32 GB (2x16 GB DDR5-4800)
* **GPU:** NVIDIA RTX 3080, 10 GB VRAM, driver 610.74, compute capability 8.6 (hardware-accelerated local OCR / CV inference via CUDA/PyTorch). A Parsec virtual display adapter is also installed and may appear as a phantom monitor.
* **Storage:** Samsung 990 EVO Plus 2 TB (NVMe), Samsung 870 QVO 2 TB (SATA SSD), Seagate 2 TB HDD
* **Browser:** Microsoft Edge 155 (Chromium-based, Manifest V3 extensions)
* **Backend Runtime:** Python 3.12.10 (installed machine-wide at `C:\Program Files\Python312`); PyTorch's pip wheels bundle CUDA, so no separate CUDA toolkit is needed
* **Other tools:** Git 2.56. Node.js is not installed and not needed.
* **Extension tooling:** Plain JS/HTML/CSS, no build step — load files directly via `manifest.json`

## 2b. Provisional Architecture Decisions

Both of these are accepted for now and can be revisited if they don't work out:

1. **OCR location:** Python daemon on the GPU. **Updated after the Phase 0 spike:** frames come from the daemon screenshotting each slot's screen region itself, not from the extension, because DRM blocks reading frames out of the page (see §8, Phase 0 results). Alternatives considered: JS-only OCR inside the extension (weaker, and blocked by the same DRM issue).
2. **Muting secondary streams:** Web Audio gain nodes. Alternatives considered: `video.muted = true` (some players pause), or `chrome.tabs.update({muted: true})` (may cut off the audio before `tabCapture` can measure it). Needs testing alongside the Phase 0 spike.

## 3. Display Layout & Window Roles

Three physical monitors, arranged left to right as **Screen 3 → Screen 1 → Screen 2**:

| Screen | Size | Orientation | Role(s) |
|---|---|---|---|
| Screen 3 (left) | 24" 1080p | Landscape | `LEFT` |
| Screen 1 (middle) | 27" 1440p | Landscape | `MAIN` — the auto-swap promotion target |
| Screen 2 (right) | 27" 1440p | Portrait/vertical | Bottom two-thirds split into `RIGHT_MID` (upper) and `RIGHT_BOT` (lower), stacked. Top third is normally unused. |

```
Screen 3        Screen 1              Screen 2
(1080p)         (1440p, MAIN)         (1440p vertical)
┌────────┐      ┌──────────────┐      ┌──────────────┐
│        │      │              │      │ RIGHT_TOP    │ ← rarely used
│  LEFT  │      │     MAIN     │      ├──────────────┤
│        │      │              │      │  RIGHT_MID   │
│        │      │              │      ├──────────────┤
└────────┘      └──────────────┘      │  RIGHT_BOT   │
                                       └──────────────┘
```

* **No fullscreen on `LEFT` and `MAIN`:** the user can't use fullscreen there, and a tab swap exits video fullscreen anyway. Windows are maximized with Edge's tab strip and address bar visible, so OCR screen capture must locate the video area inside each window rather than use fixed screen coordinates.
* **Roles belong to windows, not tabs.** The user marks each window once with its role in the extension popup; a tab's role is the role of the window it is currently in, and a swap moves tabs between the fixed windows. Window ids change when Edge restarts, so roles are re-marked each session. Each slot window should hold exactly one stream tab.
* **Total slots:** 5 (`MAIN`, `LEFT`, `RIGHT_TOP`, `RIGHT_MID`, `RIGHT_BOT`), but `RIGHT_TOP` is a rarely-used 5th slot in the vertical monitor's top third. In normal use only 4 slots are active.
* **Auto-fill order for new streams into empty slots:** `LEFT → RIGHT_BOT → RIGHT_MID → RIGHT_TOP`. `MAIN` is not part of this queue — it's only ever reached via excitement-based promotion, never direct assignment.

### Priority should be automatic and simple for the user
Priority by sport, favourite teams and game status (live / break / over) should come from live information (audio and picture) with minimal manual input. Target behaviour: OCR on the screen capture detects teams, game clock, score changes and break/final text; a stopped clock or missing scorebug marks a stream as on a break or over. The user only maintains a short favourites list and sport weights. Manual per-tab sport, teams and Live/Break/Over selectors already exist in the popup as stage A and become overrides once detection works.

## 4. Core Architecture & Zero-Lag Strategy

### The Spoiler Problem
OTT web streaming services (Disney+/ESPN, DAZN, Canal+) lag 20–60 seconds behind real-time. Relying on external live sports APIs for triggers causes premature switches that spoil the play before it appears on screen.

### The Solution: Stream-Synchronized Local Telemetry
The system relies exclusively on local browser data that matches the exact video stream buffer:
1. **Audio RMS Peak Detection:** real-time crowd/commentary decibel spikes captured directly from the tab's audio pipeline via `chrome.tabCapture` and the Web Audio API.
2. **Local CUDA OCR:** periodic frame grabs from the video element, processed locally on the RTX 3080 via EasyOCR, to read the scorebug synchronously with the stream.

```
┌──────────────────────────────────────┐          ┌──────────────────────────────────────┐
│     MICROSOFT EDGE EXTENSION         │          │   LOCAL PYTHON DAEMON (CUDA / CPU)   │
│                                      │          │                                      │
│ ┌──────────────────────────────────┐ │  Web-    │ ┌──────────────────────────────────┐ │
│ │ Tab Audio RMS & Canvas Capture   │ │  Socket  │ │ PyTorch / CUDA OCR Engine        │ │
│ │ (Extracts frames & audio volume) │ ├─────────►│ │ (EasyOCR / PaddleOCR on RTX 3080)│ │
│ └──────────────────────────────────┘ │ (ws://)  │ └─────────────────┬────────────────┘ │
│                                      │          │                   │                  │
│ ┌──────────────────────────────────┐ │          │ ┌─────────────────┬────────────────┐ │
│ │ Window Swapper Controller        │ │◄─────────┤ │ Scoring Logic (9800X3D Multi-Th) │ │
│ │ (Executes chrome.tabs.move)      │ │  Events  │ │ (Audio dB + Game Clock/Score Delta)│ │
│ └──────────────────────────────────┘ │          │ └──────────────────────────────────┘ │
└──────────────────────────────────────┘          └──────────────────────────────────────┘
```

## 5. Scoring Logic

Excitement combines audio intensity with an OCR-derived "momentum" signal:

$$\text{Excitement Score} = (\text{Audio RMS} \times \text{Priority Weight}) + f(\text{OCR score delta}, \text{OCR clock remaining})$$

* The exact weighting of the OCR term is TBD pending the Phase 0 feasibility spike (§8) — if frame capture doesn't work on DRM-protected players, the OCR term is dropped and scoring falls back to audio-only (`Audio RMS × Priority Weight`).
* **Trigger rule:** if a secondary slot's score exceeds `MAIN`'s score by a safety threshold (e.g. +40%), the daemon commands the extension to execute a swap.
* **Audio management:** auto-unmutes the active `MAIN` stream while muting secondary streams via Web Audio gain nodes (muting via gain rather than `video.muted` avoids pausing DRM-protected HTML5 players that pause on mute).
* **Window-swap semantics:** `chrome.tabs.move()` moves tabs between/within windows; it does not reposition or resize OS-level windows. "Promoting to MAIN" means moving the winning tab into the window assigned to the `MAIN` role (and relocating the bumped tab to the role the promoted tab vacated), not changing window geometry.

## 6. Planned later (must not be forgotten)

* **DAZN multiview** (up to 4 sub-streams tiled inside one DAZN tab, with click-to-focus audio per tile and two layout modes: equal grid vs. main+sidebar). The user wants this supported eventually. Today every tab, DAZN included, is treated as a single stream. Findings so far:
  * **Picture:** screen capture reads every tile (verified: main tile and both side tiles returned real pixels, scorebug readable). So OCR and the image-based priority work per tile once the video-area step can report each tile's rectangle (several `<video>` elements, not just the biggest one).
  * **Audio:** `tabCapture` hears only the tile that currently has sound (the others are muted inside the page). Per-tile loudness and crowd detection is not possible with the current approach; tiles other than the focused one would have to be scored from the picture only (score changes, clock), or the extension would click tiles to rotate audio focus.
  * **Layouts:** equal grid vs. main+sidebar changes tile sizes; tile rectangles must come from the page, not fixed regions.
  * Needs a design decision (tile = separate stream in the scorer? what does "promote" mean for a tile: click it in DAZN, or move the whole tab?) before building.

## 7. Known Risks & Open Questions

1. **DRM frame capture (resolved for DAZN and Canal+, not yet for Disney+/ESPN).** Reading frames from the page via canvas is blocked by DRM, so OCR uses screen capture instead. Remaining consequences: a window covered by another window can't be read, and the whole approach relies on **hardware acceleration staying disabled in Edge** (the user's normal setting). Re-check if that ever changes.
2. **`tabCapture` on unfocused/background tabs.** Needs confirmation that Edge allows simultaneous audio capture from up to 5 tabs that aren't the focused tab/window.
3. **Scorebug is not proof of live play (deferred, low priority).** Scorebugs stay on screen during breaks, half-time and studio segments, and may show a different match than the picture (seen on Canal+). OCR scoring must use score changes and a running game clock, not just the presence of a score. Design to be decided later.
4. **Sport-type assignment and priority weight defaults.** Not yet decided whether these are set manually per tab via the popup UI (current assumption, matching the original roadmap) or inferred automatically.

## 8. Implementation Roadmap

* **Phase 0 — DRM feasibility spike (scripts in `spike/`).**
  * **Status: complete.** All tests ran with hardware acceleration off (the user's normal setting).
  * **Video:** page-level frame capture (`drawImage`) returns blank frames on **DAZN, Canal+ and Disney+**. Screen capture of the Edge window returns real pixels with a readable scorebug on all three. ESPN-specific not tested separately (Disney+ carries the ESPN content).
  * **Audio:** `chrome.tabCapture` measures audio RMS on all three DRM services, including when the stream tab is hidden behind another tab. The measurement stays accurate with the output muted.
  * **Muting (decision 2, validated):** a Web Audio gain node set to 0 silences the tab, RMS (measured before the gain) keeps reading, and the players do not pause.
  * **Constraints found:** (1) the user must click the extension icon once on each tab to start capture (browser requirement, probably again after a reload); (2) a hidden tab renders nothing on screen, so OCR can only read streams in visible windows, which fits the 5-window layout; (3) the extension cannot see tabs it hasn't been clicked on.
  * *Test setup:* a separate Edge profile launched with `--remote-debugging-port=9222` (Edge ignores that flag on the default profile), the throwaway extension in `spike/tabcapture_test/`, and helper scripts in `spike/`.
* **Phase 1 — Python daemon skeleton. Done (audio-only).** One async process (`daemon/server.py`, WebSocket on `ws://127.0.0.1:8765`) wrapping a pure, tested scoring module (`daemon/scoring.py`). Scoring averages each stream's audio over a 3 s window, applies its priority weight, and promotes a challenger that beats `MAIN` by +40%, with a 20 s minimum gap between swaps, a silence floor, stale-stream handling and a freeze flag (all configurable). Streams are keyed by tab id so weights follow tabs between slots. Message formats are documented at the top of `server.py`. 13 tests pass (`pytest daemon/tests`). The OCR inputs (score change, game clock) are not wired in yet and arrive with Phase 2.
* **Phase 2 — OCR pipeline.** EasyOCR on the RTX 3080; per-service region-of-interest cropping (scorebug position differs across ESPN/DAZN/Canal+), validated against saved screenshots before live capture.
* **Phase 3 — Edge extension skeleton.** Plain JS/HTML, no build step: `manifest.json` (permissions: `tabs`, `tabCapture`, `offscreen`, `storage`), `service-worker.js`, offscreen document for `tabCapture` + Web Audio RMS, popup UI for assigning the 5 slot roles and priority weights.
* **Phase 4 — Integration.** Extension streams audio RMS to the daemon over WebSocket (frames come from the daemon's own screen capture, not the extension); daemon scores and emits swap commands; extension executes `chrome.tabs.move()` plus mute/unmute gain-node logic, following the slot fill order from §3.
* **Phase 5 — Safeguards.** Manual override/freeze hotkey (`Ctrl+Shift+P`) to lock `MAIN`; persist slot role mappings and priority weights in `chrome.storage.local`.
