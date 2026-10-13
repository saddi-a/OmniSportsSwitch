# Testing

- Run: `.venv\Scripts\python.exe -m pytest daemon director_training -q` (`director_training/tests/` = Director Training, synthetic data only). About 1000 tests. One expected failure: the UCF layout (`test_ucf_layout_with_a_logo_read_as_a_digit`).
- Add a test for every behaviour you add. Run the suite before every commit.
- Real scoreboard fixtures (OCR items with boxes from screenshots) are in `daemon/tests/fixtures/`. When fixing a misread, add the real OCR items as a fixture and a test.
- After a football change also run `.venv\Scripts\python.exe scripts\backtest.py logs --summary` (read-only: how far ahead of each goal a stream scored, per sport, plus the feed as ground truth). Measure with it before changing weights.
- Calibrate and report football, American football and basketball separately.
- Settings: every tunable goes in `Config`/`Weights` and `settings.toml`; `daemon/tests/test_settings.py` fails if they drift apart. Popup entries go in `LAYOUT` in `daemon/settings.py`. Per-sport overrides (`[scoring.<sport>]`, `[weights.<sport>]`) and `OVERRIDABLE_SCORING`: details in `daemon/CLAUDE.md` "Rules for changes".
- Decision logic stays pure (callers pass `now`) so it can be tested; tests build `Server(recorder=None)` so they write nothing.
- Offline tools worth knowing: `scripts/replay_logs.py`, `scripts/review_swaps.py`, `scripts/build_dataset.py`, `scripts/feed_compare.py` (see layout.md).
