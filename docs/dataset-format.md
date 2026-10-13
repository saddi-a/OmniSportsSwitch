# Dataset format (logs/dataset/)

Written by `daemon/recorder.py` while `scoring.dataset_mode` = 1 (default; 0 = nothing is written, changed live in the popup). Local only: `logs/` is never
committed, and the events file holds the commentators' words. Not part of the log archive and never pruned. Format version `v` = 1; a field is only ever
added, never renamed (a rename gets `v` = 2).

Files, one per local day, append-only, one JSON object per line, UTF-8, compact:

* `logs/dataset/<YYYY-MM-DD>.jsonl` one ROW per stream (tab) every ~2 s (the cadence of the `MAIN <tab>: live ...` score line).
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
| `sport` | the sport the scorer uses for this tab (lowercase: football, american football, basketball, or null) |
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
note, `surge_note`, `crowd_note`), `min`/`gap`/`live` = the thresholds and the live switch of this stream's sport, `main_score` MAIN's score, `qualifies` level
>= `min` and >= `gap` above MAIN (the cooldown and the guards ignored), `cooldown_left` s of the stream's build-up cooldown, `surge_note_left` /
`crowd_note_left` s until another surge / crowd note can be made, `crowd_run` consecutive loud-crowd readings, `blocked` = why `take_main` would refuse now
(`dwell`, `goal on MAIN N s ago`, `not live`, `slot rules`, `on an ad`, `manual hold`, `MAIN is frozen`, `already MAIN`) or null.

`tiles[]`: `i` tile index, `sport` / `sport_src` (popup, hard, feed, title, weak, memory), `state`, `ad`, `replay`, `period`, `clock` (text), `clock_min`,
`home`, `away` (voted score), `audible` (this tile has the sound), `game_id`, `league`, `mapped_ok` (the feed mapping is verified by the OCR score/clock).

## Event lines

Common: `v`, `t`, `mono`, `kind`, `tab`, `tile` (null when not about a tile), `sport`, `main` (MAIN tab at that moment). Then by `kind`:

| kind | extra fields |
|---|---|
| `swap` | `source` (`decide` normal rule, `buildup`, `user`), `promote`, `demote`, `reason` (same text as the `swap:` log line), `promote_c` / `demote_c` (score, surge, crowd, speech, music, shout, event, cue of each side, or null) |
| `score_change` | `home`, `away` (voted score after), `strength`, `verdict` (feed verdict) or `note` |
| `goal_banner`, `big_play` | `text` (banner text), `strength` |
| `banner_cue` | `cue`, `banner` (text), `weight` |
| `speech_cue` | `cue`, `text` (what the commentators said), `language` |
| `game_state` | `old`, `new` |
| `buildup` | `level`, `main_score`, `surge`, `crowd`, `outcome` (`moved MAIN`, `log only`, `not moved: <why>`), `moved` |
| `feed_goal` | `goal_kind`, `strength` (a feed goal released by the shadow gate) |

Reading it: `for line in open(path, encoding="utf-8"): row = json.loads(line)`.
