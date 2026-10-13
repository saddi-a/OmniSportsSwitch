# Learning plan (DRAFT v2, 2026-10-13, decisions added)

Status: a plan only. Nothing here is built, and nothing runs in the daemon. The user parked it on 2026-10-13 ("not building it tonight").
Numbers marked **[checked]** were read from the code or the logs on 2026-10-13. Anything marked **[unverified]** is an assumption.
This repo is public: only derived numbers, code and tiny synthetic fixtures may be committed. No stream URLs, no raw scraped text, no logs.

## 1. Goal and non-goals

Goal: learn, offline and per sport (football/soccer and American football first; basketball is parked), the weights and thresholds of the existing cues so that MAIN
moves BEFORE a goal (big chance, save, VAR, penalty awarded, red zone) with few false swaps. The cues stay as features. The result is a handful of
plain numbers shipped as per-sport settings overrides (`[scoring.football]` style), editable and explainable.

Non-goals:
- No deep or end-to-end model (too little data, GPU about 7 of 10 GB used, hard to debug).
- No cloud and no live API in the decision loop. The ESPN feed stays measuring-only (`feed_event_weight` 0). It is only used offline, for labels.
- No change to the daemon in this plan, except a small recorder (task T0, approved and in progress).
- No automatic self-tuning at runtime.

## 2. Success metrics

All computed by `daemon/backtest.py`-style code (read-only), per sport, on games NOT used for fitting. Definitions:

- **Lead** of a goal (or tier-1 event): seconds between the first moment the rule would have put the scoring stream in MAIN and the real event
  time. The real time is the feed time shifted by the measured picture lag (today the confirmation minus `GOAL_LAG_S` 12 s is the proxy).
  Positive = before. Report the median and the share of events with lead >= 12 s ("ahead", the existing `GOAL_LAG_S` rule). Events already in
  MAIN are reported separately (they need no swap).
- **Coverage**: of events NOT already in MAIN, the share with a swap at least 12 s ahead. Today (backtest, all logs, **[checked]**): football
  build-up tracker ahead of 5 of 11 goals (median 17 s); swap rule 0 of 11; American football 0 of 20 for both.
- **False swaps per 10 min**: swaps (or "would take MAIN" firings) in live play with no tier-1/2 event on that stream in the next 60 s, divided by
  watched minutes. Today (from PLAN.md section 0, one replay session, **[checked in the notes, not re-run]**): 4.4 firings per 10 min against
  1.7 before; the user saw no false alarm by eye.
- **Precision of a swap**: swaps followed by a tier-1/2 event within 60 s, over all swaps.
- Secondary: thrash (swap back within 30 s), and time in MAIN that is a break or a replay.

What counts as "better": at the SAME false-swap rate as today, coverage up by a clear margin, or at the same coverage fewer false swaps. With
~15-20 games per sport expect wide error bars; report the leave-one-game-out spread, not one number, and call a change "better" only if it wins in
a majority of held-out games. The trade-off (lead vs false alarms) is the user's call, see Decision 1. Proposal: a budget of at most 3 false swaps
per 10 min (football) and then maximise coverage at lead >= 12 s.

## 3. Event taxonomy per sport

Tiers say how much the user wants MAIN to move. Proposal, v1, to be tuned (accepted by the user as a starting point). The basketball row is parked.

| Sport | Tier 1 (move now) | Tier 2 (move if MAIN is quiet) | Tier 3 (note only) |
|---|---|---|---|
| Football | goal, penalty awarded, red card, VAR check on a goal/penalty | big chance, shot on target, save, post/bar, free kick near the box, corner | foul, offside, yellow card, substitution |
| American football | touchdown, field goal made, turnover returned for a score | red zone entry, 4th down near the goal line, field goal attempt, interception, fumble | sack, penalty flag, big gain |
| Basketball | game-winning/lead-changing shot in the last 2 min, buzzer beater | run (>= 8-0), close game last 5 min, technical/flagrant | free throws, timeout |

Label sources (best first) and what exists today:

| Event | Source | Available today? |
|---|---|---|
| Soccer goals, red and yellow cards | ESPN scoreboard events in `logs/feed/*.jsonl` | **[checked]** yes. Feed kinds seen: goal 36, penalty goal 2, yellow card 47, red card 5 (soccer, all polled leagues, not only watched games) |
| American football scores, turnovers, sacks, penalties, big plays | ESPN summary plays (`feed.parse_summary`) | **[checked]** yes: touchdown 39, field goal good 22, big play 102, penalty 81, sack 41, fumble 30, interception 9 (NFL + college) |
| Basketball scores | feed `score` snapshots (NBA, 14 games polled) | **[checked]** score yes, no play types; run/clutch derived from the score timeline |
| Soccer shots, saves, corners, VAR, penalty awarded | ESPN summary commentary list | **[unverified]** the daemon never fetches the soccer summary (`parse_summary` is American football only). A read-only probe is needed, NOT approved yet |
| Same, plus near misses, from French text (L'Equipe etc.) | scraping a site | Not recommended (terms/copyright, raw text must stay out of the repo); optional, local only |
| "Good swap / false alarm / late" | the user, after a game | Not recorded today. Needs the review tool (T3) |
| Big chances without a text source | the user's own judgement, or the build-up cues themselves | Only the cues, which is circular for fitting |

Unlabelled today: every tier-2 soccer event, all basketball plays, all "was this swap good" judgements. Until the probe says otherwise, tier-2 soccer
labels are the weakest part of the plan.

## 4. Dataset schema

One row per stream every 2 s, matching the existing `MAIN <tab>: live ...` score line cadence **[checked]**: lines carry `live|break`, `fav`,
`score`, `surge`, `crowd`, `speech`, `music`, `shout`, `lev`, `top` (3 AudioSet classes) and a `MAIN` marker.

Available today from `daemon.log` score lines **[checked]**: time, tab id, MAIN flag, live/break, fav, score, surge, crowd, speech, music, shout, lev.
Available today from `events.log` **[checked]**: `sport detected`, `game state` (live/break), `anticipation <cue> tab N [(spoken)] [weight]`, `speech
cue ...`, `score change`, `goal banner`, `scorebug hidden/back`, `feed map`, `feed rewind`, `buildup would take MAIN` (level, cues, surge, crowd, only when it
fires), `swap`/`arrange`.

NOT in any log today (the recorder must add them, **[checked by grep]**): the build-up LEVEL at every tick (only logged when it fires); the list of
active cues with age and faded value at every tick; the sport on the row; the match clock, period and score (only inside `feed`/OCR lines, not
per row); a stable game id; the replay/hold flags (`stream.replay`, goal hold); whether the tile was MAIN-eligible.

Proposed row (CSV or JSON lines, one file per session, `logs/dataset/<session>/<stream>.jsonl`, untracked):

| Group | Fields |
|---|---|
| Key | `session`, `game_key` (feed game id when known, else session+tab), `tab`, `t` (UTC seconds, 2 s grid) |
| Context | `sport`, `period`, `clock_s`, `score_home`, `score_away`, `lev`, `is_main`, `state` (live/break/over), `replay_now`, `goal_hold` |
| Audio | `surge`, `crowd`, `speech`, `music`, `shout`, `fav` |
| Cues | per cue kind: strongest faded value in the last 60 s and age of the newest (kinds as in `cues.py`: free_kick, chance, penalty_awarded, post, post area, var/review, corner, red zone ...); `cue_sum_60s` |
| Rule outputs | `score` (current stream score), `buildup_level`, `would_swap_rule`, `would_buildup` (so the current rules can be compared row by row) |
| OCR | `ocr_score_conf`, `ocr_clock_ok` (vote counts, not raw text) |
| Labels | `y_goal_30` (goal or penalty awarded in the next 30 s), `y_t12_60` (any tier-1/2 event in the next 60 s), `y_t1_60`, `next_event_kind`, `secs_to_next_event`, `label_quality` (feed / commentary / user) |

No transcripts and no OCR text in the dataset file (privacy, copyright): only derived cue kinds and counts.

Label time alignment: the feed lags the picture by 0-40 s and the lag varies by game **[checked: feed lead over the picture median +18 s football, +33 s
NFL in PLAN.md; backtest shows +49 s football, min -16, max +110]**. Method: for each game find the lag by matching the feed's clock to the on-screen
clock (OCR clock votes) at several moments, take the median per game, keep the spread as `label_jitter_s`; rows within 10 s of an event boundary are
down-weighted. Replays (user-controlled playback) make this easier: the stream clock is exact if the user starts at kickoff, so lag is a constant per
session.

Split: by GAME, never by row. Leave-one-game-out for fitting; a final untouched set of at least 3 games per sport for the shadow test.

Size estimate: 3600 rows per stream per 2 h game; a session of 4 parallel games = 14.4k rows (about 3 MB as JSONL). 20 games per sport = 72k rows per
sport. Positives are few: about 3 goals per soccer game x 15 rows (30 s / 2 s) = 45 positive rows per game, about 900 for 20 games, but from only about
60 independent events. Plan the model size on the 60, not the 900.

What exists to train on today **[checked]**: logs cover 2026-10-10 22:58 to 2026-10-13 20:57 (about 3 days). `events.log` keeps everything (2 MB x 10
files, 9064 lines). `daemon.log` rotates at 5 MB with 5 backups (3 exist; the oldest starts 2026-10-10 22:58 and rolls over about every 4-18 h with load), so
older score lines will be lost within a day or two of heavy use. Score readings: 34,800 across 4 files. Spoken transcripts exist only in the 3 newest files
(7,200 `speech heard` lines). Sport detections: 124 American football, 52 football, 5 basketball, 23 unknown. Goals by the backtest: 23 football, 64
American football (many are OCR noise), 3 unknown. Feed ground truth on tiles we really watched: football 8 goals (OCR saw 6), American football 16
(OCR saw 2). So: roughly 8 usable football and 16 NFL events today; basketball essentially none. Far too little to fit anything now.

## 5. Replay-session protocol

- Games per sport: 15-20 football and 15-20 American football (basketball parked) before any fit (checked against the 60-independent-events estimate above).
  A session is 3-4 games in parallel for about 2 hours; so about 5 sessions per sport.
- Choosing games: finished games with a known feed id; mix of high-scoring and quiet (0-0, 1-0) games; a spread of leagues, competitions and
  commentary languages and broadcasters (the cues are French and English phrases; broadcaster style shifts the speech cues); avoid highlights, they
  have no steady crowd baseline. Hold 3 games per sport back for the final test, chosen before looking at results.
- Per session: start every replay from kickoff (or period start) so the stream clock is known; one game per tab with the usual window layout; do
  not cover a stream (OCR reads the screen); Edge hardware acceleration off; daemon running with the dataset recorder on and `feed_mode` historical.
- What to record: the dataset rows above (sensor side), the daemon's own swaps (what the current rules did), the historical feed events (labels),
  and the session notes (game id, language, broadcaster, replay start offset). The user's judgement of swaps (good/false/late) goes in after the
  session through the review tool (T3).
- Alignment: the on-screen clock vs the feed clock (median over the game), cross-checked on the first goal. Replays must not look like noise: the
  rewind logic (`feed rewind`) and the goal-hold/replay logic must be told "this is a replay session" so a rewind is not treated as a glitch **[unverified: how rewind logic reacts to a historical feed is not tested]**.
- Realism caveat: a replay has no "other stream to compare with" unless 3-4 games run together, which is why parallel sessions are used; the swap
  decision is relative (a stream vs MAIN), so the replay mix should resemble a normal evening.

## 6. Model stages and stop points

Each stage ends with a short report to the user and a go/no-go.

1. **Priors study (offline, measuring only).** From bulk ESPN history: base rates such as P(goal within 30/60/120 s | corner, penalty awarded, red card,
   shot on target) for soccer and P(score | down, distance, yard line) for NFL. Needs the soccer
   probe (T2) and the history fetcher. Decides: do the cue weights have the right order and size? Stop if the base rates contradict the current ordering
   in a way that does not look like noise.
2. **Dataset builder.** Turn recorded sessions plus labels into the rows above; sanity plots (label rate by cue, by sport). Decides: is there enough clean
   data? Stop if fewer than about 40 independent events in a sport.
3. **Fit a small model.** Logistic regression (L2) per sport on the cue and sensor features; optionally a depth-3 tree for a comparison; priors set the
   starting weights or a regulariser toward them. Leave-one-game-out. Decides: which features matter, which thresholds. Stop if the model is not
   clearly better than the current rules on held-out games, or the weights are unstable between folds.
4. **Shadow comparison on unseen games.** Run the learned numbers in the backtest beside the current rules on the held-out games (metrics of
   section 2), then live in log-only mode (shadow) for a few real evenings. Decides: ship or not. Stop if false swaps rise at equal coverage.
5. **Ship as per-sport overrides.** Plain numbers in `settings.toml` under the per-sport sections (needs the override mechanism being built on 2026-10-13),
   a test keeping defaults equal to `settings.toml`, a note in `PLAN.md`, and one-line revert values. Decides: nothing more; revert is a settings change.

## 7. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Label noise (a "chance" is subjective) | Use goal/penalty/card labels as the main target; use commentary labels only as secondary; carry `label_quality`; check per-cue precision by hand on a sample |
| Feed lag 0-40 s and varying | Per-game lag from the clock match; `label_jitter_s`; down-weight rows near boundaries; replays with a known start |
| Overfitting (60 independent events) | Few features, strong regularisation, leave-one-game-out, a final held-out set chosen in advance, report spread |
| Broadcaster/language shift (speech cues are French and English phrases) | Record language and broadcaster; check per-broadcaster hit rates; do not pool languages blindly |
| Rotating logs lose data | The compact recorder writes its own non-rotating files per session; copy `events.log` and the feed files at the end of each session |
| Replays differ from live (rewind, no live feed, no real-time stream order) | Mark session type; keep a few live games for the shadow stage |
| Legal/terms (scraping, ESPN's unofficial JSON) | Only polite, rate-limited, read-only requests; store derived numbers only; no scraping of commercial sites without the user's explicit decision |
| Public repo hygiene | `logs/dataset/` and raw dumps are untracked (check `.gitignore` before the first recorder commit); commit only code, tiny synthetic fixtures and aggregate numbers; no stream URLs or transcripts |
| Circularity (cues label chances, then the model learns cues) | Never use the cue itself as the chance label; fit to goals/penalties/cards and structured events |
| GPU and CPU load of the recorder | Recorder only writes numbers already computed; measure the cost before enabling |
| Model complexity creeping in | The stop points above; any non-linear model needs a new decision |

## 8. Work breakdown (agent-sized tasks)

Basketball tasks are parked. Order is by dependency, not by date.

| Id | Task | Status | Inputs | Outputs | Acceptance | User approval |
|---|---|---|---|---|---|---|
| T0 | Recorder (`logs/dataset/`) + log archive (`logs/archive/`) | in progress (another agent, tonight) | `server.py` score tick, `BuildUp`, `cues`, `GameTracker` | non-rotating per-session files in the section 4 shape, archive of rotated logs; both gitignored | schema tests; no text fields in the dataset; cost per tick measured; off switch | given |
| T1 | ESPN summary probe (one finished soccer game, one NFL game) | in progress (separate agent) | public JSON | field list (commentary, key events, play types) written into this file; no raw dump committed | what exists and what is missing stated; derived facts only | given |
| T2 | Dataset builder from the T0 output, the older logs and the feed files | next | T0, T1, `feed.py` events | `daemon/dataset_build.py`, `scripts/build_dataset.py`; labels per section 4 with `label_quality`; split by game | label counts per game printed; a test enforces the split by game; dry run on tonight's games and the 3-day logs (numbers not used to change settings) | no |
| T3 | Swap review tool: automatic good/false/late labels from the dataset plus the user's marks (user wins on disagreement) | after T2 | `events.log` swaps, dataset, feed events | one command lists a session's swaps with automatic verdicts; the user's marks stored in `logs/dataset/review/` | verdict rules unit-tested on fixtures; the user's mark overrides; session list fits on one screen | UI/design choice |
| T4 | Historical feed mode: fetch a finished game by id and replay its events on the session clock | after T1 | T1 result, `feed.py`, `feed_gate.py` | `daemon/feed_history.py` + a script; tests with a small synthetic fixture | same FeedEvent list as live; rewind and goal-hold logic not tripped by a replay session; polite rate limit | yes (touches feed code) |
| T5 | Priors study (offline, measuring only) | after T1 and T4 | game-id list, fetcher | `scripts/priors.py`; a table of base rates in this file | reproducible from ids; tiny fixture test; numbers only in the repo | game list and request volume |
| T6 | Fit and evaluate (logistic regression, leave-one-game-out, per sport) | when the section 6 stage 2 gate passes (about 40 independent events per sport) | T2, T5 | `scripts/fit_weights.py` report with section 2 metrics | same metrics as `scripts/backtest.py` for the current rules on the same games | no |
| T7 | Shadow comparison on held-out games and live evenings | after T6 | T6 numbers | report: lead, coverage, false swaps per 10 min vs the per-sport budget | decision table for the user | ship decision |
| T8 | Ship as per-sport overrides | after T7 go | T7 | `[scoring.<sport>]` / `[weights.<sport>]` sections in `settings.toml`, tests, `PLAN.md` | settings tests pass; revert values documented | yes |

## 9. Decisions (2026-10-13) and new open questions

Decisions by the user:
1. False-swap budget: a separate budget PER SPORT; the user sets the numbers later. Proposed defaults to start from: football 3 per 10 min, American football 4 per 10 min (more stoppages and plays), measured with the section 2 definitions.
2. Event tiers: the table in section 3 is accepted as a starting point ("v1, to be tuned"); expect changes after the probe and the first data.
3. ESPN summary probe: approved, a separate agent is running it. Any source of information is welcome and other sources can come later; the scraping/copyright caveats and the public-repo rules (section 7) stay.
4. Swap review: BOTH automatic labels and the user's marks; where they disagree the user's mark is the final reference.
5. Recorder and log archive: built tonight by another agent (`logs/dataset/`, `logs/archive/`, both gitignored; storage is no constraint). Tonight is Champions League night with 3 games, the first recorded data.
6. Sports: football AND American football first. Basketball is forgotten for now (its work is parked, not deleted).
7. Data mix: replays AND live games, whichever is available when the user is free. The plan has no fixed schedule; stages are gated by event counts, not dates.
8. Raw data stays LOCAL ONLY in the gitignored `logs/`. The user declined a backup: a disk loss loses the data.
9. Hygiene rules stay. Cleaning the public history is decided separately and does not block this work.

Facts that shape shipping: per-sport settings overrides exist (README "Per-sport settings", `Config.for_sport` in `daemon/scoring.py`), so learned numbers go in `[scoring.football]` / `[weights.football]` style sections. Sport is per TAB (popup choice or first sport detected on any tile), so a mixed-sport DAZN multiview tab is an open limitation.

New open questions:
A. Mixed-sport multiview tabs: (1) shared values when tiles disagree (recommended: simple, safe); (2) use the sport of the tile with sound; (3) per-tile sport (a bigger change). Recommendation: (1).
B. Training on live games with no replay start: (1) estimate the lag per game from the clock match as in section 4 (recommended); (2) use only games where the user marks the kickoff time; (3) accept more label jitter. Recommendation: (1), and (2) when cheap.
C. Mixing replay and live rows when fitting: (1) pool them with a `session_type` flag and check the fit on live-only held-out games (recommended); (2) fit on replays only, test on live; (3) live only (slow). Recommendation: (1).
D. Tonight's 3 Champions League games are football only: (1) keep them as the first football dataset and dry-run the builder on them (recommended); (2) hold them as an untouched test set; they are too few for a test set alone. Recommendation: (1).
E. If the probe finds no soccer commentary: (1) fit football on goals, cards and penalties only and treat chances as cue-only (recommended); (2) add a manual source later for a few games; (3) wait. Recommendation: (1).
F. Per-sport budget numbers: the defaults in Decision 1 are guesses from one replay session; the user should confirm or replace them after the first measured false-swap rate per sport.
