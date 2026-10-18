# OmniSportsSwitch (guide for Claude)

A local "live broadcast director": an Edge extension plus a Python daemon that watch several browser sports streams and move the most exciting one into the Main window (internal id `MAIN`).

**Read `PLAN.md` section 0 first.** It is the durable hand-over (current state, how to run and test, what is verified live, next steps, gotchas); keep it up to date after every change. It stays short on purpose: older notes, the scoring rules reference and the old architecture/layout/risk sections are in `docs/history/plan-section0-archive.md` (not loaded by default). `README.md` is for users; do not duplicate it here.

## Key facts (kept here because every session needs them)

- Machine: Windows 11, 3 monitors (Screen 3 24" 1080p left, Screen 1 27" 1440p middle = Main, Screen 2 27" portrait right = Window 3 and 4, Window 5 top third rarely used), RTX 3080 10 GB, Python 3.12 in `.venv`, Edge (Manifest V3, plain JS, no build step), no Node. Slots fill Window 2, 3, 4, 5; Main is only reached by promotion. Roles belong to windows, not tabs; a swap moves tabs between fixed windows (`chrome.tabs.move`), one stream tab per window.
- Why local and screen-based: OTT streams lag 20-60 s, so no external API may trigger a swap (the ESPN feed is measuring only). DRM blocks page frame capture, so the daemon screenshots each video area (EasyOCR + RapidOCR rescue) and the extension captures tab audio (`tabCapture`, PANNs on the GPU); only Main is audible (Web Audio gain nodes).
- Swap rule in one line: a challenger must beat Main by 40% and 0.15, score >= 0.35 (loudness alone never enough), qualify for 2 s, after a 20 s warm-up and 20 s dwell; ads and breaks score negative; the build-up tracker (football) can move Main earlier. All numbers: `settings.toml`; explanation for users: README "How a swap is decided".
- Verified live so far and not: see the state and open items in `PLAN.md` section 0.

## Non-negotiables

- Everything runs locally: no cloud, accounts or paid APIs.
- The repo is PUBLIC: never commit logs, samples, screenshots, stream URLs or personal data.
- Work on `develop` only; never commit on `main`, never force-push, never edit git config.
- Add a test for every behaviour you add; run the suite before every commit.
- Do not trust single readings (music probability, OCR text, clocks flicker): use votes, memory and confirmations.
- Never cover a stream with a window (OCR reads the screen); keep Edge hardware acceleration off.
- The aim is to move Main BEFORE the goal (build-up: big chance, save, VAR, penalty awarded), not after it; each sport has its own clues and is calibrated and reported on its own.
- Ask before architecture or design decisions: options with pros and cons and a recommendation. Implementation details are yours.
- The main chat is for planning, decisions and logging the user's feedback; background agents do the work, but spend them deliberately (usage is limited): routine agents run on the cheaper model, follow-ups resume an existing agent, work is batched per area, and tiny edits are done inline (rules in `docs/claude/workflow.md`, "Agent economy").
- Talk to the user with the window names Main, Window 2 to 5, not the internal ids.

## Index: read the file when

Plain links on purpose (not `@` imports), so a file loads only when needed.

| File | Read it when |
|---|---|
| [docs/claude/workflow.md](docs/claude/workflow.md) | replying to the user, asking design questions, delegating to agents, restarting, working in several sessions |
| [docs/claude/git.md](docs/claude/git.md) | committing, pushing, merging, touching `main` |
| [docs/claude/testing.md](docs/claude/testing.md) | running tests, the backtest, adding fixtures or settings |
| [docs/claude/layout.md](docs/claude/layout.md) | looking for where something lives in the repo |
| [docs/claude/names-and-windows.md](docs/claude/names-and-windows.md) | naming windows (Main, Window 2..5 vs `LEFT`, `RIGHT_BOT`...) |
| [docs/claude/windows-quirks.md](docs/claude/windows-quirks.md) | editing files or installing packages on this Windows machine |
| [daemon/CLAUDE.md](daemon/CLAUDE.md) | changing daemon code: file table and rules for changes |
| [extension/CLAUDE.md](extension/CLAUDE.md) | changing the Edge extension |
| [docs/director-training.md](docs/director-training.md), [docs/dataset-format.md](docs/dataset-format.md), [docs/per-tile-sport.md](docs/per-tile-sport.md) | the learned-weights plan, its dataset format and tools, per-tile sport detection |
| [director_training/README.md](director_training/README.md) | Director Training: fitting, shadow evaluation and shipping per-sport weights offline (`scripts/director_training.py`, runs in `logs/director_training/`) |
| [docs/scoring-rules.md](docs/scoring-rules.md) | changing how scores, game state (live/break/over), ads, swaps or cues are decided |
| [docs/api-plan.md](docs/api-plan.md) | the ESPN feed (measuring only) |
| [docs/history/plan-section0-archive.md](docs/history/plan-section0-archive.md) | a fact about the 2026-10-11 to 2026-10-18 work (finished scoring-bugs branches, per-tile sport stages, old state paragraphs) or the old 2026-10-13 open decisions, or the old architecture/layout/risk sections (removed from PLAN.md) is needed |
