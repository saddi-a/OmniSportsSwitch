# Director Training plan (DRAFT v2, 2026-10-13, decisions added)

Status: a plan only. Nothing here is built, and nothing runs in the daemon. The user parked it on 2026-10-13 ("not building it tonight").
Numbers marked **[checked]** were read from the code or the logs on 2026-10-13. Anything marked **[unverified]** is an assumption.
This repo is public: only derived numbers, code and tiny synthetic fixtures may be committed. No stream URLs, no raw scraped text, no logs.

## 1. Goal and non-goals

Goal: learn, offline and per sport (football/soccer and American football first; basketball is parked), the weights and thresholds of the existing cues so that MAIN
moves BEFORE a goal (big chance, save, VAR, penalty awarded, red zone) with few regret swaps (a swap to a less interesting stream). The cues stay as features. The result is a handful of
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
- **Regret swap (replaces "false swaps per 10 min", user decision 2026-10-13).** The user does not mind a swap between two streams of similar
  interest, even if nothing special follows. A BAD swap is one where the new Main turns out LESS interesting than the stream it replaced.
  Definition, per swap at time `t` (old Main `O`, new Main `N`), window `W` = 30 s (parameter `regret_window_s`, user decision; American football may need a longer one later), after the picture-lag shift
  of section 4:
  - `tier(X)` = the highest event tier (1 best, 2, 3/none) that stream X has in `[t, t+W]`, from the labels of section 3 (structured events,
    commentary, automatic) and OVERRIDDEN by the user's review mark when they disagree.
  - **Regret** if `tier(O)` is strictly higher than `tier(N)` (for example a tier-1 event on `O` and none on `N`), counted once per swap.
    Ties (both quiet, or both with an event of the same tier) are not regret by default; a quiet tie becomes regret only if the user marks it so in the review (T3).
  - **Flip-flop regret**: a swap back to the previous Main within `flipflop_s` = 30 s (a starting flag; the user says it depends on the context)
    counts as regret for the first swap only when the context agrees (the stream just left had the higher-tier event in the window). Every such
    flip-flop is ALSO put in the review list (T3) for the user to judge.
  - Optional weight: regret is weighted by the tier gap (tier 1 vs none = 2, tier 2 vs none or 1 vs 2 = 1) so a missed goal weighs more.
  - **Regret rate** = regret swaps per 10 watched live minutes, and **regret share** = regret swaps / all swaps. Both per sport.
  Swaps of source `user` are excluded (the user chose); swaps while `O` is in a break or on an ad are excluded (leaving is right).
- **Miss** (separate metric, kept as before): a tier-1/2 event on a stream that is NOT Main while Main's `tier` in `[t-10 s, t+W]` is lower, and no
  swap to that stream happened at least 12 s ahead. Reported as misses per 10 min and per event. This is the other side of regret: regret is a
  wrong swap, a miss is a swap not made. Events with Main already on them are not misses.
- **Lead** of a goal stays the main upside metric (above). Precision of a swap is dropped (replaced by regret share).
- Secondary: time in MAIN that is a break or a replay; swaps per 10 min (reported for information, not budgeted).

What counts as "better": lead and coverage up (or equal) with regret rate not above the sport's budget and misses not up. Compare current rules
and learned numbers on the same held-out games. With ~15-20 games per sport expect wide error bars; report the leave-one-game-out spread and call a
change "better" only if it wins in a majority of held-out games. Budget: a REGRET-RATE budget per sport, set by the user AFTER we measure the
current rules (no placeholder numbers; see Decision 1 and question G). Until then, report the measured rate only.

What the dataset must hold for the regret and miss metrics (checked against `docs/dataset-format.md`, 2026-10-13):
- Provided already: a `swap` event with `promote`, `demote`, `source`, `reason` and both sides' components (`promote_c`, `demote_c`); a row for EVERY
  stream every ~2 s, so the next 30 s (and more) of both `O` and `N` exist even after `O` leaves Main (rows carry `is_main`, `role`, `state`, `ad`, `replay`,
  `game_id`, `clock`, `home`/`away`); `bu.qualifies`, `bu.blocked` and `swap_pending`, which show why a miss was not swapped.
- NOT recorded, so derived offline: the tier labels of events on each stream (they come from the feed by `game_id`, from the commentary, from
  `score_change`/`speech_cue`/`banner_cue` events and from the user's marks); the picture-lag shift per game; whether `O` was in a break at the
  swap (use the `state`/`ad` of its row at `t`).
- Missing or weak: rows with `game_id` null (no feed mapping) have only OCR score changes and spoken/banner cues as labels, so regret cannot be
  fully judged there; the review tool (T3) must let the user mark those. A swap's `t` and `mono` are the decision time, not the time the windows
  actually moved (a small delay, **[unverified]**). No field marks "a swap was suppressed because it would not have been regret": not needed.

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
   section 2), then live in log-only mode (shadow) for a few real evenings. Decides: ship or not. Stop if regret rate rises at equal coverage.
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
| T2 | Dataset builder from the T0 output, the older logs and the feed files | DONE 2026-10-13 (`daemon/dataset_builder.py`, `scripts/build_dataset.py`; older logs not rebuilt) | T0, T1, `feed.py` events | `daemon/dataset_build.py`, `scripts/build_dataset.py`; labels per section 4 with `label_quality`; split by game | label counts per game printed; a test enforces the split by game; dry run on tonight's games and the 3-day logs (numbers not used to change settings) | no |
| T3 (DONE 2026-10-13: `daemon/swap_review.py`, `scripts/review_swaps.py`, marks in `logs/review/<date>.jsonl`; the automatic verdicts come with the dataset builder) | Swap review tool: automatic good/false/late labels from the dataset plus the user's marks (user wins on disagreement) | after T2 | `events.log` swaps, dataset, feed events | one command lists a session's swaps with automatic verdicts; the user's marks stored in `logs/dataset/review/` | verdict rules unit-tested on fixtures; the user's mark overrides; session list fits on one screen | UI/design choice |
| T4 (PARTLY DONE 2026-10-13, branch `dataset-commentary`: `daemon/espn_history.py` = cached polite fetcher of finished games + commentary/plays parsing, used by the dataset builder as tier-2 labels; the replay of a game's events on a session clock is NOT built) | Historical feed mode: fetch a finished game by id and replay its events on the session clock | after T1 | T1 result, `feed.py`, `feed_gate.py` | `daemon/feed_history.py` + a script; tests with a small synthetic fixture | same FeedEvent list as live; rewind and goal-hold logic not tripped by a replay session; polite rate limit | yes (touches feed code) |
| T5 (not started; its fetcher exists in `daemon/espn_history.py`, request volume and game list still need the user's approval) | Priors study (offline, measuring only) | after T1 and T4 | game-id list, fetcher | `scripts/priors.py`; a table of base rates in this file | reproducible from ids; tiny fixture test; numbers only in the repo | game list and request volume |
| T6 | Fit and evaluate (logistic regression, leave-one-game-out, per sport) | INFRASTRUCTURE DONE 2026-10-13 (`director_training/fit.py`, `scripts/director_training.py fit`; refuses below 8 games / 25 events, `--dry-run` for the pipeline only); real fit waits for data: when the section 6 stage 2 gate passes (about 40 independent events per sport) | T2, T5 | `scripts/fit_weights.py` report with section 2 metrics | same metrics as `scripts/backtest.py` for the current rules on the same games | no |
| T7 | Shadow comparison on held-out games and live evenings | replay part DONE 2026-10-13 (`director_training/evaluate.py`, `evaluate --fit-run N`; an approximation, live shadow evenings not built); after T6 | T6 numbers | report: lead, coverage, regret rate, regret share and misses vs the per-sport regret budget | decision table for the user | ship decision |
| T8 | Ship as per-sport overrides | tool DONE 2026-10-13 (`director_training/ship.py`; dry by default, `--apply --target` + backup + `--rollback`; overrides can only live in settings.toml); after T7 go | T7 | `[scoring.<sport>]` / `[weights.<sport>]` sections in `settings.toml`, tests, `PLAN.md` | settings tests pass; revert values documented | yes |

## 9. Decisions (2026-10-13) and new open questions

Decisions by the user:
1. Swap quality: the user does NOT mind false alarms as such; a swap between streams of similar interest is fine. A BAD swap is a REGRET swap (the new Main is less interesting than the one it replaced), defined in section 2 with a relative window, flip-flops counted as regret, and MISSES as a separate metric. A separate regret-rate budget per sport; the user sets the numbers after we measure the current rules (no placeholders).
2. Event tiers: the table in section 3 is accepted as a starting point ("v1, to be tuned"); expect changes after the probe and the first data.
3. ESPN summary probe: approved, a separate agent is running it. Any source of information is welcome and other sources can come later; the scraping/copyright caveats and the public-repo rules (section 7) stay.
4. Swap review: BOTH automatic labels and the user's marks; where they disagree the user's mark is the final reference.
5. Recorder and log archive: built tonight by another agent (`logs/dataset/`, `logs/archive/`, both gitignored; storage is no constraint). Tonight is Champions League night with 3 games, the first recorded data.
6. Sports: football AND American football first. Basketball is forgotten for now (its work is parked, not deleted).
7. Data mix: replays AND live games, whichever is available when the user is free. The plan has no fixed schedule; stages are gated by event counts, not dates.
8. Raw data stays LOCAL ONLY in the gitignored `logs/`. The user declined a backup: a disk loss loses the data.
9. Hygiene rules stay.

Facts that shape shipping: per-sport settings overrides exist (README "Per-sport settings", `Config.for_sport` in `daemon/scoring.py`), so learned numbers go in `[scoring.football]` / `[weights.football]` style sections. Sport is per TAB (popup choice or first sport detected on any tile), so a mixed-sport DAZN multiview tab is an open limitation.

New open questions:
A. Mixed-sport multiview tabs: (1) shared values when tiles disagree (recommended: simple, safe); (2) use the sport of the tile with sound; (3) per-tile sport (a bigger change). Recommendation: (1).
B. Training on live games with no replay start: (1) estimate the lag per game from the clock match as in section 4 (recommended); (2) use only games where the user marks the kickoff time; (3) accept more label jitter. Recommendation: (1), and (2) when cheap.
C. Mixing replay and live rows when fitting: (1) pool them with a `session_type` flag and check the fit on live-only held-out games (recommended); (2) fit on replays only, test on live; (3) live only (slow). Recommendation: (1).
D. DECIDED: tonight's 3 Champions League games are the first football dataset and dry-run the dataset builder; they join the training pool later.
E. DECIDED: where a game lacks usable shot/save/corner commentary, fit on the structured events (goals, cards, penalties) AND add other label sources for chances (any source that passes the section 7 rules; the user's marks win).
F. REPLACED by the regret definition (user decision): the false-swap placeholders (3 and 4 per 10 min) are removed.
G. DECIDED: regret budget per sport. Measure today's rules first on recorded games, then the user chooses per sport. Working rule meanwhile: no worse than today's rate.
H. DECIDED: comparison window W = 30 s (a parameter; American football may need a longer one later).
I. DECIDED: a quiet tie (no event on either stream in the window) is NOT regret by default; it becomes regret only if the user marks it so in the review (T3).
J. DECIDED: flip-flop window 30 s as a starting flag; it counts as regret only when the context agrees (the stream just left had the higher-tier event) and is also listed in the review for the user to judge.
K. DECIDED: regret weight by the tier gap.

Per-tile sport (question A) is worked out in `docs/per-tile-sport.md` (Stage 1 then Stage 2: popup sport per tile; an unknown-sport tile is treated as football; freer tile focus off Main; stricter build-up threshold for silent tiles; instant sound switch on a build-up). Follow that file for the multiview limitation.

## 10. Decisions (2026-10-13, second round)

1. Tier 2 is split for football. 2a = shot on target, save, woodwork, VAR outcomes, goal overturned, own-box handball (penalty-related); 2b = corners, shots blocked / off target from the box, near free kicks. Rows and metrics report them apart (`group` on events, `tier2a_events`, `tier2b_events`, `misses_2a`, `misses_2b` in the summary); 2a is the main miss number. `tier` and the old columns stay unchanged.
2. A goal (feed or commentary) followed by a commentary 'goal overturned' of the same game within 90 s is cancelled as a goal (`cancelled_goal`, becomes tier 2a); the VAR chain stays 2a.
3. The feed stays the source for cards and penalties (no change).
4. One pipeline in two stages, shipped together: stage 1 fits the weights; stage 2 searches the build-up thresholds by replay.
5. The offline test replays the build-up as well: yes, but light (a small grid, not a full search).
6. Hold-out games are fixed, chosen once when 8 or more games exist, and not changed afterwards.
7. The ship gate adds "misses not higher than today" once the per-sport budgets are set (question G).
8. `ship --apply` needs an explicit `--target` file; it never guesses the settings file.

## 11. Long-term goal: an autonomous director (user, 2026-10-14)

The user wants the finished system to be "autonomous": after training it should not depend on the hand-written clues (keywords such as post, box, VAR, banner cues) at run time; the clues are for training only.

Today (phase 1, being built): the model's inputs are the existing signals, including the cue weights, so the daemon still needs the cue detection at run time. Training only supplies weights and thresholds.

Plan, in three phases:
1. **Phase 1, fit the current weights and thresholds** (cues kept as features). Cheap, safe, explainable. Needs about 8 games / 25 events per sport.
2. **Phase 2, learn from the signals directly.** Inputs: the PANNs audio embedding (already computed in the daemon), a text embedding of the speech (Whisper text + a small text model), simple picture signals. Labels: ESPN commentary tiers (2a strong / 2b weak), goals and VAR, the user's review marks. The keyword cues act as teachers and as the baseline to beat, not as inputs.
3. **Phase 3, shadow mode, then switch.** The learned model runs next to the rules, logs what it would have done and moves nothing. It replaces the cues only when it matches or beats them on fixed hold-out games and on the user's marks.

Needs and risks: far more games with RECORDED audio (hundreds, not 3; ESPN gives labels but no audio, so audio comes from replays through the daemon or from stored stream audio, which does not exist in bulk yet); harder to explain and debug live; a bad model could swap badly, hence shadow mode first. The surge cue (scoring-bugs-57) is a first step: it fires from the sound itself, with no words, and is recorded in the dataset.
Decision: keep phase 1 as the current work; phase 2 and 3 are planned, not started.
