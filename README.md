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

Other features: freeze `MAIN` with a hotkey, DAZN multiview support (it clicks the right tile), window roles remembered across restarts, sport detected from the picture, permanent logs.

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

```powershell
scripts\start_all.ps1
```

This starts the daemon in its own minimized window (**closing that window stops the daemon**) and a separate Edge test profile with the extension preloaded. `scripts\stop_all.ps1` stops the daemon.

Then:

1. Open one stream per Edge window and put the windows where you want them.
2. In each window, click the extension icon and **Track this tab** (or press `Ctrl+Shift+Y`). Edge requires this click to start audio capture.
3. In each window's popup, give the window a role (`MAIN`, `LEFT`, `RIGHT_BOT`, `RIGHT_MID`, `RIGHT_TOP`). Roles are remembered by window position.

After a page reload (for example single view to multiview) the tab has to be tracked again with `Ctrl+Shift+Y`.

If you edit `extension/` files, press **Reload extension** in the popup and track the tabs again. If you edit the daemon code, stop and start the daemon.

### Shortcuts

| Keys | Action |
|---|---|
| `Ctrl+Shift+P` | Freeze or unfreeze `MAIN`. While frozen, the winning stream goes to `LEFT` instead. |
| `Ctrl+Shift+Y` | Track (or re-track) the visible tab of the current window. |

### The popup

- Role of the current window and the freeze button.
- Per tab: sport, teams, weight, status (Auto, Live, Break, Over), allowed slots.
- Settings: all the scoring, ad, weight, timing and banner settings (needs the daemon running).
- Priorities: sport weights, and a team list with a bonus multiplier (favourites) and allowed slots per team. For a team rule to apply at once, type the team in the tab's Teams field. The daemon also learns teams from the scoreboard text.

### Settings

Every threshold, weight, break timing and banner phrase is editable in the popup (**Settings** section). Each setting has a short explanation, edits apply at once without restarting the daemon, changed values are shown in bold, and the arrow button puts one back to its default. The values are kept in `settings.local.json` (not committed), layered on top of the defaults in `settings.toml`.

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
