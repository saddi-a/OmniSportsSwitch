# OmniSportsSwitch (guide for Claude)

A local "live broadcast director": an Edge extension plus a Python daemon that watch several browser sports streams and move the most exciting one into the `MAIN` window. Everything must run locally (no cloud, accounts or APIs).

**Read `PLAN.md` section 0 first.** It has the current state, how to run and test, what is verified live, next steps and gotchas. It is the durable hand-over; keep it up to date after every change. Sections 1 to 8 hold the architecture, display layout, scoring and risks. `README.md` is for users; do not duplicate it here.

## Layout

- `daemon/` Python daemon (see `daemon/CLAUDE.md`).
- `extension/` Edge Manifest V3 extension, plain JS, no build step (see `extension/CLAUDE.md`).
- `settings.toml` every threshold/weight/phrase; a test keeps it equal to the built-in defaults.
- `launcher/` the tray launcher (`launcher.py`, built into `dist/OmniSportsSwitch.exe` by `scripts/build_launcher.ps1`; build tools in `requirements-build.txt`); it starts this project's `.venv` daemon and the test Edge profile.
- `scripts/` PowerShell `setup.ps1` (one-time install: venv, CUDA PyTorch, packages, models, tests), start/stop scripts (`start_all.ps1 -NoEdge`, `stop_all.ps1 -Edge`), `capture_samples.py` (saves screenshots to untracked `samples/`).
- `tmp/` scratch space, ignored by git (nothing in it is kept). The Phase 0 experiments (`spike/`) were removed; their conclusions are in `PLAN.md` and the code is in the git history before the commit that removed it.
- `models/`, `logs/`, `samples/`, `dist/`, `build/`, `tmp/`, `.venv/` are not tracked.

## How to work here

- **Ask before architecture or design decisions**: give options with pros and cons and a recommendation. Implementation details are yours; explain them briefly. The user is new to web development, knows Python and C. Keep replies short and plain.
- **Git**: private repo. Work on `develop` (renamed from `phase-0-1-spike-and-daemon` on 2026-10-11); other sessions work on their own branches from `develop` and the main session merges them. Never commit directly on `main`: it is only fast-forwarded from `develop` (`git push origin develop:main`) when the user asks; the permission system blocked this once and allowed it after the user asked explicitly. Push `develop` freely. Never force-push. Never edit git config; commit with `git -c user.name=saddi-a -c user.email=72130894+saddi-a@users.noreply.github.com`. Publishing later will be a NEW repo with one clean commit (no history, no email, no Claude trailers).
- **Tests**: `.venv\Scripts\python.exe -m pytest daemon -q` (one expected failure: the UCF layout). Add a test for every behaviour you add. Real scoreboard fixtures are in `daemon/tests/fixtures/`.
- **After changes**: daemon or settings changes need a daemon restart (`scripts\stop_all.ps1`, `scripts\start_all.ps1`). Extension changes need the user to press "Reload extension" in the popup and re-track each tab.
- **Do not trust single readings**: music probability, OCR text and clocks flicker. Use votes, memory and confirmations over several readings.
- **Never cover a stream** with a window (OCR reads the screen) and keep Edge hardware acceleration off.

## Names

Windows are shown to the user as Main, Window 2, 3, 4, 5. The internal ids stay `MAIN`, `LEFT`, `RIGHT_BOT`, `RIGHT_MID`, `RIGHT_TOP` (in this order of filling) because they are saved in the browser and used by the daemon, arrange and the tests: Window 2 = `LEFT`, Window 3 = `RIGHT_BOT`, Window 4 = `RIGHT_MID`, Window 5 = `RIGHT_TOP`. The labels live in `ROLE_NAMES` in `extension/popup.js`. Use the user-facing names when talking to the user.

## Windows quirks

- Source files use CRLF line endings. Multi-line Edit calls can fail to match; patch from a Python script (read bytes, replace, write bytes) instead. Write patch scripts to files rather than inline shell.
- In Python strings `\b` is a backspace. Use raw strings or lookarounds in regexes.
- Install `torch` and `torchvision` from the CUDA index first, and use `--no-deps` for packages that depend on them, or pip replaces them with CPU builds.
- The bash `python` is a Microsoft Store stub; use `.venv\Scripts\python.exe`. In PowerShell, `git` may not be on PATH; use bash for git.
