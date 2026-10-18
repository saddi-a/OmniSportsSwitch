# Sport per tile (built in two stages, see section 8)

Status: Stage 1 (T1, T2) and Stage 2 (T3 to T6) are BUILT (2026-10-13 and 2026-10-14); sections 1 to 7 are the original scoping (what it looked like BEFORE), section 8 the Stage 2 result and live checklist. Line numbers are from the working tree of `develop` on 2026-10-13
(includes the uncommitted per-sport overrides), so they may move.

User decision (2026-10-13): today one sport is picked per TAB. In a DAZN multiview a tab can hold tiles of different
sports (e.g. an NFL tile and a football tile). "The daemon needs to be aware of each sport and treat each one independently."

Legend: VERIFIED = read in code. ASSUMED = not checked in code or only from docs.

## 1. Where a sport is read today

### 1.1 How the tab sport is made (VERIFIED)

`Server._tab_sport(tab_id)` (`daemon/server.py:540`) = the popup choice `self.sports[tab_id]`, else the first non-empty value in
`self.sport_seen` whose key is `(tab_id, any tile)` (dict insertion order, so "first tile that was ever detected", not the
audible tile). `sport_seen[(tab, tile)]` (`server.py:644-646`) is only the HARD detection of that tile (`SportDetector.update`).
It does NOT contain the other sources that `choose_sport` uses.

Per tile, `_update_game_states` (`server.py:628-646`) computes a richer value with `choose_sport(popup, hard, feed league,
tab title, weak league words, memory)` and stores it in `GameTracker.sport` (`tracker.set_sport`, line 637) and
`sport_choice_seen[(tab, tile)]` (log/recorder only). So there are TWO sport values per tile today:

* tile sport (rich): `trackers[(t,i)].sport` / `sport_choice_seen[(t,i)][0]`. Per tile.
* tab sport (poor): `_tab_sport(t)`. Per tab. Differs from the tile sport when a tile's sport comes from feed league, title,
  weak words or memory (then `_tab_sport` can be None while the tile's tracker says "football").

`SportMemory` (`daemon/sport_memory.py`, used `server.py:636,639`) is keyed by TAB id: it remembers the last reliable sport of a
tab and offers it as a start guess to every tile of that tab. In a mixed tab it flips between the two sports and is the wrong
guess for one of the tiles (lowest priority, so only matters until a tile has evidence). `server.py:130,636,639`.

`self.sports[tab_id]` (popup choice) is per tab by design (`register`, `server.py:149`). `choose_sport` ranks it first
(`sport_choice.py`), so a popup choice forces the same sport on EVERY tile of the tab (also `server.py:160`, `628`).
The popup has one sport selector per tab (ASSUMED: `extension/popup.js` not re-read).

### 1.2 Read sites

"Data" = what the site's inputs are keyed by. T = per tab, TILE = per tile `(tab id, video index)`.

| # | Site (file:line) | Which sport it uses | Data it works on | Audio-derived? |
|---|---|---|---|---|
| 1 | `Scorer.sport_of = Server._tab_sport` (`server.py:87`), `Scorer.cfg_for(tab)` (`scoring.py:240-242`) | tab sport | resolves ONE cfg view per tab | n/a |
| 2 | `Scorer.components` (`scoring.py:385-424`): weights, surge_cap, replay_dampen, event/cue/leverage terms, stale_s, warmup_s | tab sport (`cfg_for(tab)`) | ONE `_Stream` per tab: `event_at`, `anticipation_*`, `leverage`, `replay`, `on_ad`, `auto_state` are all tab values folded from the tiles | mixed: surge/crowd/speech/music/shout are audio (T, audible tile only); event/cue/leverage come from TILEs but are stored per tab |
| 3 | `Scorer.set_anticipation` (`scoring.py:316`): `anticipation_s`, `cue_minor` | tab sport | called with a tab id from both banner cues (a TILE, `server.py:666`) and spoken cues (T) | banner: no; spoken: yes |
| 4 | `Scorer.goal_hold_left` (`scoring.py:338`): goal_hold_s, extend, max | tab sport of the stream that scored | `stream.goal_at` set by any tile's goal (`set_event(goal=True)` `server.py:682,722,740,451`) | no |
| 5 | `Scorer.decide` (`scoring.py:483-494`): min_score, threshold, min_gap, confirm_s, hold_break_score | tab sport of the CHALLENGER tab | tab scores | uses T scores |
| 6 | `Scorer.take_main_block` (`scoring.py:518-543`): min_dwell_s, hold_break_score, goal_hold_break | tab sport of the challenger tab | tab streams | uses T scores |
| 7 | `Server._check_buildup` (`server.py:366-407`): cfg by `cfg_for`, `is_soccer(_tab_sport)` (line 370), `moves_main(_tab_sport)` (line 387); `BuildUp` keyed by tab id (`buildup.py:42-59`) | tab sport | one cue list per tab. Cues arrive from TILEs (banner, `server.py:667`) and from the audible commentary (spoken, `server.py:517,521`). Support = surge+crowd+shout of the TAB (audio) | cues: banner TILE, spoken T; support: T audio |
| 8 | `_speech_loop` (`server.py:474-521`): `cfg_for(tab)`, `find_speech_cues(text, _tab_sport)` (CUE_SPORTS filter, soccer recap/negation filter, `speech.py:136-149`), `cue_speech.record(.., sport)`, `speech_cue_weight`, `is_strong_cue`, `tracker_note_weight` | tab sport | `speech_buffers[tab]` = tab audio = whatever tile has sound | yes, T audio |
| 9 | `cues.py`: `speech_cue_weight` (:37), `tracker_note_weight` (:50), `is_strong_cue` (:59), `group_of` (:69), `banner_tier` (:78), `speech_tiers` (:88), `cue_weight` (:151) | sport passed in by the caller | pure; caller passes tab sport (speech) or tile sport (banner) | caller-dependent |
| 10 | `SpeechMemory` (`cues.py:109`, used `server.py:502,664`) | stored per TAB, sport only used when recording | tab; a spoken tier heard (audible tile) is applied to a banner cue of ANY tile of the tab (`cue_weight(.., self.cue_speech.tier(tab_id, now), ..)` `server.py:664`) | yes |
| 11 | Banner cues loop (`server.py:660-668`): `tile_cfg = config.for_sport(tracker.sport)`, `end_zone_factor`, `cue_weight`, `is_strong_cue` | TILE sport (rich) | TILE | no |
| 12 | `big_play`, `goal_banner` strengths (`server.py:671,678`) | TILE sport (`tile_cfg`) | TILE `event_at`, then `set_event(tab)` | no |
| 13 | `GameTracker` (`game_state.py:163-219`): `profile_for(sport)` (freeze_s, no_clock_s per sport), `BANNER_CUE_SPORTS` (:433), goal banner only "football" (:447), end-zone only "american football" (:472), `MAX_CLOCK_MIN` (:339) | tile sport (`set_sport`) | TILE | no |
| 14 | `ScoreWatcher.update(.., sport=...)` (`server.py:693`, `score_watch.py:401-527`): event size (American football), quarter-end rule | `self.sports.get(tab) or self.sport_seen.get((tab, tile))` = popup or HARD detection of THIS tile | TILE | no. Note: not the rich tile sport. |
| 15 | `tab_leverage(tiles)` (`server.py:753-761`, `leverage.py:28-56`): per tile `(sport, score, period, minute)`, `cfg.for_sport(sport)` limits; result = max over tiles | tile sport (popup or `sport_seen`) | TILE inputs, ONE tab value out (`scorer.set_leverage`), then applied with the TAB sport's `leverage_weight`/`cue_leverage_gain` (site 2) | no |
| 16 | `set_on_ad` all tiles (`server.py:745`), `set_replay` any tile (`:746`), `combine(states)` live if any tile live (`:747`, `game_state.py:541`) | none (but tab results) | TILEs folded to a tab | no |
| 17 | Feed: `feed_map` mapping per `(tab, tile)` (`server.py:632-633`), `feed_gate` keyed by game id with `sport_of[game_id]` (`feed_gate.py:123,157,172`), `feed_map.sport_of` (`feed_map.py:108`) | the feed game's sport (from the matched game) | TILE | no. Already independent per tile (feed gate sets events via `set_event(f.tab)`, tab-level). |
| 18 | Tile focus: `_focus_commands` (`server.py:763-789`), `TileFocuser` (`multiview.py`) | no sport at all | per tile `event`, `state`, `ad`; `crowd` only for the audible tile, taken from the TAB components (`server.py:781-782`) | crowd: yes (T audio, audible tile) |
| 19 | `_check_expand` / `_expand_swap` (`server.py:791-826`) swaps `trackers, watchers, event_at, ocr, sport_detectors, sport_seen` between two tiles. Does NOT swap `sport_choice_seen`, `replay_signal`, `held_change`, `unconfirmed`, `fed`, `fav_seen`, `rule_seen`, feed mappings, per tile gate state | follows streams | TILE | no |
| 20 | Recorder (`recorder.py:173,192,240`) writes `row["sport"] = _tab_sport(tab)` and per-tile `sport` (rich); events get the tab sport (`server.py:310`) | both | both | no |
| 21 | `arrange.py` / `plan_swap` / `plan_fix`: no sport at all, work on tabs and slots | n/a | T | n/a |
| 22 | `backtest.py:107` determines a goal's sport itself from logs (own logic, per detection) | n/a | offline | n/a |

### 1.3 What is per tile today (VERIFIED, `server.py:100-130`)

`ocr`, `trackers` (GameTracker: state live/break/over, ad, replay, period, clock, banners, big play, goal banner, sport),
`watchers` (ScoreWatcher: score, goal detection), `event_at` (tile mirror used only for tile focus), `sport_detectors`, `sport_seen`,
`sport_choice_seen`, `replay_signal`, `held_change`, `unconfirmed`, `fed`, `fav_seen`, `rule_seen`, `digit_*`/`gap_*`/`zoom_*`
bookkeeping, feed mapping (`feed_map`) and gate state. All keyed `(tab id, video index)`.

### 1.4 What is per tab today (VERIFIED)

* `Scorer.streams[tab]` (`scoring.py:196-222`): ONE audio history, baseline, tags (crowd, speech, music, shout), ONE `event_at`,
  ONE `anticipation_at/value/strong`, ONE `goal_at`/replay hold, ONE `leverage`, ONE `replay`, ONE `on_ad`, ONE `auto_state`,
  favourite, weight, can_main, override.
* `BuildUp` cues and cooldowns (`buildup.py`), `SpeechMemory`, `speech_buffers`, `buffers` (classifier), `tags`, `rects`, `roles`,
  `allowed`, `limits`, `sports` (popup), `tab_titles`, `page_text`, `team_text`, `SportMemory`.
* Swap and arrange: tabs and windows (`Scorer.main`, `Swap(promote, demote)`, `arrange.py`).

So the picture side is read per tile, but everything is folded into ONE tab stream (`set_event(tab_id)`, `set_anticipation(tab_id)`,
`set_leverage(tab_id)`, `set_replay(tab_id)`, `set_on_ad(tab_id)`, `set_auto_state(tab_id)`) before the Scorer sees it. The Scorer
cannot tell which tile produced an event. That is the structural reason a per-tab sport cannot serve a mixed tab.

### 1.5 Audio inputs: per tab (VERIFIED for the capture, ASSUMED for DAZN muting)

* Capture is per TAB: `chrome.tabCapture` in `extension/background.js:213`, one stream and gain node per tab in
  `extension/offscreen.js:11,49-53,81-106`; messages carry only the tab id (`server.py:209-224`, `handle_audio`). Muting is a gain
  after the analysis tap (PLAN section 5 "measured before the gain").
* So `surge`, `crowd`, `speech`, `music`, `shout`, `ad` by music, the classifier batch (`_classify_loop`, `server.py:409-426`), the
  Whisper transcripts and every spoken cue are TAB values. The extension reports `audible = !video.muted && video.volume > 0`
  per video (`background.js:120-123`); in a multiview exactly one tile is expected to be audible (ASSUMED from PLAN section 6:
  "only the tile that has audio focus has sound"; code shows `next(... if r.get("audible"))` at `server.py:802`).
* Therefore audio-derived inputs describe the AUDIBLE tile only. They are wrong for the other tiles: those get no crowd, no
  speech, no cues from commentary. Picture signals (score change, banners, state) exist for all tiles.
* Audible tile changes (focus_tile after 30 s dwell, or by the user): the tab baseline (60 s average `baseline_s`) mixes two
  different sources, so the first seconds after a switch can show an artificial surge or dip; `speech_buffers` (window
  `speech_window_s`) and the 3 s classifier buffer hold audio of both tiles for a few seconds; `SpeechMemory`, `BuildUp` cues and
  the stream's `tags` do not know the sound changed. ASSUMED effect size: small after a few seconds but not measured.

### 1.6 How multiview tiles compete today (VERIFIED)

Between tiles of one tab only the tile focus decides (`TileFocuser.decide`): score = floor 0.05 + event (fresh score change on THAT
tile, linear fade over `event_s`) + crowd (only for the audible tile, from the tab's components) times a status multiplier
(break 0.25, over 0), ads negative; another tile must be 1.5x better, min 30 s between changes per tab. The sport of a tile plays
no part (an American football "big play" event and a football goal count the same through `event_at` strength).
Between tabs the Scorer compares tab scores (the folded values) and swaps whole TABS between windows. A tab swap moves a tab to
MAIN; which tile of that tab is on screen/audible is a separate daemon command (`focus_tile`). The extension can only expand
tiles by a real click through the debugger API (PLAN section 6), so changing the tile takes seconds, may fail ("only selected"),
and is rate limited.

## 2. What the problem actually is

For a mixed tab (e.g. tile 0 NFL, tile 1 football):

1. `_tab_sport` returns ONE of them (first detected, arbitrary), so the tab's cfg (weights, thresholds, build-up values), the
   spoken cue filter (CUE_SPORTS), the soccer recap filter, `is_soccer`/`moves_main` and the post-goal hold all use that sport
   for both tiles. A football goal in a tab whose sport resolved to "american football" gets NFL weights and no build-up tracker;
   NFL commentary in a tab that resolved to football is read with the soccer cue list and `moves_main` is True.
2. The tab stream folds both tiles into one `event_at`/cue/leverage, so a football cue boosts the tab although the NFL tile is
   audible, and the other way round.
3. Audio signals belong to the audible tile, but the cfg/cue interpretation belongs to the tab sport: the pairing is wrong whenever
   the audible tile is not the tab-sport tile.
4. `SportMemory` and `sports` (popup) are per tab: they push one sport onto both tiles.
5. Small: `ScoreWatcher` gets `sports.get(tab) or sport_seen` while the tracker gets the rich `choose_sport` value; the two can
   disagree for the same tile.

Single-tile tabs (everything except DAZN multiview) have one tile, so tile sport = tab sport apart from the `_tab_sport` vs
`choose_sport` difference in 1.1 (a candidate bug: the Scorer's cfg ignores the feed/title/memory sources).

## 3. Designs

All three share a first step that is cheap and safe: ONE source of truth for a tile's sport,
`Server.tile_sport(tab, index)` = the rich `choose_sport` result (trackers' sport) and use it everywhere a tile's sport is needed
(ScoreWatcher, tab_leverage, recorder). It is also the fix for the 1.1 difference. For single-tile tabs it replaces the poorer
`_tab_sport`, so a stream whose sport only came from the feed league/title/memory starts using its overrides: a behaviour change,
small and intended (note it in PLAN).

### Design C (minimal): resolve by the audible tile

* `Server.audible_sport(tab)`: the sport of the tile whose rect is `audible` (sole tile if one rect); if no audible tile or its
  sport is unknown: the popup sport, else the sport of the single known sport among the tiles, else None.
* `Scorer.sport_of` and every spoken-cue site (`_speech_loop`, `_check_buildup` guards, recorder) use `audible_sport(tab)`. Banner
  cues keep using the tile's sport (already do). Build-up tracker: notes already come with tile or audible sport; it needs the
  tab's sport only for `is_soccer`/`moves_main` and cfg: decided by the AUDIBLE tile.
* Add: on an audible-tile change, clear `speech_buffers[tab]`, `buffers[tab]`, and mark tags as stale so the first seconds of the
  other source do not carry over; optionally drop spoken BuildUp notes of the old tile.
* Pros: small (about 40 lines + tests), no change to `Scorer`'s data model, single-tile tabs identical, delivers: spoken cues
  and the audio side are read with the right sport.
* Cons: picture signals of the NON-audible tile are still folded into the tab stream and judged with the audible tile's cfg; a
  football goal in the silent tile gets NFL weights when the NFL tile has the sound. The build-up tracker for football tiles
  that are not audible never has crowd/surge support and no spoken cues (it would only have banner cues and the cue-sum
  threshold). Not "independent".
* Effort: S (1 agent task). Risk: low. Live-flow risk: low; swap decisions between tabs unchanged except cfg choice; arrange and
  tile expand untouched (focus uses no sport).

### Design A: sport per tile everywhere, mixed values by source

* Tile sport everywhere. Tab-level AUDIO values (surge, crowd, speech, music, shout) use the audible tile's sport; PICTURE values
  (event, cue, leverage, replay) use each tile's own sport. Implementation: `_Stream` keeps the SPORT of the signal that set
  `event_at`, `anticipation_*` and `leverage` (`set_event(tab, .., sport=)`, `set_anticipation(.., sport=)`, `set_leverage(.., sport=)`),
  and `components()` takes `w.event` and the event strength with the event's sport cfg, `anticipation_*`, `cue_alone_cap`, `cue_support`,
  `cue_leverage_gain` with the cue's sport, `leverage_weight` with the leverage's sport, audio terms with the audible sport. `decide()`
  and `take_main_block` use the sport of the dominant signal of the challenger (rule needed: the sport that contributed most
  to the score, or the audible tile's).
* Rule when the audible tile changes: new audio terms use the new tile's sport from the moment the extension reports `audible`
  (stale for up to one video_rect report); signals already stored keep the sport they were stored with; clear the speech/classifier
  buffers (as C).
* Pros: each signal is interpreted by the sport that produced it; moderate change in `Scorer` only (about 8 sites); single-tile
  tabs identical (all sports equal).
* Cons: the tab score becomes a mixture of two configs, which is hard to reason about and to tune (a score of 0.6 means different
  things per component); one `event_at`/`anticipation` slot still hides that two tiles interacted ("the later start wins": a
  football cue can be overwritten by an NFL cue); `goal_hold` and `BuildUp` still per tab; the overrides still compete for the tab's
  min_score/threshold (which sport's?). Needs a rule per site, easy to get subtly wrong.
* Effort: M (2 tasks). Risk: medium: touching `components()`, the core of every swap.
  Live-flow risk: medium (a regression changes swaps for everybody), mitigated by "all sports equal" tests.

### Design B: per-tile signal slots inside the tab stream, tab score = best tile

There are two ways to "split a multiview tab into scoring streams":

* B1 true separate streams with ids `(tab, tile)` in `Scorer.streams`: cleanest in theory but `Scorer.streams`, `main`, `Swap`,
  `register`, roles, arrange, `can_main`, `evict_main`, `force_main`, recorder, popup and every test key on an int tab id; a swap
  moves TABS (windows hold tabs, `arrange.py`), not tiles. A tile stream cannot be promoted. Effort L, risk high. NOT recommended.
* B2 (recommended variant): keep `Scorer.streams[tab]` for audio, baseline, can_main, favourite, weight, override, but move
  the picture signals into a small per-tile record `_Stream.tiles[index]` = `{sport, event_at, goal_at, anticipation_*, leverage, replay,
  on_ad, auto_state, replay_seen_at}`. The server calls `set_event(tab, now, strength, goal=, tile=index)` etc. For a single tile
  (or when `tile` is None, as for the feed) the record is a default tile 0 (identical maths).
  `components(tab)` computes a score per tile: picture terms with the tile's own sport cfg; audio terms (surge, crowd, speech,
  music, shout) added ONLY to the audible tile (using the audible tile's sport); non-audible tiles get picture terms only;
  tab score = max over its live tiles (ads: a tab is an ad only when all tiles are; status via any live tile, as today). The winning
  tile's sport is `components()["sport"]`, `["tile"]`, and `decide()` / `take_main_block` / `_check_buildup` use the winning
  (or build-up tile's) sport for thresholds.
  `BuildUp` is keyed `(tab, tile)`: banner cues go to their tile; spoken cues and the audio support to the audible tile only.
  `SpeechMemory` per tile (or per tab filtered by the audible tile at record time). Goal hold per tile; `take_main_block` checks the
  tile that scored on MAIN. `SportMemory` keyed `(tab, tile)`.
* Pros: really independent per sport: an NFL red-zone cue and a football penalty are separate signals with their own weights, holds
  and build-up levels; the tab still competes as one thing for a window, which matches how windows and arrange work; audio is
  attributed to the tile that really produced it; no cross-contamination; the logging/recorder can say which tile and sport made
  a score.
* Cons: largest change to `scoring.py` and `server.py`; a tab's best tile may not be the audible one (the daemon must then get that
  tile focused, which is the existing `focus_tile` path but it only reacts to `event_at` and crowd: it needs to learn about cues /
  build-up of the other tile or a "wanted tile" output); more state to swap in `_expand_swap`; many tests touch the `_Stream` API.
* Effort: L (about 4 agent tasks). Risk: medium-high, because it is the heart of scoring. Live-flow risk: swap decisions between
  tabs would change only when two tiles exist (single tile mathematically identical; to be proven with a golden test); arrange
  untouched (moves tabs); tile expand: the new "wanted tile" output interacts with the 30 s dwell and click failures, and a tab moved
  to MAIN whose best tile is not on screen could show the wrong match until the expand succeeds (existing weakness, but now more
  likely to be hit).

### Comparison

| | C: audible tile | A: mixed by source | B2: per-tile slots |
|---|---|---|---|
| Spoken cues/audio right sport | yes | yes | yes |
| Silent tile's picture signals own sport | no | yes | yes |
| Independent holds / build-up per sport | no | no | yes |
| Tab score easy to explain | yes | no | yes (max over tiles) |
| Effort | S | M | L |
| Risk to swaps of single-tile tabs | low | medium | medium (proved by golden tests) |

### Recommendation

B2, built in two releasable stages: Stage 1 = the shared tile-sport source of truth plus Design C's audible-tile rules and the
audible-change reset (small, safe, fixes the audio/cue side at once and can be used live right away); Stage 2 = the per-tile slots
of B2 for picture signals, then per-tile build-up/holds. Reason: C alone does not meet "each one independently" for the silent
tile, A makes the score a blend that is hard to tune per sport (the whole reason for per-sport settings), and B1 conflicts with
tabs-as-windows. The user decides (questions below).

## 4. Tests that would prove it

Existing tests that must stay green unchanged: `test_server.py`, `test_scoring.py`, `test_sport_overrides.py`, `test_multiview.py`,
`test_leverage.py`, `test_recorder.py`, `test_speech_filter.py`, `test_cues.py`.

Stage 1 (tile sport + audible sport):
1. `tile_sport` returns the `choose_sport` result (feed league beats weak words, popup beats all); `_tab_sport` equals it for a
   single tile tab (also when the sport came from feed/title/memory only: the cfg now resolves).
2. Mixed tab [NFL tile, football tile], football tile audible: spoken text "pénalty sifflé" gives a `penalty awarded` cue with the
   football weight, `moves_main` True, build-up allowed; NFL tile audible: the same text is read with American football cues
   (CUE_SPORTS) and the tracker does not move MAIN.
3. Audible tile switches (rects with `audible` changing): the sport used by the speech/Scorer flips on the next `video_rect`;
   `speech_buffers`/`buffers` cleared; no cue from the old tile's transcript counted after the switch.
4. Single-tile tabs: identical decisions and identical `components()` values as before (golden values on a recorded sequence).
5. Popup sport set on a mixed tab keeps winning for all tiles (documented), and `SportMemory` does not overwrite a tile that has
   its own hard evidence.
6. `_expand_swap` carries any new per-tile sport store.

Stage 2 (per-tile slots):
7. Mixed tab: a football goal on tile 1 and a NFL cue on tile 0 are two independent records (one does not overwrite the other),
   each scored with its own sport's weights; tab score = max; the winning tile and sport are reported.
8. Audio terms apply only to the audible tile: a crowd roar while the NFL tile is audible does not raise the football tile's score.
9. Audible tile switches: audio terms move to the new tile; stored picture signals stay with their tile.
10. Build-up per tile: football tile reaches `buildup_min` with its cues + (if audible) support; NFL tile never moves MAIN; each
    keeps its own cooldown; `moves_main` per tile; `take_main_block` uses the scoring tile's goal hold.
11. Goal hold per tile (a football goal does not hold an NFL cue and the other way round).
12. Single-tile and all-same-sport tabs: golden equality with the old maths (property test: random event/cue sequences give the same
    score before and after).
13. `tab_leverage`: max over tiles each with its own sport; combined into the tile's score.
14. Tile focus: with a "wanted tile" output, a high-scoring silent tile triggers `focus_tile` once, respecting the 30 s dwell and
    the pending-expand state.
15. Recorder rows: per-tile sport and the winning tile are written; `docs/dataset-format.md` updated.
16. Scoring unchanged when `sport` is None/generic everywhere.

## 5. Work split into agent-sized tasks

* T1 (S) DONE 2026-10-13 (branch `per-tile-1`). Tile sport source of truth. Add `Server.tile_sport` / `tile_sports(tab)`; use it for `ScoreWatcher`, `tab_leverage`,
  recorder and `_tab_sport`'s fallback; add `sport_choice_seen`/`replay_signal` etc. to `_expand_swap`. Acceptance: tests 1, 6 pass;
  the full suite passes (815 + new, one expected UCF failure); no behaviour change for tabs whose hard detection already gave the
  sport; PLAN section 0 notes the one intended change.
* T2 (S) DONE 2026-10-13 (branch `per-tile-1`). Audible-tile sport and switch reset (Design C core). `Server.audible_sport(tab)`, `Scorer.sport_of` and `_speech_loop`
  use it; on an audible change clear `speech_buffers`, `buffers`, mark `tags` stale. Acceptance: tests 2, 3, 4, 5 pass; golden
  single-tile sequence identical; a log line "audible tile tab T: tile A -> B (sport X -> Y)" at each change.
  Stage 1 ends here (T1 + T2); deliverable live.
* T3 (M) DONE 2026-10-14 (branch `per-tile-2`). Scorer per-tile picture slots (B2 part 1). `_Stream.tiles`, `set_event/anticipation/leverage/replay/on_ad/auto_state` get an
  optional `tile`, `components()` per tile and max; `decide`, `take_main_block`, `goal_hold_*` use the winning tile; audio on the
  audible tile only. Acceptance: tests 7, 8, 9, 11, 12, 13, 16 pass; `components()` result keeps all existing keys (plus `tile`,
  `sport`); recorder still works.
* T4 (M) DONE 2026-10-14 (branch `per-tile-2`). Server wiring and per-tile build-up/speech (B2 part 2). Pass the tile index from `_update_game_states` and the feed code;
  `BuildUp` and `SpeechMemory` keyed per tile; `_check_buildup` per tile with its own sport; `SportMemory` per tile; spoken cues to
  the audible tile. Acceptance: test 10 passes; the logs say tile and sport for each build-up line; `scripts\backtest.py --summary`
  runs on the new logs (backtest parsing of the changed log lines updated and tested).
* T5 (S-M) DONE 2026-10-14 (branch `per-tile-2`). Tile focus uses the wanted tile. Add a per-tile score output for the focuser, keep the 30 s dwell, the pending expand
  handling and the "only selected" fallback. Acceptance: test 14; existing multiview tests unchanged.
* T6 (S) DONE 2026-10-14 (branch `per-tile-2`; checklist = section 8). Docs and live checklist: PLAN section 0, `daemon/CLAUDE.md` (keys), `README` per-sport text ("mixed tabs"), dataset docs.
  Acceptance: a live script for the user (mixed NFL+football multiview, read the new log lines).

Order: T1, T2 (release), then T3, T4, T5, T6. Each task needs its tests added with it; T3 and T4 must not run in parallel (same files).

## 6. Verified in code versus assumed

VERIFIED (read in this session): everything in 1.2 with a file:line, 1.3, 1.4, `handle_audio` per-tab audio, tab capture and gain
per tab (`offscreen.js`), `audible` per video in `background.js`, `TileFocuser`, `_expand_swap` stores, `BuildUp`/`SpeechMemory`
keyed by tab, `SportMemory` keyed by tab id (`sport_memory.py:24`), `choose_sport` order (`sport_choice.py` header, popup first),
that `_tab_sport` ignores feed/title/weak/memory sources (`server.py:540-543` vs `:635`).

ASSUMED (not checked in code or live): that in a DAZN multiview only the audible tile has sound in the captured tab audio (user
statement and PLAN section 6; not measured); how much the baseline/buffers mix when the tile changes; that `popup.js` offers one
sport selector per tab; that a tab with an NFL tile and a football tile resolves `_tab_sport` to whichever was detected first (follows
from the code, never observed); `extension/` details of `video_rect` timing (how soon after a focus the new `audible` is reported);
the effect sizes of every item listed under "Cons". No test was run in this scoping task.

## 7. Open design questions (for the user; not decided here)

See the final report of the scoping task; they are copied to PLAN.md section 0 when the user answers.

## 8. Stage 2 built: choices taken and live checklist (2026-10-14, branch `per-tile-2`)

Choices the build took without asking (all the safest option; change on request):

* The audible tile is known: the extension marks it in the rects (`audible` = the video is not muted and has volume, `extension/background.js`), `Server._audible_index` takes the first audible one and the Scorer's `audible_of` hands the audio terms (speech, crowd, loudness) and spoken cues to that tile only. Only when no tile is reported audible does every tile hear the tab's audio and a spoken cue go to the tile whose sport is the audible sport, else the first tile (`Server._audible_target`). An audible tile of unknown sport in a multiview counts as football.
* A one-video tab is untouched: its picture signals stay in the stream itself (`tile=None`); a golden test proves equal scores and swaps. Tile records exist only while the tab shows 2+ videos.
* The goal-hold log lines ("post-goal replay hold tab T ...") stay per tab (the longest tile hold); the hold itself is per tile.
* The focus uses the scorer's tile scores only when every tile has one; otherwise event + crowd, as before. The score of the audible tile includes the crowd and loudness, so a silent tile needs a cue or goal clearly above it (1.5x).
* `SportMemory` keys are `"tab:tile"` in a multiview (a restart gives a multiview tile no start guess until the memory of that tile is written).
* Only a soccer tile can move MAIN by the build-up; the other sports' build-up stays off (as before).

Live checklist (mixed NFL + football multiview on one DAZN tab, another tab as MAIN; the daemon must be restarted once):

1. Start the multiview, press `Ctrl+Shift+Y` if the tab is not listed. In `logs\daemon.log` read `sport ... tab T tile N via ...` (each tile must name its own sport) and `audible tile tab T: tile A -> B (sport X -> Y)` when the sound moves.
2. Score lines of the tab (`  2: live ... score=0.31 ... tile=1 sport=football tiles=0:0.12,1:0.31`): `tile` and `sport` name the best tile, `tiles=` the score of each. Check the NFL tile never shows a soccer cue and the soccer tile is read with the football weights.
3. Build-up lines: `buildup would take MAIN tab T tile N (football): ...` only for the soccer tile; none for the NFL tile. `anticipation ... tab T tile N (spoken)` should name the tile that has the sound.
4. Let a soccer chance or VAR happen on the silent soccer tile while the NFL tile has the sound: `focus tile: tab T -> tile N | [...]` (the `score` of each tile is in the line) once, then nothing for 30 s.
5. Move the sound by hand: the log line above must appear and `speech heard` must not carry words of the old commentary afterwards.
6. Single-video tabs: scores and swaps must look as before (no `tile=` text in their score lines).
7. Offline: `.venv\Scripts\python.exe scripts\backtest.py logs --summary` still runs; goals of a mixed tab are matched with the cues, build-up and sport of their own tile.
