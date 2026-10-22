# Dataset format (logs/dataset/)

Written by `daemon/recorder.py` while `scoring.dataset_mode` = 1 (default; 0 = nothing is written, changed live in the popup). Local only: `logs/` is never
committed, and the events file holds the commentators' words. Not part of the log archive and never pruned. Format version `v` = 1; a field is only ever
added, never renamed (a rename gets `v` = 2).

Files, one per local day, append-only, one JSON object per line, UTF-8, compact:

* `logs/dataset/<YYYY-MM-DD>.jsonl` one ROW per stream (tab) every ~1 s since 2026-10-17 (~2 s before; `SNAPSHOT_INTERVAL_S` in `daemon/server.py`, the cadence of the `MAIN <tab>: live ...` score line). Rows of the older days are ~2 s apart: readers search by time, never by row count.
* `logs/dataset/<YYYY-MM-DD>.events.jsonl` one line per EVENT.

Size, 4 streams: about 7200 rows/hour, about 1.1 KB each = about 8 MB/hour (about 70 MB for a 9-hour day); events add well under 1 MB/hour.
A full queue drops rows instead of slowing the daemon; drops are logged once a minute (`dataset: N row(s) dropped`).

## Row fields

Times: `t` = wall clock, epoch seconds (local timezone does not matter); `mono` = `time.monotonic()` seconds, the clock the daemon decides with (use it
to subtract times inside a run; it restarts at each daemon start). Ages and "left" values are seconds. `null` = not known / not applicable.

| Field | Meaning |
|---|---|
| `v`, `t`, `mono` | format version, wall time, monotonic time |
| `tab`, `role` | tab id; its window slot (`MAIN`, `LEFT`, `RIGHT_BOT`, ... as the windows report it) |
| `is_main` | this stream is on MAIN now |
| `sport` | the sport the scorer uses for this tab (lowercase: football, american football, or null) |
| `tile`, `tile_sport` | DAZN multiview only (null for a tab with one video): the tile that scored the tab this tick (the tab scores as its best tile; its picture signals `event`, `cue*`, `lev`, `replay`, `goal_*` are that tile's) and that tile's sport. Added 2026-10-14 (per-tile scoring, `docs/per-tile-sport.md`) |
| `warm` | false while the stream has no recent audio or is warming up: its audio fields are then null |
| `state`, `auto_state`, `override` | game state in use (`live`/`break`/`over`); the one detected from the picture; the one the user forced (or null) |
| `ad`, `on_ad` | ad flag in use (picture or music-only sound); ad seen in the picture |
| `can_main`, `weight` | the slot rules allow MAIN; the user weight of the tab |
| `score` | the stream score (what MAIN is compared by; negative = ad penalty) |
| `surge` | loudness rise over the stream's own baseline, 0..`surge_cap` |
| `crowd`, `speech`, `music`, `shout` | audio classifier probabilities 0..1 (`shout` is 0 under music) |
| `event` | fade of the last score change / goal banner / big play, 1 = just now, 0 = over |
| `cue_fade`, `cue_part` | fade 0..1 of the newest banner/spoken cue; its contribution to `score` (after leverage and the cue-alone cap) |
| `fav` | favourite multiplier (1 = none) |
| `lev` | leverage -1 (late blowout) .. +1 (late close game) |
| `loud`, `baseline` | recent mean loudness (RMS) and the slow baseline it is compared with |
| `top` | the 3 strongest AudioSet classes `[[name, p], ...]` (null when the classifier has not read it) |
| `cue` | the scorer's one cue `{value, age, strong}` (value = weight before leverage, age in s, strong = counts in full); null if none ever |
| `bu` | build-up tracker state, EVERY tick (see below) |
| `home`, `away` | voted score of the first tile that has one (OCR, voted over readings); null if none |
| `clock`, `period` | the first OCR game clock text of that tile (`"67:23"`); quarter / half as the tracker reads it |
| `replay` | a replay is shown over a live scorebug |
| `goal_hold`, `goal_age` | seconds the post-goal replay hold still runs; seconds since the last goal on this stream |
| `hold` | seconds a manual "Make Main" hold still runs on this stream (0 if none) |
| `game_id`, `league` | the feed game this stream is mapped to (ESPN id, league key) or null |
| `tiles` | one object per tile (DAZN multiview has several; a single player has one), see below |
| `main`, `frozen` | the tab that is MAIN; MAIN frozen by the hotkey |
| `last_swap_age` | seconds since the last swap (null = none since the start) |
| `swap_pending` | `[replaced tab, challenger tab, seconds it has qualified]` while a swap waits for its confirmation, else null |

`bu`: `level` build-up level (faded sum of the cues of the window + `support`; 0 with no cue), `cues_sum` the faded cue sum alone, `support` the surge/crowd/shout
part (`weights` x signals), `cues` = `[[age_s, faded_value, kind], ...]` oldest first (kind = banner cue name, `spoken:<cue>`, `note:<cue>` for a set-piece
note, `surge_note`, `crowd_note`, `surge_rise`), `min`/`gap`/`live` = the thresholds and the live switch of this stream's sport, `main_score` MAIN's score, `main_level` MAIN's own build-up level (its cues + surge/crowd; 0 on MAIN's own row), `hot_left` seconds MAIN stays 'hot' (after its own goal or a strong cue: `scoring.hot_game_s`; 0 = not hot, also 0 on MAIN's own row when not hot), `qualifies` level
>= `min` and >= `gap` above the larger of `main_score` and `main_level` (the cooldown and the guards ignored), `cooldown_left` s of the stream's build-up cooldown, `surge_note_left` /
`crowd_note_left` s until another surge / crowd note can be made, `crowd_run` consecutive loud-crowd readings, `tile` (multiview only: the tile this build-up belongs to, the tab's best tile; each tile has its own cues and cooldowns), `blocked` = why `take_main` would refuse now (also MAIN keeping its seat: "MAIN's own cue ..." / "MAIN under review ...", only when live and the row qualifies; "MAIN is hot (...)" while `hot_left` > 0 and the level is below MAIN's score or level + `hot_game_margin`)
(`dwell`, `goal on MAIN N s ago`, `not live`, `slot rules`, `on an ad`, `manual hold`, `MAIN is frozen`, `already MAIN`) or null.

`tiles[]`: `i` tile index, `sport` / `sport_src` (popup, hard, feed, title, weak, memory), `state`, `ad`, `replay`, `period`, `clock` (text), `clock_min`,
`home`, `away` (voted score), `audible` (this tile has the sound), `game_key` (the scorebug's two team codes, sorted, "AAA-BBB", or null: the game this video shows, which a multiplex channel changes; new games are also `new_game` events), `game_id`, `league`, `mapped_ok` (the feed mapping is verified by the OCR score/clock),
`score` (multiview only, else null: the score of THIS tile; the tab's `score` is the best of them; the audio terms are in the audible tile's score only).

## Event lines

Common: `v`, `t`, `mono`, `kind`, `tab`, `tile` (null when not about a tile), `sport` (a tile event of a multiview: that tile's sport; else the tab's), `main` (MAIN tab at that moment). In a multiview `buildup`, `speech_cue` and `surge_rise` events carry the tile they belong to (the audible tile for speech and sound; null for a tab with one video). Then by `kind`:

| kind | extra fields |
|---|---|
| `swap` | `source` (`decide` normal rule, `buildup`, `user`), `promote`, `demote`, `reason` (same text as the `swap:` log line), `promote_c` / `demote_c` (score, surge, crowd, speech, music, shout, event, cue of each side, or null) |
| `score_change` | `home`, `away` (voted score after), `strength`, `verdict` (feed verdict) or `note` |
| `goal_banner`, `big_play` | `text` (banner text), `strength` |
| `banner_cue` | `cue`, `banner` (text), `weight` |
| `speech_cue` | `cue`, `text` (what the commentators said), `language` |
| `speech_veto` | `p` (mean), `p_c`, `p_g`, `would_veto` (shadow only: what the learned spoken-cue veto would have said about the window that yielded these `cues`; changes nothing) |
| `surge_rise` | `weight`, `surge`, `crowd`, `level` (soccer: the crowd sound rose fast, a medium cue heard before the words; also a build-up cue of kind `surge_rise`) |
| `game_state` | `old`, `new` |
| `buildup` | `level`, `main_score`, `surge`, `crowd`, `outcome` (`moved MAIN`, `log only`, `not moved: <why>`), `moved` |
| `feed_goal` | `goal_kind`, `strength` (a feed goal released by the shadow gate) |

Reading it: `for line in open(path, encoding="utf-8"): row = json.loads(line)`.

## Dataset builder

`.venv/Scripts/python.exe scripts/build_dataset.py [YYYY-MM-DD ...] [--out logs\dataset_built] [--summary] [--holdout GAMEID,...]` (code `daemon/dataset_builder.py`).
Offline; reads `logs/dataset`, `logs/feed`, `logs/review` (the user's swap marks, which override the automatic verdict) and `logs/events.log`;
writes only into `--out` (default `logs/dataset_built/`, gitignored): `rows.jsonl` (JSON lines, because fields are null or nested and may be added; no text),
`swaps.jsonl`, `events.jsonl` (tier-1/2 feed events with their miss/lead status), `games.jsonl` (picture lag per game).

* Game of a row: its `game_id`, else its tile's, else a `feed map` interval, else the tab's only game of the day (`game_src`). Never guessed otherwise.
* Picture time of a feed event, in order: football clock (rows with an on-screen "MM:SS" within 90 s of the event's match clock give `row.t + (c - clock)`;
  median, refused if the spread is over 6 s or fewer than 3 rows), then the OCR score change of a mapped tab minus 12 s (goals), then the feed time plus the game's
  median lag (weak). Lag = picture time minus feed time, from goals only (a card is published minutes late). No clock and no score change: the event is
  UNRESOLVED and every row whose next 60 s it could fall in (feed time -150 s .. +400 s) is `aligned` false with no labels.
* Labels (stream time): `y_goal_30`, `y_t12_60`, `y_t1_60`, `next_event_kind`, `secs_to_next_event`, `label_quality` (`feed`, `feed-weak`), `label_weight` 0.5
  near the 30 s edge, `cue_t12_60` (from speech/banner cues; circular, kept apart). Tiers v1 are in `FEED_TIER`/`CUE_TIER`.
* Swaps: regret / ok / tie / excluded (user swap, old Main on an ad or break) / unjudged (no game or no feed) / flip-flop-candidate, window 30 s, weight = tier gap;
  quiet ties and flip-flops have `review` true. A swap whose game lag is unknown (an event could not be placed) or weak (placed only by the median lag) is NOT dropped:
  it is judged on the unresolved events placed at feed time + the sport's median measured lag (else `DEFAULT_LAG_S`) over a wider window (+-45 s), and carries
  `lag: "assumed"` (else `"measured"`), `window_s`, `lag_assumed_s`. The summary counts `judged_exact` and `judged_wide` (and the regrets of each) separately.
* Cues (speech/banner) are evidence ONLY when the user confirmed them: a missed-chance mark (`kind` "event", id `evt-HH:MM:SS-<tab>`) is itself a tier-2 event of that
  tab and confirms the cues of that tab within 30 s; a mark on a swap confirms the cues of its two tabs in [t-30, t+60]. Everything else stays out of the verdict;
  `verdict_with_cues` / `regrets_with_cues` (all cues, unconfirmed) is a separate labelled column.
* A goal on a stream Main left <= 30 s before it is BOTH a regret (of that swap) and a miss (event row has `left_main_s`); the summary reports regrets and misses on
  separate lines plus "misses where Main had left <= 30 s before".
* Leads: `lead_last_s` = event time minus the LAST swap to that stream whatever its age (system swaps only; the summary gives mean/median/min per sport); the strict
  count "swap made >= 12 s ahead and within 30 s before the goal" (`status` ahead) is a second line. Event `status`: main, ahead, covered, miss-late, miss-after, miss,
  user-main, unjudged, no-rows. User-marked missed chances (`src` "user") are tier-2 events and can be misses.
* Split: `split_by_game` (`--holdout`), never by row.

### ESPN commentary labels (tier 2 for football)

`scripts/build_dataset.py --fetch-history` fetches and caches the FINISHED soccer games the days' rows watched (sequential, >= 2 s apart, the daemon's
User-Agent; a cached game is never refetched; a game whose summary is not post/FULL_TIME is skipped and printed). The raw JSON lives only in
`logs/feed/history/<league>/<event>/` (gitignored). Without the flag the cache is read when present (`--no-commentary` ignores it); `--logs DIR` points
the script at another `logs/`. Code: `daemon/espn_history.py` (fetch, parse), `dataset_builder.place_commentary` (alignment, merge).

* Sources: the summary `commentary` list (each play is listed twice: one event per `play.id`; `time.value` = game seconds, `play.wallclock` = UTC, estimated from
  the nearest entry when missing) and the core `plays` endpoint (details by play id: `shotInfo`, `targetZone`; plus the `Free Kick` plays, which the commentary lacks).
* Tier v1 (`kind` -> tier): 1 = goal / own goal / penalty goal, red card, penalty AWARDED (no type in ESPN: the foul entry whose text starts `Penalty `, the twin
  `Penalty conceded by` is dropped); 2 = shot on target and save (text 'saved'), shot off target / blocked only from the penalty area, hit woodwork, handball
  (only when the offender is in his own box: unverified), VAR outcome (`var goal`, `var no goal`, `var decision` from the text; `goal overturned` = type 175),
  corner, free kick (taker x >= 75); 3 = foul, offside, yellow card, substitution, shots from outside the box, far free kicks (parsed, NOT loaded into the
  builder: no label would change and `next_event_kind` would fill with fouls). No 'big chance' and no 'VAR check started' exists in the data.
* Position rule: `fieldPositionX/Y` (0..100) are in the acting team's attacking direction; the penalty area is x >= 84 and 20 <= y <= 80 (16.5 m of 105 m deep,
  40.3 m of 68 m wide). No position: the wording ('from the centre of the box', 'very close range'; 'outside the box' = outside); neither: tier 3.
* Alignment (`place_commentary`): `clock` = the game clock `time.value` against the rows' on-screen clock (not for first-half stoppage, ambiguous with the second
  half; refused when it disagrees with the wallclock estimate by > 120 s); `wc-lag` = the wallclock plus the game's `lag_wc_s` (median of picture time minus
  commentary wallclock over the clock placements, at least 3, else over the resolved feed goals paired with a commentary goal; in `games.jsonl` with `lag_wc_jitter_s`,
  `lag_wc_n`, `lag_wc_src`). Measured on 3 games, `wallclock - time.value` is constant within a period (spread <= 2 s): the wallclock follows the match clock, it is not the
  operator's entry time. Unplaceable events are dropped and counted (`commentary_report`), they block no row.
* No double count: a commentary event of the same game and family (goal, red card, penalty, yellow card) as a feed event is the same moment (one-to-one nearest raw
  time, feed 60 s before to 900 s after the commentary entry); the feed event stays (`also_commentary`; a non-goal feed event placed only by the weak lag takes the
  commentary picture time, `align_refined`). Other commentary events are added with `src` 'commentary' in `events.jsonl` (misses are counted apart in the summary).
* Rows get `y_t12_60` with the commentary and `y_t12_60_feed` without. Cues stay circular and out of the verdict: commentary is outside evidence, a spoken cue is not.
* Tier 2 groups: events carry `group` ('2a' = shot on target, save, woodwork, VAR, goal overturned, own-box handball; '2b' = corner, shot off target / blocked from the
  box, near free kick; feed tier-2 events are 2a). The summary reports `tier2a_events`, `misses_2a`, `tier2b_events`, `misses_2b` next to the unchanged `misses`; 2a is the main miss number.
* Goal overturned: a feed or commentary goal followed by a commentary `goal overturned` of the same game within 90 s (`overturn_window_s`; the feed time may be up to
  20 s after the overturn's wallclock) is no goal: `goal` false, tier 2, group 2a, `cancelled_goal` true; the count is `commentary_report["goals_cancelled"]`.
* Limits: tier-2 shots/corners are many per game (about 30), so misses and regrets with them are far more frequent than with the feed alone; the tier-2 definition
  (every corner, every on-target shot) is a v1 choice to tune. The text of the operator's sentences is never written to the outputs.

* `rows.jsonl` also carries `event`, `cue_part`, `cue_fade` (added 2026-10-13 for `director_training`, which fits the weights on the score components; older files read them as 0). `build()` returns a `context` (tab events, cues, lags) so another swap sequence can be judged with the same code (`director_training/evaluate.py`).
