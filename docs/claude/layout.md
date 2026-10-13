# Repo layout

## Tracked
- `PLAN.md` hand-over (section 0) and architecture; `README.md` for users; `settings.toml` every threshold, weight and phrase (a test keeps it equal to the built-in defaults; per-sport tables `[scoring.<sport>]`, `[weights.<sport>]`).
- `daemon/` Python daemon, file table in `daemon/CLAUDE.md`. Newer pieces: `recorder.py` (learning data), `dataset_builder.py`, `swap_review.py`, `buildup.py`, `team_names.py` with `team_tables/` (one text file per sport), `feed*.py` (ESPN feed, measuring only), `backtest.py`. Tests in `daemon/tests/` (fixtures in `daemon/tests/fixtures/`).
- `extension/` Edge Manifest V3 extension, plain JS, no build step (`extension/CLAUDE.md`).
- `launcher/` tray launcher (`launcher.py`), built into `dist/OmniSportsSwitch.exe` by `scripts/build_launcher.ps1` (build tools in `requirements-build.txt`); it starts this project's `.venv` daemon and the test Edge profile.
- `scripts/` PowerShell: `setup.ps1` (one-time install: venv, CUDA PyTorch, packages, models, tests), `start_all.ps1 -NoEdge`, `stop_all.ps1 -Edge`, `start_daemon.ps1`, `start_test_edge.ps1`, `build_launcher.ps1`. Python: `backtest.py`, `replay_logs.py`, `review_swaps.py` (swap review), `build_dataset.py`, `feed_compare.py`, `analyze_audio_lead.py`, `capture_samples.py` (screenshots to `samples/`). The offline tools are read-only on `logs/`.
- `director_training/` OFFLINE package (never imported by the daemon; test-enforced) for Director Training: `runs.py` run folders + experiment log, `features.py`, `fit.py`, `evaluate.py` (shadow replay), `ship.py`, `cli.py`; command `scripts/director_training.py` (`build fit evaluate ship status`); runs in `logs/director_training/`. Details: `director_training/README.md`. Tests in `director_training/tests/`.
- `docs/` `learning-plan.md`, `dataset-format.md`, `per-tile-sport.md`, `api-plan.md`; `docs/claude/` the detail files for this guide.

## Not tracked
- `logs/` (daemon.log, events.log, boxes.log, plus `archive/` gzip of rotated logs, `dataset/` recorder rows, `dataset_built/`, `feed/`, `feedback/` the user's remarks, `review/` swap marks), `models/`, `samples/`, `dist/`, `build/`, `.venv/`, `settings.local.json` (popup changes).
- `tmp/` scratch space, nothing in it is kept. The Phase 0 experiments (`spike/`) were removed; their conclusions are in `PLAN.md` and the code is in the git history before the removing commit.
