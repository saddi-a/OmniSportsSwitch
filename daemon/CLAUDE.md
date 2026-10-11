# daemon/ (guide for Claude)

The Python daemon: one asyncio process (`python -m daemon.server`, started by `scripts\start_all.ps1`) that listens on `ws://127.0.0.1:8765`, receives audio and reports from the extension, scores each stream, and sends back "arrange" (move tabs) and "focus_tile" (DAZN multiview) commands. Read `PLAN.md` sections 3, 5 and 6 for the rules these files implement.

## Files

| File | Job |
|---|---|
| `server.py` | The `Server`: message handling, the loops (classify audio, OCR, decide swaps), per-tab/per-tile state, slot limits (tab choice + team rules), expand confirmation. Where everything is wired together. |
| `scoring.py` | `Scorer`: score of each stream from audio signals, events, favourites and status; `decide()` picks swaps (margin, minimum score, warm-up, dwell); `evict_main()` hands MAIN over when its stream may not be there. Pure logic, takes `now`. |
| `game_state.py` | `GameTracker`: live / break / over, commercial placeholders, big-play banners, per sport profile. Pure logic. |
| `score_watch.py` | `ScoreWatcher`: finds the score on a scoreboard from OCR items (team-anchored, geometry), votes over readings, detects a goal. |
| `sport_detect.py` | Guesses the sport from scoreboard cues, with accumulated evidence. |
| `favourites.py` | Matches favourite teams (`match_multiplier`) and team slot rules (`match_slots`) against text. |
| `arrange.py` | `plan_swap` (who moves where, honouring allowed slots, three-way rotations) and `plan_fix` (correct tabs in slots they may not use). Pure logic. |
| `multiview.py` | `TileFocuser`: which tile of a multiview tab should have the sound / be expanded. |
| `screen_ocr.py` | Screen capture of a video area (mss) and EasyOCR (GPU); also clock finding. |
| `feed.py` | Outside game data (ESPN's public JSON), MEASURING ONLY (`scoring.feed_mode` 0 = off = nothing starts, 1 = record; needs a restart): parsing into `Snapshot`/`FeedEvent`, `FeedTracker` (what changed), `Poller` (when to ask, back-off), `FeedRecorder` (`logs/feed/<date>.jsonl`), `FeedSession.step` (run in a thread by `Server._feed_loop`). Changes no score or swap. `feed_compare.py` + `scripts/feed_compare.py` measure the lead of the feed over each stream. `feed_map.py` (Stage 1, measuring only): `FeedMapper` ties each (tab, tile) to a feed game by voting over OCR team names + popup teams text, locked only while the OCR score/clock fit the feed's timeline; logs `feed map ...`. `feed_gate.py` (Stage 2, measuring only; also the football end-zone cue lines from `feed.parse_situations`): `FeedGate` compares feed events with the stream clock of mapped tiles, logs `feed would-fire/confirmed/unconfirmed/skipped/ocr-only` (lead `scoring.feed_lead_s`). Plan: `docs/api-plan.md`. |
| `audio_classifier.py` | PANNs Cnn14 on the GPU, 32 kHz audio, 527 AudioSet classes. Models live in `models/panns/`. |
| `settings.py` | Loads `settings.toml` (the defaults) plus `settings.local.json` (what the user changed in the popup) into the config objects, and lets the popup read, change and reset them live (`describe`, `apply_changes`, `reset_all`). |
| `logsetup.py` | Permanent logs in `logs/`. |
| `tests/` | pytest suite; `tests/fixtures/*.json` are real OCR readings (items with boxes) from screenshots. |

## Rules for changes

- Keep decision logic **pure** (callers pass `now`, no sockets, no sleeps) so it can be tested; only `server.py` touches time, sockets and threads.
- Every tunable number goes in `Config`/`Weights` (or a sport profile) **and** `settings.toml`; `tests/test_settings.py` fails if they drift apart. To make it editable in the popup, also add it to `LAYOUT` in `settings.py` (id, label, help text); the popup draws itself from that, so `extension/` needs no change. Settings objects are changed in place while running, so read `config.x` when you use it; never copy a value at startup.
- Per-tile state is keyed `(tab id, video index)`; per-tab state by tab id. When a DAZN expand swaps streams between slots, the per-tile records must swap too (`_expand_swap`), only once the expand is confirmed by where the sound went.
- OCR is noisy. A single reading must never trigger an event: use votes, memory windows and confirmations. When fixing a misread, add the real OCR items as a fixture and a test.
- Slot limits: `allowed` is what the user ticked on the tab; `limits` = allowed plus team rules (by teams field, or OCR text remembered 10 minutes). Use `limits` for planning.
- Run `.venv\Scripts\python.exe -m pytest daemon -q` before every commit. The one expected failure is the UCF layout (`test_ucf_layout_with_a_logo_read_as_a_digit`).
