# OmniSportsSwitch — Plan

## 0. Resume here (read this first)

**State (2026-10-11, just after midnight).** Everything described below is built, committed and pushed on the branch `phase-0-1-spike-and-daemon` of the private repo `saddi-a/OmniSportsSwitch` (`main` only has GitHub's first commit). `pytest` passes (153 tests) plus 1 expected failure (the UCF scoreboard layout, on purpose).

**Running it.**
* `scripts\start_all.ps1` starts the daemon (its own window, **minimized; closing it stops the daemon**) and the test Edge profile (separate profile at `%LOCALAPPDATA%\OmniSportsSwitch\edge-profile`, debug port 9222, extension preloaded). It skips whatever already runs. `scripts\stop_all.ps1` stops the daemon.
* **When a tab needs tracking again:** only when its page reloads (single view to multiview and back, a new game, a refresh); changing layout or adding/removing tiles inside a loaded multiview needs nothing, and the extension itself is never reloaded for this. Press **`Ctrl+Shift+Y`** (or the popup's "Re-track this tab") in that window: it tracks the visible tab of the window you are in.
* After changing **daemon or settings** code: stop and start the daemon. After changing **extension** files: the user presses "Reload extension" in the popup (reloading drops tracking), then clicks "Track this tab" on every stream tab again (Edge requires that click). Window roles come back by themselves.
* Tests: `.venv\Scripts\python.exe -m pytest daemon/tests -q`.
* Logs: `logs/events.log` (goals, swaps and why, tile focus, game states, freeze, big plays, sport detections, warnings), `logs/daemon.log` (everything, incl. OCR text per video area every ~3 s), `logs/boxes.log` (size and position of every OCR item, for tuning). The debug port 9222 can be used (read-only) to inspect the test profile's pages.
* Real samples of a scoreboard: `.venv\Scripts\python.exe scripts\capture_samples.py "label"` (saves to `samples/`, untracked).

**Pending right now.** The user put a DAZN **multiview on the Maryland @ Ohio State game** (3 tiles) to test with. The extension's reporter is lost when a page reloads, so the multiview tab must be tracked again (popup -> Track this tab) before the daemon reports its tile positions. Then check: each tile read separately (clocks, scores), score changes and breaks per tile, tile focus / expand on a goal, the negative ad score on a tile, big-play banners.

**Next, in order of value.** (1) the multiview live test above; (2) more real scoreboard samples (NFL, Champions League, national-team matches, other leagues) via `capture_samples.py`, then tune the reader (`daemon/score_watch.py`) on them and on `logs/boxes.log`; (3) per-team allowed slots and a popup editor for `settings.toml`; (4) setup script, README and licence, then the public repo as a single clean commit (see section 7).

**Working agreements with the user.** The user is new to web development, knows Python and C. For anything architectural or about design, ask first with options, pros and cons and a recommendation; implementation details are mine to decide, with a short explanation. Keep replies short and plain. The repo is private: commit on the feature branch (never on `main`), push the branch freely, never force-push. Commit with `git -c user.name=saddi-a -c user.email=72130894+saddi-a@users.noreply.github.com` (never edit git config); the `Co-Authored-By: Claude Sonnet 5.5` trailer is fine because history is dropped when publishing. Keep this plan up to date as things change.

**Gotchas learned the hard way.**
* In Python strings a `\b` becomes a backspace character; a stray `0x08` once silently broke a regex. Use raw strings or lookarounds, and write multi-line patches as a script file, not inline in the shell.
* Source files have Windows line endings, so multi-line text edits can fail to match; patch from Python (read bytes, replace, write bytes) when that happens.
* Installing a package that depends on PyTorch can replace the GPU build with a CPU one. Install `torch` and `torchvision` from the CUDA index first (`requirements.txt` explains), and use `--no-deps` for the rest.
* Hardware acceleration must stay off in Edge, and no other window (including the daemon's console) may sit on top of a stream, because OCR works from screen capture.
* Old background monitors watch old log files; arm new ones on `logs/events.log`, which survives restarts.

**File map.** `daemon/` (server, scoring, game_state, score_watch, sport_detect, favourites, arrange, multiview, screen_ocr, audio_classifier, settings, logsetup, tests/ with real-sample fixtures); `extension/` (manifest, background.js service worker, offscreen.js, popup, pcm-processor.js, defaults.js); `settings.toml`; `scripts/`; `spike/` (Phase 0 throwaway tests); `models/` (not tracked: PANNs checkpoint, EasyOCR models); `logs/`, `samples/` (not tracked).

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
2. **Muting secondary streams:** Web Audio gain nodes. Alternatives considered: `video.muted = true` (some players pause), or `chrome.tabs.update({muted: true})` (may cut off the audio before `tabCapture` can measure it). Validated in Phase 0: the sound goes silent, the level is still measured and the players do not pause.

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
* **Roles belong to windows, not tabs.** The user marks each window once with its role in the extension popup; a tab's role is the role of the window it is currently in, and a swap moves tabs between the fixed windows. Window ids change when Edge restarts, so the extension also remembers where each role's window sits on screen and gives the role back to the window at that position. Each slot window should hold exactly one stream tab.
* **Total slots:** 5 (`MAIN`, `LEFT`, `RIGHT_TOP`, `RIGHT_MID`, `RIGHT_BOT`), but `RIGHT_TOP` is a rarely-used 5th slot in the vertical monitor's top third. In normal use only 4 slots are active.
* **Auto-fill order for new streams into empty slots:** `LEFT → RIGHT_BOT → RIGHT_MID → RIGHT_TOP`. `MAIN` is not part of this queue — it's only ever reached via excitement-based promotion, never direct assignment.

### Priority should be automatic and simple for the user
Priority by sport, favourite teams and game status (live / break / over) should come from live information (audio and picture) with minimal manual input. Target behaviour: OCR on the screen capture detects teams, game clock, score changes and break/final text; a stopped clock or missing scorebug marks a stream as on a break or over. The user only maintains a short favourites list and sport weights. This is built: sport, favourite teams and game status are detected; the manual per-tab sport, teams and Live/Break/Over (Auto/Live/Break/Over) selectors in the popup are only overrides.

## 4. Core Architecture & Zero-Lag Strategy

### The Spoiler Problem
OTT web streaming services (Disney+/ESPN, DAZN, Canal+) lag 20–60 seconds behind real-time. Relying on external live sports APIs for triggers causes premature switches that spoil the play before it appears on screen.

### The Solution: Stream-Synchronized Local Telemetry
The system relies exclusively on local browser data that matches the exact video stream buffer:
1. **Tab audio:** captured per tab with `chrome.tabCapture`, streamed to the daemon as 32 kHz audio. The daemon measures loudness and classifies the sound (cheering, crowd, speech, music) with a PANNs model on the GPU.
2. **Local CUDA OCR:** the daemon screenshots the screen area where each video plays (page-level frame capture is blocked by DRM) and reads the scorebug with EasyOCR on the RTX 3080.

```
┌──────────────────────────────────────┐          ┌──────────────────────────────────────┐
│     MICROSOFT EDGE EXTENSION         │          │   LOCAL PYTHON DAEMON (CUDA / CPU)   │
│                                      │          │                                      │
│ ┌──────────────────────────────────┐ │  Web-    │ ┌──────────────────────────────────┐ │
│ │ Tab audio + video-area reporter  │ │  Socket  │ │ PANNs audio + EasyOCR (RTX 3080) │ │
│ │ (32 kHz audio, tile positions)   │ ├─────────►│ │ (screen capture of video areas)  │ │
│ └──────────────────────────────────┘ │ (ws://)  │ └─────────────────┬────────────────┘ │
│                                      │          │                   │                  │
│ ┌──────────────────────────────────┐ │          │ ┌─────────────────┬────────────────┐ │
│ │ Arranger + tile expander         │ │◄─────────┤ │ Scoring Logic (9800X3D Multi-Th) │ │
│ │ (tabs.move, debugger click)      │ │  Events  │ │ (crowd, surge, score, game state)│ │
│ └──────────────────────────────────┘ │          │ └──────────────────────────────────┘ │
└──────────────────────────────────────┘          └──────────────────────────────────────┘
```

## 5. Scoring Logic (as built)

Score of a stream = `priority × (max(0, 0.3·surge + 1.0·crowd − 0.2·speech − 0.5·music + 0.8·event) + 0.4·(favourite − 1)) × status`

* **surge:** recent loudness (3 s average) relative to that stream's own long-term average (60 s), so streams of different loudness are comparable and steady commentary scores zero.
* **crowd / speech / music:** classifier probabilities (cheering, crowd, applause / speech / music). Ads and jingles are suppressed by the music term, commentary by the speech term.
* **event:** a confirmed score change read by OCR, fading over 45 s. The score is found next to the team names (or as a small `a-b` token; a clock misread as `31-56` is ignored), each side's value is voted over the last 5 readings (3 must agree), and a goal is a stable change where a side rose by 1 to 10. Logo noise such as `1+3` read as `13`, stat graphics and lone numbers do not count. If the game clock jumps more than the elapsed time allows (a different game moved into the slot, or a rewind; confirmed on two readings so a stray kickoff time does not count), the baseline is thrown away. Tested by replaying real readings from the Napoli and PSG goals.
* **status:** live 1.0, break 0.25, over 0. Detected from the picture (`Auto`) or set by hand (`Live / Break / Over` override). Rules per sport: soccer clock frozen 20 s, American football 4 min, basketball 3 min, generic 2 min; "no readable clock" for 45–90 s; standalone repeated captions such as FINAL, FULL TIME, HALF TIME, MI-TEMPS.
* **priority:** tab weight × sport weight (set in the popup).
* **Favourite teams:** a live stream with a favourite team gets a steady bonus of 0.4 per extra ×1 of the multiplier, so a ×2 favourite is on `MAIN` by default and keeps it while nothing exciting happens elsewhere. A goal (0.8) or a real crowd roar on another stream beats it, and a break or end of game removes the bonus (status multiplier). A favourite is recognised from the scoreboard text read by OCR (enter the short name shown on screen, e.g. PSG) or from the tab's teams field, and counts for 60 s after it was last read.
* **Swap rule:** a challenger must beat `MAIN` by +40% and by at least 0.15, score at least 0.35 (loudness alone, capped at +0.3, is never enough), have finished a 20 s warm-up, and `MAIN` must not be warming up itself, and 20 s must have passed since the last swap. Every swap logs the signals behind it. Weights and thresholds are in `settings.toml`.
* **Freeze (`Ctrl+Shift+P`):** while frozen, `MAIN` is locked (no swaps into it, no tile changes inside it). The best challenger, by the same rules and thresholds, takes the `LEFT` window instead, so a goal elsewhere is still shown without touching `MAIN`. Unfreezing returns to normal swaps.
* **Ads score negative:** a stream whose player shows its ad placeholder scores -0.5 (whatever its audio says); music-dominated sound without a crowd scores -0.25. In a multiview tab a tile on an ad gets negative interest too. Negative streams never win a swap and `MAIN` hands over to the best live stream. The penalty is `ad_penalty` in `settings.toml`; the log shows `AD` for such streams.
* **Ad memory:** the music probability flickers (0.8, 0.8, 0.2, 0.9...), so a stream that sounded like an ad (music, no crowd) stays flagged as one for 12 s (`audio_ad_memory_s`), and a stream flagged that way is never chosen to take over from an idle `MAIN`. Streaming-service promo screens ("WATCH NOW", "ON DAZN", FIFA+ and NFL Game Pass promos) are in the ad phrases too. This was fixed after the daemon handed `MAIN` to a stream that was in the middle of a FIFA+ ad because one reading dipped.
* **Idle MAIN:** if `MAIN` is on an ad (music probability 0.5 or more) or is on a break or over, the best live stream without music takes over, even if nothing exciting is happening (the 20 s gap between swaps still applies). Ads on every stream mean no swap.
* **Allowed slots:** a tab can be restricted to some slots (for example PSG only on MAIN or LEFT). The daemon plans the arrangement (`daemon/arrange.py`): a straight trade if allowed, otherwise a three-way rotation through an allowed slot, otherwise the swap is refused. A stream not allowed on MAIN is never promoted.
* **Audio:** only the `MAIN` window is audible; the other tabs are muted with Web Audio gain nodes (measured before the gain, players do not pause).
* **Swap mechanics:** roles belong to windows; a swap moves tabs between fixed windows with `chrome.tabs.move`, keeping a spare tab in each window so none closes.

## 6. DAZN multiview (built; live test on the Ohio State multiview pending)

* The extension reports every playing `<video>` (a tile) with its position and whether it has sound; the daemon reads each tile separately (OCR, game state, score changes).
* **Tiles are scored from the picture only** (score change, game state), plus crowd sound for the tile that has audio focus. Rotating focus to sample other tiles' audio was rejected.
* **Tile focus:** if another tile has a fresher score change (and is not on a break), the daemon asks the extension to expand it. Minimum 30 s between changes per tab.
* **Expanding needs a genuine mouse click.** A scripted click only moves the sound. The extension therefore uses Edge's debugging API (`debugger` permission) to hover and click, then detaches; Edge shows its "debugging this browser" bar for a moment. A script click remains as a fallback.
* **The click needs a second small mouse move.** The expand icon appears under a pointer that is already still, so DAZN does not count it as hovered and the click only *selects* the tile (the sound moves to it). The extension moves the pointer a few pixels after the icon has appeared, then clicks; found when a live expand only selected a tile.
* **How to tell whether an expand happened:** slots keep their size and the *streams* move between them, so tile sizes never change. An expand puts the sound on the big (main) slot; a click that only selected leaves it on the clicked tile. The daemon checks this on the next video report (`_check_expand`) and swaps its per-tile records only for a confirmed expand (a warning `only selected` is logged otherwise).
* **Slots swap streams.** In the main + sidebar layout, expanding a tile moves its stream into the big slot and the old main stream into the clicked slot, so the daemon swaps its per-tile records the same way.
* Seen live: the daemon swapped tile records and moved focus when a tile expanded, and tab-level swaps from goals inside a multiview tab. Not yet seen: a goal in a *side* tile expanding that tile on its own.

## 7. Known risks & open items

1. **Hardware acceleration must stay off in Edge** (the user's setting). With it on, screen capture of DRM video may go black. Re-check if it ever changes.
2. **Per-session setup:** tabs must be tracked again after any restart (one click per tab; Edge requires the click) and the freeze is reset. Window roles come back automatically: the extension remembers where each role's window sits on screen and gives the role back to the window at that position. If you move a window, its remembered position updates.
3. **OCR reads everything in the video area**, including ad boards, popups and graphics. Mitigations in place (standalone repeated captions, score pairs, thresholds), but false readings can still happen. Watch the daemon log (`score change`, `game state`, `focus tile`).
4. **Not built yet:**
   * **Samples of real scoreboards.** Online there are only free-licensed mock-ups (Wikimedia Commons "Score bugs": 7 files, CC BY-SA); broadcast screenshots are copyrighted. So samples come from the user's own streams: run `.venv\Scripts\python.exe scripts\capture_samples.py "NFL Fox"` while a game is on (saves screenshots and OCR results to `samples/`, not tracked by git); copy only the `.json` into `daemon/tests/fixtures/` to make a test. Wanted: NFL (Fox, CBS, NBC, Amazon, ESPN), college football (ESPN, Fox, CBS, Big Ten), top-5 leagues, other major leagues, Champions League, Euros, AFCON, World Cup, Nations League.
   * **More scoreboard layouts:** no NFL or national-team samples yet; the UCF layout still reads a logo as a score (expected-failure test); the score reader needs tuning on the data in `logs/boxes.log`. A "TEAM 12" single-item layout (Army-Navy mock-up) is now supported.
   * **Per-team allowed slots** ("PSG only on MAIN or LEFT" by team name instead of per tab) and a popup editor for `settings.toml` (settings are read at daemon start only).
   * **Setup for other people:** a setup script (venv, CUDA PyTorch, model downloads), a README and a licence, then the clean single-commit public repo.
5. **Verified live so far:** goals on non-MAIN streams promote them, the allowed-slot rotation (PSG moved to LEFT), idle MAIN handover on ads, favourite teams staying on MAIN, the freeze hotkey, window roles restored after a reload, sport detection, commercial placeholder detection. **Not yet seen live:** a goal in a multiview side tile expanding that tile, the freeze sending the winner to LEFT, and the negative ad score during a real ad.
6. **Publishing later:** create a new public repo with a single clean commit (no history). Models (`models/`) and the venv are not tracked.

## 8. Roadmap and status

* **Phase 0 — DRM feasibility spike: done.** Page frame capture is blocked on DAZN, Canal+ and Disney+; screen capture works; `tabCapture` audio works on all three, also from hidden tabs; gain-node muting works. Scripts in `spike/`.
* **Phase 1 — Daemon and scoring: done.** `daemon/` (async WebSocket server, pure tested modules: `scoring`, `game_state`, `score_watch`, `sport_detect`, `favourites`, `multiview`, `arrange`, `screen_ocr`, `audio_classifier`, `settings`). 153 tests plus 1 expected failure.
* **Phase 2 — Audio classification and OCR: done.** PANNs CNN14 and EasyOCR run locally on the RTX 3080 (about 5 ms and 0.2 s per pass).
* **Phase 3 — Extension: done.** Popup (window role, tracking, status, sport, teams, weight, allowed slots, priorities, reload), offscreen page (audio, WebSocket), service worker (tracking, arrange, tile expand).
* **Phase 4 — Integration: done and running.** Audio, OCR, scoring, window swaps, muting and multiview focus work together.
* **Phase 5 — Safeguards and polish: in progress.** Done: freeze hotkey (`Ctrl+Shift+P`, LOCK badge), permanent logs (`logs/daemon.log`, `logs/events.log`), window roles remembered across restarts. Sport detected from the picture and the one-command start are done (`scripts/start_all.ps1` starts the daemon and the test Edge and skips what already runs; `scripts/stop_all.ps1` stops the daemon). The settings file is done (`settings.toml`: every threshold, weight and break timing, read at daemon start). Commercial placeholders ("COMMERCIAL BREAK IN PROGRESS", "PUBLICITARIA EN CURSO"...) are detected as a break at once and their countdown is ignored (phrases in `settings.toml` [banners]). The score is now also read from the size and position of the text (score digits are the biggest numbers near the team names; this fixed Texas-Oklahoma, where jersey numbers were read as the score), with the text-only reader as a fallback. Known failure: the UCF layout, where a logo is read as a big digit (an expected-failure test records it). `logs/boxes.log` keeps the size and position of every OCR item for tuning. Ads and breaks now score negative. Big-play banners (TAKEAWAY, INTERCEPTION, FUMBLE, SACK, RED CARD...) boost a stream by 0.7 of a goal (`big_play_strength`, words in `settings.toml`). Open: more layouts (NFL, national-team matches: no samples yet).
