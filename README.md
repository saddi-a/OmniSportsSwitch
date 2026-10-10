# OmniSportsSwitch

An automated "live broadcast director" for people who watch several sports streams at once.

You open your streams in separate Edge windows (one per screen slot). OmniSportsSwitch listens to every stream, reads the scoreboards on screen, and automatically moves the most exciting game into the big centre window (`MAIN`). Everything runs locally on your PC: no accounts, no cloud, no API keys.

> Status: personal project, working but still being tested live. It was built and tested on one specific setup (Windows 11, three monitors, NVIDIA GPU, DAZN and similar streaming sites). Expect rough edges.

## How it works

Two parts talk over a local WebSocket (`ws://127.0.0.1:8765`):

| Part | What it does |
|---|---|
| **Edge extension** (`extension/`, Manifest V3, plain JavaScript, no build step) | Captures each tab's audio, reports where the video sits on screen, moves tabs between windows, mutes all but `MAIN`, and has a popup for your settings. |
| **Python daemon** (`daemon/`) | Scores every stream and decides who goes where. Audio is classified on the GPU with PANNs (crowd, speech, music...). Scoreboards are read with EasyOCR on screenshots of the video area. |

What the daemon looks at:

- **Crowd noise** and sudden loudness (a roar means something happened).
- **Score changes** read from the scoreboard (a goal, a touchdown), and **big-play banners** ("TOUCHDOWN", "GOAL"...).
- **Game state**: live, break, or over (from the clock, the scoreboard and the picture).
- **Commercials**: a stream on an ad gets a negative score and is never promoted. A broadcast on an ad in `MAIN` is replaced.
- **Your preferences**: favourite teams (kept on `MAIN` unless something exciting happens elsewhere), per-sport weights, per-tab weights, and slots a team or tab may or may not use.

Other features: freeze Main with a hotkey, **Make Main** to force a stream there for a while, DAZN multiview support (it clicks the right tile), window names remembered across restarts, sport detected from the picture, close and late games valued above blowouts, every setting editable in the popup, permanent logs.

The daemon understands English and French scoreboards and banners.

## Requirements

- Windows 11 and Microsoft Edge.
- An NVIDIA GPU with a recent driver (audio classification and OCR use CUDA).
- Python 3.12.
- Edge **hardware acceleration must be OFF** (`edge://settings/system`). Streaming sites protect their video, so the daemon reads the screen instead. With acceleration on, the screen capture comes out black.
- No other window may cover a stream, because the daemon reads what is on screen.

## Setup

Run these from the repository folder in PowerShell.

1. Create the environment and install PyTorch first, from the CUDA index (otherwise pip may install a CPU-only build):

   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128
   ```

2. Install the rest. `panns-inference` is installed without its dependencies, because only its model definition is used:

   ```powershell
   .venv\Scripts\python -m pip install websockets numpy mss pillow easyocr torchlibrosa matplotlib pytest pytest-asyncio
   .venv\Scripts\python -m pip install --no-deps panns-inference
   ```

   See `requirements.txt` for the exact versions.

3. Download the audio model into `models\panns\`: the PANNs `Cnn14_mAP=0.431.pth` checkpoint and `class_labels_indices.csv` (AudioSet labels). The OCR models download themselves into `models\easyocr\` on first run.

A single setup script that does all of this is on the to-do list.

## Running it

### The one-click launcher

`dist\OmniSportsSwitch.exe` does what `scripts\start_all.ps1` does, with no console window to close by accident. Double-click it (or put a shortcut to it on your desktop): it starts the daemon (hidden) and the Edge test profile, and stays in the system tray. Right-click the tray icon to see whether the daemon is running, and to **Start / Restart / Stop the daemon**, open Edge, open the logs, or **Quit** (leaving the daemon running, or stopping it). It only starts what is not already running, and a second copy exits by itself.

The exe is small (about 26 MB) because it does not contain the daemon: it starts this project's own `.venv`, so keep it inside the project folder (`dist\` is fine). Build it once, and again after changing `launcher\launcher.py`:

```powershell
scripts\build_launcher.ps1
```

`OmniSportsSwitch.exe --status` prints the daemon and Edge state and exits (useful for checks).

### First time

1. Open PowerShell in the repository folder and run:

   ```powershell
   scripts\start_all.ps1
   ```

   If Windows says "running scripts is disabled", run it like this instead (same for the other scripts):

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\start_all.ps1
   ```

   This does two things, and skips whichever is already running:
   - starts the **daemon** in its own minimized PowerShell window. The first start takes about 15 seconds while the models load. **Closing that window stops the daemon.**
   - opens a separate **Edge test profile** (your normal Edge profile is not touched) with the extension already loaded.

2. In that Edge, open **one stream per window** and put the windows where you want them (for example one per screen). Keep Edge's hardware acceleration off (see Requirements) and do not let any window cover a stream.
3. In each window click the extension icon, then **Track this tab** (or press `Ctrl+Shift+Y`). Edge requires this click to start audio capture. The icon badge shows `ON`.
4. In each window's popup, give the window a name: **Main**, **Window 2**, **Window 3**, **Window 4** or **Window 5**. Main is the big window that streams are promoted into; new streams fill Window 2, then 3, 4, 5. The names do not depend on which screen a window is on. They are remembered by window position, so after a restart they come back by themselves if the windows sit where they did.
5. That's it. The daemon now watches every tracked stream and moves the most exciting one into Main. Only Main is audible.

### Every day

1. `scripts\start_all.ps1`, wait about 15 seconds.
2. Open your streams, press `Ctrl+Shift+Y` in each window.
3. Watch. When you're done: `scripts\stop_all.ps1` (stops the daemon; close the Edge windows yourself).

### While you watch

| You want to | Do this |
|---|---|
| Keep Main where it is | `Ctrl+Shift+P` (or the popup's Freeze button). While frozen, the winning stream goes to Window 2 instead. Press again to resume. |
| Put a stream on Main yourself | Popup, on that stream's block: **Make Main**. It stays on Main for the hold time (10 minutes by default, set in Settings), unless something really exciting happens elsewhere. A yellow bar at the top of the popup shows the time left and a **Release** button. |
| Stop a stream from taking Main | Untick **Main** in its "Allowed in" boxes (per tab) or in the team's boxes (per team). |
| Prefer a team | Popup, Priorities, Teams: add the team with a multiplier (2 = twice as much). Favourites stay on Main unless something exciting happens elsewhere. |
| Tell the daemon a game is over or on a break | Per tab: Status = Over or Break (Auto reads it from the picture). |
| Change how it behaves | Popup, **Settings** section: every threshold, weight, timing and banner phrase, with an explanation each. Changes apply at once. |

After a page reloads (for example switching a DAZN tab between single view and multiview) the tab drops off the daemon's list. Press `Ctrl+Shift+Y` in that window. Changing the layout inside a loaded multiview needs nothing.

### Restarting things

| What changed | What to do |
|---|---|
| You edited daemon code or pulled new code | `scripts\stop_all.ps1`, then `scripts\start_all.ps1`. The streams reconnect by themselves. For about 20 seconds after the start nothing swaps (the streams are warming up). |
| You changed a setting in the popup | Nothing, it applies at once. |
| You edited `extension\` files | In the popup, press **Reload extension**, then track every tab again (Edge requires the click). |
| The popup says the daemon is not running | Start it: `scripts\start_all.ps1`. |
| The daemon window was closed | Same: `scripts\start_all.ps1` (it only starts what is not running). |

### Using your own Edge profile instead of the test profile

The start script uses a separate test profile because it is easy to reset. To use your normal Edge: run only the daemon (`scripts\start_daemon.ps1`, which runs in the current window, stop it with `Ctrl+C`), then in Edge open `edge://extensions`, turn on Developer mode, **Load unpacked**, and choose the `extension` folder. Everything else is the same.

### If something does not work

| Problem | Likely cause and fix |
|---|---|
| A stream is never promoted, and its OCR text in `logs\daemon.log` is empty | Edge hardware acceleration is on (screen capture comes out black), or a window covers the stream. |
| "Track this tab" does nothing | The tab is not a normal web page (for example `edge://`). Use it on the stream page itself. |
| A tab stopped being tracked | Its page reloaded. Press `Ctrl+Shift+Y` in that window. |
| Everything is silent | Only Main is audible. Check the window names, and that one window is Main. |
| Nothing swaps right after a start | Normal: a 20-second warm-up. |
| A stream will not go to Main | Check its "Allowed in" boxes and any team rule that includes it. |
| Main stays on a stream you do not like | It may be held (**Make Main**, yellow bar in the popup) or frozen (the badge says `LOCK`). |
| A wrong swap or a missed goal | Look in `logs\events.log` for the swap and the reason, and in `logs\daemon.log` for the scores and the OCR text at that time. |

### Shortcuts

| Keys | Action |
|---|---|
| `Ctrl+Shift+P` | Freeze or unfreeze Main. |
| `Ctrl+Shift+Y` | Track (or re-track) the visible tab of the current window. |

If a shortcut does nothing, check `edge://extensions/shortcuts`.

### The popup

- Name of the current window (Main, Window 2...), the freeze button, and the hold bar when a stream is held on Main.
- Per tracked tab: sport, teams, weight, status (Auto, Live, Break, Over), which windows it may use, **Make Main**, Untrack.
- Settings: all the scoring, ad, weight, timing and banner settings (needs the daemon running). Changed values are shown in bold, the arrow button puts one back to its default, and "Reset all" restores everything. The values are kept in `settings.local.json` (not committed), layered on top of the defaults in `settings.toml`.
- Priorities: sport weights, and a team list with a bonus multiplier (favourites) and allowed windows per team. For a team rule to apply at once, type the team in the tab's Teams field. The daemon also learns teams from the scoreboard text.

## Logs

In `logs/` (not committed): `events.log` (goals, swaps and why, game states, warnings), `daemon.log` (everything, including the OCR text of each video area), `boxes.log` (sizes and positions of OCR items, for tuning).

## Tests

```powershell
.venv\Scripts\python -m pytest daemon\tests -q
```

One test is an expected failure on purpose (a scoreboard layout the reader still gets wrong).

## Limits worth knowing

- Tested mostly with DAZN, a few other sites, and American football, college football, basketball and football (soccer) scoreboards. Other layouts may need tuning of the scoreboard reader.
- The screen layout (three monitors, five slots) is currently built in.
- Audio capture ends whenever a page reloads; the tab then needs tracking again.

## Project notes

The design, the scoring rules and the hand-over notes for development are in [PLAN.md](PLAN.md). Guidance for AI coding assistants is in [CLAUDE.md](CLAUDE.md).

## Licence

Not chosen yet.
