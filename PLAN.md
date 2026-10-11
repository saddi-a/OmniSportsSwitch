# OmniSportsSwitch — Plan

## 0. Resume here (read this first)

**State (2026-10-11, about 02:20).** Everything is built, committed and pushed on the branch `develop` of the private repo `saddi-a/OmniSportsSwitch` (called `phase-0-1-spike-and-daemon` until today); `main` on GitHub was fast-forwarded to `develop` at 02:17 (48f5af9) when the user asked. Work continues on `develop` (never commit on `main`). `pytest daemon` passes (280 tests) plus 1 expected failure (the UCF scoreboard layout, on purpose). The daemon (started 02:16:21) runs `develop` as of 58e2c40; the only later commit (48f5af9, ranked names such as "2 Georgia" in the digit fallback) goes live at the next restart. The test Edge is running with the extension reloaded earlier, tracking intact; the user is watching a set of college football games (LSU, USC at Penn State, Georgia-Alabama...).

**Two Claude sessions work on this repo (agreed with the user, they talk directly through `SendMessage`).**
* **Main session** (the one that wrote this): owns the main folder `C:\Users\Ahmed\Documents\OmniSportsSwitch` (branch `develop`), the live daemon and Edge (nobody else starts, stops or restarts them; the user decides when), merging, `daemon/server.py`, `arrange.py`, `settings.py`, `screen_ocr.py`, the popup and extension, the launcher, `scripts/`, README, CLAUDE.md files.
* **Scoring session** (session name `omnisportsswitch-e4`): works in its own git worktree `C:\Users\Ahmed\Documents\OmniSportsSwitch-scoring` on branches `scoring-bugs-N`, always branched from `origin/develop`. Owns `score_watch.py`, `scoring.py`, `game_state.py`, `leverage.py` and their tests. It reads the live logs from the main folder's `logs\`, finds false/missed events, fixes them test-first, pushes its branch and messages the main session "ready to merge"; the main session reviews the diff, runs the tests, merges into `develop` (fast-forward or a plain merge), pushes, restarts the daemon once per batch when the user agrees and tells the scoring session the restart time. Merged `scoring-bugs-*` branches are deleted on the remote. Design questions go to the user with options, pros, cons and a recommendation.

**Running it.**
* `scripts\setup.ps1` (one-time: venv, CUDA PyTorch, packages, models, tests; `-CheckOnly` reports what is missing). `scripts\start_all.ps1` starts the daemon (its own window, **minimized; closing it stops the daemon**) and the test Edge profile (separate profile at `%LOCALAPPDATA%\OmniSportsSwitch\edge-profile`, debug port 9222, extension preloaded), waits until the daemon is ready, skips whatever already runs; `-NoEdge` starts only the daemon. `scripts\stop_all.ps1` stops the daemon; `-Edge` also closes the test profile. `dist\OmniSportsSwitch.exe` (tray launcher, see Launcher below) does the same without a console window. If PowerShell blocks scripts: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\X.ps1`.
* **When a tab needs tracking again:** only when its page reloads (single view to multiview and back, a new game, a refresh); changing layout or adding/removing tiles inside a loaded multiview needs nothing, and the extension itself is never reloaded for this. Press **`Ctrl+Shift+Y`** (or the popup's "Re-track this tab") in that window: it tracks the visible tab of the window you are in.
* After changing **daemon or settings defaults**: restart the daemon. Settings changed in the popup apply live (kept in `settings.local.json`, untracked). After changing **extension** files: the user presses "Reload extension" in the popup (reloading drops tracking), then re-tracks every tab (Edge requires the click); the popup itself is read fresh each time it opens, so popup-only changes need no reload.
* Tests: `.venv\Scripts\python.exe -m pytest daemon -q` (about 7 s; one OCR-model test needs the models and the GPU).
* Logs: `logs/events.log` (goals, swaps and why, tile focus, game states, freeze, big plays, sport detections, warnings), `logs/daemon.log` (everything: per-stream score lines every 2 s with `lev=`, OCR text per video area every ~3 s, settings changes, Make Main), `logs/boxes.log` (size and position of every OCR item, for tuning). The debug port 9222 can be used (read-only) to inspect the test profile's pages (also for a JS syntax check: `Runtime.compileScript` over the port).
* Real samples of a scoreboard: `.venv\Scripts\python.exe scripts\capture_samples.py "label"` (saves to `samples/`, untracked). `scripts\analyze_audio_lead.py --since "2026-10-11 02:16:21"` measures how early the audio reacts before a scoreboard change.

**Features added on 2026-10-11 (all merged; see README for the user's view).** Popup Settings section (the daemon describes every setting in `settings.py LAYOUT`, the popup draws it; edits apply live); per-team allowed slots; **Make Main** (popup button per tab: the stream goes to Main and is held `manual_hold_s` = 60 s unless a challenger scores `hold_break_score` = 1.2; a wrong "over" reading must last `hold_over_s` = 60 s to end a hold; verified live at 02:09 and 02:13); window names shown as Main, Window 2..5 (ids stay MAIN, LEFT, RIGHT_BOT, RIGHT_MID, RIGHT_TOP: Window 2 = LEFT, 3 = RIGHT_BOT, 4 = RIGHT_MID, 5 = RIGHT_TOP); closeness and time left (`leverage.py`); digit fallback for big score digits OCR misses (below); safety net `Server._ensure_main` (adopts the tab in the MAIN window when the scorer has no MAIN) after a stray tab registered as MAIN and left at 01:58 and made the daemon swap-less for 6 minutes; setup script, tray launcher, README run guide, CLAUDE.md files. Languages: the user watches in French and English, so every banner/phrase list must be bilingual (OCR reads en+fr).

**Digit fallback (2026-10-11).** Scoreboards whose big score digits EasyOCR misses (Big Ten: white italic digits with a shadow on a coloured plate) are read by `ScreenReader.read_digits_beside_names` (`daemon/screen_ocr.py`): only when `find_score_geo` finds nothing, it looks right beside a team name, detects boxes in a small crop, reads them with a digits-only alphabet (>= 0.7 confidence, a PAIR of numbers side by side), and removes the detector's padding (20% per side) so the two boxes do not overlap. The result is added to the OCR items and flows through the normal score reader (votes, peak). Log line: `digit fallback tab X tile Y: ['16', '45']`. Retries every 6 s on tiles where it found nothing. Known risk: it can invent numbers beside any uppercase word; the geometry rules (size vs labels, pair, team near) and the vote guard against it.

**Where the live testing stands (02:20).** Verified live: goals on non-MAIN streams promote them; allowed-slot rotation; idle-MAIN handover on ads; favourites staying on MAIN; the freeze hotkey; window roles restored after a reload; sport detection; commercial placeholders; big-play banners; the DAZN tile expand (extension-triggered); per-team slot rules; Make Main (02:09, 02:13); MAIN recovery after the stray-tab bug. **Not yet seen live:** the daemon itself choosing to expand a DAZN tile (`focus tile` in `logs/events.log`, warning `only selected` in `logs/daemon.log`), the freeze sending the winner to Window 2, a negative ad score in a real ad, the closeness values (`lev=`) on a real tied/blowout pair after the latest restart, the digit fallback in the live pipeline (log line `digit fallback tab X tile Y`), the tray launcher's Start/Restart (never stopped the daemon to try), a fresh-machine `setup.ps1`, the popup Settings section (the user has not reported on it). Open weak spots: the UCF layout (expected-failure test), boards whose digits OCR misses and that are not Big Ten style, `SCORE_VS_LABEL` = 2.0 is borderline for the Big Ten board (2.07), OT scoreboard digits not read, the first readings after a restart can miss the second digit.

**Scoring hand-over (state 2026-10-11 02:30; written by the scoring session, files `daemon/scoring.py`, `score_watch.py`, `game_state.py`, `leverage.py`).**

*1. The rules as they are now*
* **State per tile** (`GameTracker`): live / break / over. Order: over caption (`FINAL`, `FINAL/OT`, `MATCH TERMINÉ`... whole item, 2 readings, kept 120 s) unless a live scorebug (down and distance `3rd & 4`/`2nd Down`, or the quarter `Ath`/`4th`/`Q4`) was seen in the last 15 s (a results ticker shows `Final` for other games) > ad placeholder phrase (also a clock with no scoreboard evidence beside it = ad countdown, 2 readings) > break caption (`HALFTIME`, `MI-TEMPS`) > readable clock frozen longer than the sport allows > overtime banner (no clock in OT, 30 s) > down/quarter seen in the last 20 s (the clock colon is often lost: `14848`) > no clock for `no_clock_s` = break. Bilingual phrase lists are in `settings.toml` `[banners]`.
* **Ad / not live** (`Scorer.components`): picture ad (`on_ad`) = -0.5; sound ad = music >= 0.5, no crowd, music above speech by 0.15, on 3 of the last 5 readings, remembered 12 s = -0.25. An ad is never promoted. `recent_ad_s`: an idle MAIN is not handed to a stream that was on an ad in the last 30 s, unless MAIN shows a picture ad/break/over for certain.
* **Swap rule**: challenger must beat MAIN by 40% and 0.15, score >= 0.35, keep qualifying 2 s (`confirm_s`), 20 s warm-up and 20 s dwell. MAIN idle (ad, break, over) = any live non-ad stream takes over (also during warm-up if the picture says ad/break/over). Make Main hold and Freeze: see the other paragraphs.
* **Score reader** (`ScoreWatcher.update`): skip stat graphics/tickers (`STAT_GRAPHIC_RE`) -> `find_score_geo` on OCR boxes (+ the digit fallback of `screen_ocr.py` when it finds nothing) -> else `find_score` anchored to team names only (loose readings only without boxes) -> vote 3 of the last 5 per side -> event when a side rises 1..10 above the *peak* -> peak only rises after the score held 25 s -> same score not announced twice in 60 s -> events ignored while the tile is break/over. A clock jump resets the baseline.
* **Leverage** (`leverage.py`): -1 (late blowout) .. +1 (late close game) from the voted score, the quarter vote (`GameTracker.period`, OT = 5) or the soccer minute; added to the stream's score as `leverage_weight` x value. Alone it never reaches `min_score`; unknown = 0.

*2. Constants and why*
* `SCORE_VS_LABEL` = 2.0: score digits must be 2x taller than the team names beside them. Records read as numbers reach 1.83x ("59", 66/36 px), real digits start at 2.07x (Big Ten, 112/54) and are usually 2.6-4.7x: the gap is narrow, change only with real numbers from `logs/boxes.log`.
* `PEAK_HOLD_S` 25 (a false high reading must not hide the next real goal), `PEAK_MEMORY_S` 120 (a lower value is believed only after this long), `FIRED_MEMORY_S` 60, `HISTORY`/`NEEDED` 5/3, `MAX_RISE` 10 (a 1-10 point jump is a score).
* `audio_ad_margin` 0.15 / votes 3 of 5 / `audio_ad_memory_s` 12: stadium music under commentary (music 0.7, speech 0.8) was labelled AD 76% of the time before the margin; real ads have music >> speech.
* `recent_ad_s` 30 (45 could keep MAIN on an ad), `confirm_s` 2, `leverage_weight` 0.3, `lev_*` close/blowout: American football 8/24 points, basketball 6/20, soccer 1/3 goals. `IN_PLAY_MEMORY_S` 15, `DOWN_MEMORY_S` 20, `OVERTIME_MEMORY_S` 30. All numbers are in `Config`, `settings.toml` and the settings LAYOUT (a test keeps them equal), except the `score_watch.py`/`game_state.py` module constants above.

*3. Bug log (symptom -> cause -> fix, tests in the named file)*
* 10-10 night: MAIN changed hands every 20 s -> one music reading flagged an ad -> votes 3/5 (`test_scoring`). Stat graphics/halftime tickers fired goals -> `STAT_GRAPHIC_RE` (`test_score_watch`). 00:39 swap on one applause reading -> `confirm_s`.
* UCLA-Oregon: Oregon's O logo read as `0`, 17-6 flickered -> outer-pair choice (centre panel text between the numbers) + scores only go up (peak) (`test_score_watch`). Team records `4-0`/`40`/`59` read as scores -> loose text readings only without boxes, ordinals (`Ath`) are not teams, `SCORE_VS_LABEL`, jersey numbers outside the team-name span, two `5-0` tokens = records.
* 01:17 Texas tied 4th quarter marked break (clock colon lost) -> down/quarter liveness (`test_game_state`); 01:19 Illinois-Michigan State overtime marked break, `FINAL/OT` unmatched -> overtime rule + regex; 02:10 USC at Penn State "over" from a ticker `Final`, LSU ad stuck on MAIN because everyone had an ad lately -> ticker rule + certain-ad relaxation; 02:18 ad countdown `2:27` flipped break/live -> lone-clock rule; 02:10-02:36 (offline replay, 2026-10-11 later) USC at Penn State: compact scorebug `19 USC 7 ... 7 PENN STATE` with digits only 1.6x the name height was unreadable under `SCORE_VS_LABEL` (66% of frames unread) -> digits on the names' own row right beside each name are accepted (`inline` in `find_score_geo`; records sit on a row below), and the ad countdown no longer counts as a clock jump in `ScoreWatcher` (it threw the score away at every commercial, so a touchdown before the break was never seen); 02:29 the same with the ad's own copy beside the countdown (`hulu`, `Vivez les plus grandes histoires`) -> an ad countdown is a clock with no scoreboard evidence beside it (no score numbers, `2-0` pair, down or quarter), still 2 readings.
* 01:58 daemon had no MAIN after a stray tab registered as MAIN and left -> `Scorer` remembers the previous MAIN (+ `Server._ensure_main`), all swaps had stopped. 01:56 false 5-0 on Georgia-Alabama (digit with dash marks `0 =`, ranked names `2 Georgia`) -> reader fixes.

*4. Open weak spots / ideas not done*
* Offline replay (logs 10-10/10-11): scorebug on screen but no score read in 11-26% of frames on normal boards, 49% on the USC bug (after the inline fix), 83-92% on the Houston/K-State FOX board (`28 9` = two scores merged into one OCR item, digits 1.8x the name height) and on multiview tiles. Splitting merged `a b` items and a wider look at that FOX layout are not done.
* OCR cannot read some boards (Big Ten outlined digits, overtime boards): digit fallback helps, but a real touchdown there may still be missed; audio alone rarely wins (crowd 0.1-0.2 under commentary). Digit misreads (6 read as 8, 17 as 12) can still fire one false change. If the first readings after a restart miss a digit the later correct score looks like one rise.
* No score read = leverage 0 on those streams. Clock recovery from `1605`-style numbers skipped (they do not behave like a clock). Not built: replay detection (REPLAY/SLO-MO/RALENTI dampens crowd audio), event size by points (TD vs extra point), a shouting signal (Shout/Yell classes), anticipation (red zone, penalty kick), penalty shootouts. Check `scripts/analyze_audio_lead.py` for how early audio leads the scoreboard.

*5. How to work on it*
* Test first (`.venv\Scripts\python.exe -m pytest daemon -q`; 1 expected failure, the UCF board). Real OCR boxes go into tests via `boxes([(text, height, x0, y0), ...])` (see `test_score_watch.py`).
* Replay the real logs through the real classes: parse `logs/boxes.log` lines `tab N[i] [(text, h, x0, y0), ...]` into items and feed `find_score_geo`/`ScoreWatcher.update(texts, clocks, ts, items)`; parse the `ocr tab` lines of `logs/daemon.log` (clocks, texts) into `GameTracker.update`. Compare against a screenshot of the window (mss) before trusting either. Swaps and states are in `logs/events.log`; per-stream scores with `lev=` in `daemon.log`.
* Work in your own worktree and branch from `origin/develop`; never restart the daemon yourself; hand the branch to the other session by message.

**Next, in order of value.** (1) Keep watching the live games and fix what the logs and the user's reports show (the scoring session reads the logs continuously; the user reports with a time). (2) More real scoreboard samples (NFL, Champions League, national-team matches, other leagues, French broadcasts) via `capture_samples.py`, then tune the reader on them. (3) The user's open questions: none right now. (4) Before a release: choose a licence, replace the broadcast frame test image, publish a fresh repo with ONE clean commit (no history, no email, no Claude trailers; see section 7). Ideas the user declined: a hotkey for Make Main (it would need an extension reload), a third-party game API. Ideas not asked for yet: anticipation boosts (red zone, penalty kick, VAR), disallowed-goal delay, penalty-shootout reading, a fully packaged app.

**Before any release (the user decided to wait; still in development).** Choose a licence (README says "not chosen yet"); remove `daemon/tests/fixtures/bigten_scorebug.png` (a frame of a broadcast, kept only for the digit-fallback test: replace it with a synthetic image or drop the test before publishing); the `-q/` screenshots were removed from the tree (still in the git history, so the public repo must start fresh with one commit); the setup script and the PANNs download are DONE (`scripts/setup.ps1`, checked with `-CheckOnly` on this machine only; a fresh-machine install is untested).

**Decided not to build (2026-10-11): a third-party game API.** Considered for score/game data; the catch is that the stream runs 20-60 s behind the API, so events must be held back (options were: release when the stream's own clock catches up, measure the delay once per stream from the first OCR-detected score change, or use the API only for ranking). The user chose to keep the audio + OCR design as it is, which also keeps everything local. Do not propose it again unless the user does.

**Working agreements with the user.** The user is new to web development, knows Python and C. For anything architectural or about design, ask first with options, pros and cons and a recommendation; implementation details are mine to decide, with a short explanation. Keep replies short and plain; tell the user when something is not tested. Everything runs locally (no cloud, accounts or APIs). Git: private repo; commit on `develop` (never directly on `main`; `main` is fast-forwarded from `develop` only when the user asks, a push of `develop:main` worked at 02:17 after the user asked, earlier it was blocked by the permission system and the user ran it), push `develop` freely, never force-push, never edit git config. Commit with `git -c user.name=saddi-a -c user.email=72130894+saddi-a@users.noreply.github.com`; the `Co-Authored-By: Claude Sonnet 5.5` trailer is fine because history is dropped when publishing. Do not restart the daemon or Edge, or reload the extension, without the user's go-ahead (a live test is usually running); say when something needs a restart. Keep this plan up to date as things change.

**Gotchas learned the hard way.**
* In Python strings a `\b` becomes a backspace character; a stray `0x08` once silently broke a regex. Use raw strings or lookarounds, and write multi-line patches as a script file, not inline in the shell.
* Source files have Windows line endings, so multi-line text edits can fail to match; patch from Python (read bytes, replace, write bytes) when that happens.
* Installing a package that depends on PyTorch can replace the GPU build with a CPU one. Install `torch` and `torchvision` from the CUDA index first (`requirements.txt` explains), and use `--no-deps` for the rest.
* Hardware acceleration must stay off in Edge, and no other window (including the daemon's console) may sit on top of a stream, because OCR works from screen capture.
* Old background monitors watch old log files; arm new ones on `logs/events.log`, which survives restarts. Notifications saying a monitor or background task "failed" or "expired" after a daemon restart are just the old process ending.
* In a DAZN multiview the slots keep their size and the *streams* move between them, so comparing tile sizes proves nothing. To check what happened, read where the sound went (an expand puts it on the big slot) or compare the team names OCR reads in each tile before and after.
* Edge ends audio capture whenever a page reloads (single view to multiview): the tab drops off the daemon's list (`unregistered tab` in the log) and needs `Ctrl+Shift+Y` again. Never reload the extension for that.
* Do not trust one reading: music probability, OCR text and clocks all flicker. Decisions use votes, memory and confirmations over several readings; new rules should too.
* The Bash tool turns a double backslash into a single one inside commands and heredocs, so a `\\b` in text meant for a file arrived as a backspace character (twice: a regex, then the README). For anything with backslashes write the file with the Write tool, or build the character with `chr(92)`. After editing text files check for stray control characters (`grep -nP "[\x00-\x08\x0b\x0c\x0e-\x1f]"`).
* `git` is not on the PATH of the PowerShell tool and `python` in bash is the Microsoft Store stub: use bash for git and `.venv\Scripts\python.exe` for Python. Scripts need `-ExecutionPolicy Bypass` in some shells.
* A page that is not a stream (a window in the MAIN slot, a popup) can register as a tab; never assume every registered tab is a game.
* Importing `panns_inference` triggers a `wget` download (fails on Windows): check it with `importlib.metadata`, import only its model definition.
* The tests in `daemon/tests/test_server.py` bind sockets and failed once (3 tests) while the daemon ran, then passed on rerun: if they fail, rerun before investigating.
* The git history still contains `-q/` (window screenshots) and the Phase 0 `spike/` code; a public release must start from a fresh repo.

**Launcher (2026-10-11).** `dist/OmniSportsSwitch.exe` (built by `scripts/build_launcher.ps1` from `launcher/launcher.py`, PyInstaller + pystray, ~26 MB) starts the daemon hidden and the test Edge profile and sits in the tray (start/restart/stop, open logs, quit). Tested: `--status`, single instance, closing it leaves the daemon running, command-line builders (daemon/tests/test_launcher.py). NOT tested live: starting/restarting the daemon from the tray menu (needs the daemon stopped); try it at the next restart. A fully packaged app (PyInstaller with torch/CUDA, several GB) was considered and left for a possible public release.

**File map.** `README.md` (users), `CLAUDE.md` + `daemon/CLAUDE.md` + `extension/CLAUDE.md` (guides for Claude); `daemon/` (server, scoring, game_state, score_watch, sport_detect, favourites, arrange, multiview, screen_ocr, audio_classifier, settings, logsetup, tests/ with real-sample fixtures); `extension/` (manifest, background.js service worker, offscreen.js, popup, pcm-processor.js, defaults.js); `settings.toml`; `scripts/`; (`spike/`, the Phase 0 throwaway tests, was removed on 2026-10-11, as was a folder of early window screenshots; both are still in the git history); `models/` (not tracked: PANNs checkpoint, EasyOCR models); `logs/`, `samples/` (not tracked).

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

**Names shown to the user (decided 2026-10-11):** Main, Window 2, Window 3, Window 4, Window 5, independent of the physical screens (Window 2 = internal id `LEFT`, Window 3 = `RIGHT_BOT`, Window 4 = `RIGHT_MID`, Window 5 = `RIGHT_TOP`). Only the popup and docs use the new names; the ids in code, logs, storage and tests are unchanged on purpose (saved roles and team rules use them).

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
   * The popup Settings section (built: `settings.local.json` overrides `settings.toml`, edits apply live) needs a live try. Per-team allowed slots are built (commit after `85bc7e9`): need the extension reloaded and the daemon restarted to try.
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
