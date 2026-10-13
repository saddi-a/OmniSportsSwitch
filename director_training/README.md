# Director Training (offline)

Learns, per sport, the numbers of the existing score (weights and the `min_score` threshold) from the recorded games, checks them on games the fit
did not see, and turns a passing result into a settings patch. Plan and decisions: `docs/learning-plan.md`. Football and American football first.

Rules: it only READS `logs/` and writes only `logs/director_training/` (gitignored); the live daemon never imports it (a test checks); it may import
`daemon` to use the same scoring formula, settings loader and metric code. No new dependencies (numpy, scikit-learn are in the venv).

## Commands

```
.venv\Scripts\python.exe scripts\director_training.py build [YYYY-MM-DD ...]          # labelled dataset into a run folder (same builder as build_dataset.py)
.venv\Scripts\python.exe scripts\director_training.py fit [--sport football] [--dry-run] [--min-games 8] [--min-events 25]
.venv\Scripts\python.exe scripts\director_training.py evaluate --fit-run N [--holdout GAMEID,...]
.venv\Scripts\python.exe scripts\director_training.py ship --fit-run N --shadow-run M                      # DRY: prints the diff, changes nothing
.venv\Scripts\python.exe scripts\director_training.py ship --fit-run N --shadow-run M --apply --target FILE
.venv\Scripts\python.exe scripts\director_training.py ship --rollback RUN
.venv\Scripts\python.exe scripts\director_training.py status [--sport football]
```

Global options (before the command): `--logs-dir` (default `<repo>/logs`), `--settings` (the settings file that defines "today"; default `<repo>/settings.toml`).

## Run folders

Each command except `status` makes `logs/director_training/run-0001-<command>/` and appends one line to `logs/director_training/runs.jsonl`
(the experiment log):

| File | Content |
|---|---|
| `config.json` | command, sport, all parameters, git commit (+ dirty flag), the input files with size and sha256, today's values (fit) |
| `metrics.json` | the numbers (`headline` = the line shown by `status`), `status` (`ok`, `insufficient-data`, `refused`, `error`), `dry_run` |
| `report.md` | the same in plain language |
| artifact | `build`: `rows.jsonl swaps.jsonl events.jsonl games.jsonl`; `fit`: `weights.json`; `evaluate`: `shadow.json`; `ship`: `patch.toml patch.diff backup/` |

Everything is deterministic (fixed seed `--seed 0`). A crash still closes the run with status `error`.

## Modules

| Module | Job |
|---|---|
| `runs.py` | run folders, `runs.jsonl`, `status` table |
| `data.py` | reads the logs like `scripts/build_dataset.py` and lists the files for `config.json` |
| `features.py` | the score components and the exact map feature -> setting name (module docstring); `score_from_row` = the daemon's `Scorer.components` formula for a recorded row under any settings (test: identical to the daemon's score) |
| `fit.py` | per-sport L2 logistic regression, class-weighted, leave-one-GAME-out; the data guard; the scaling rule and operating threshold (module docstring) |
| `evaluate.py` | shadow evaluation: replay the recorded rows with candidate weights, simplified swap rule, judged by `daemon/dataset_builder.py` (regret, miss, lead); the list of what the replay ignores |
| `ship.py` | gate, `patch.toml`, unified diff, apply with backup, rollback |
| `cli.py` | argparse; `scripts/director_training.py` is a thin wrapper |

## The data guard and DRY RUN

`fit` refuses a sport with fewer than `--min-games` games (default 8) or `--min-events` independent positive events (default 25; positive rows of one
event count once). It then writes a run with status `insufficient-data`, the counts and the reason, and exits 0. `--dry-run` ignores the guard only to
exercise the pipeline (it still needs 2 games and both classes) and stamps the artifact `DRY RUN - do not ship`; `evaluate` and `ship` carry the stamp on and
`ship --apply` refuses it.

## What `ship` may do

Per-sport overrides can live ONLY in `settings.toml` (`[weights.<sport>]`, `[scoring.<sport>]`); `settings.local.json` has no per-sport ids. `ship` is dry by
default. `--apply` needs `--target FILE` (no default) and a passing gate: fit and shadow runs real (not dry), the shadow run a true hold-out of the same fit
run, enough judged swaps, candidate regret rate strictly below today's (both replayed), median lead not lower by more than `--lead-tolerance-s` (default 5).
It backs the target up into the run folder, patches it (comments and line endings kept), checks that the daemon's own loader reads the numbers back, and
logs it. `--rollback RUN` restores the backup (refuses if the file changed since, unless `--force`). A daemon restart is needed after any change.

## Tests

`.venv\Scripts\python.exe -m pytest daemon director_training -q`. Fixtures are tiny synthetic data in temporary folders; the tests never touch the network,
`logs/` or the real settings.
