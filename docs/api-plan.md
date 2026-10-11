# External data API: planning report

Branch `external-api`, written 2026-10-11 by the API-planning session. **Planning and investigation only: no code was written and no behaviour changed.** Everything below marked VERIFIED was fetched or measured today; SECONDHAND means a third-party page (the vendor's own page was unreachable or silent); UNVERIFIED means I could not check it. Prices are as listed today and change often.

## 0. Summary and recommendation

1. **The timing problem has a clean answer: gate by game clock, not by wall time.** Every API event carries a game time (soccer: seconds into the match; American football: period + clock). The daemon already reads the stream's own clock. Release an API event only when the stream's clock has reached the event's clock. This needs no per-stream delay measurement, and it keeps working after a pause or a DVR rewind (the clock goes back, so events are held again). Fallback when the clock is unreadable: a per-stream delay estimate (option 2 in section 2).
2. **One real measurement (today, Como-Roma on DAZN):** ESPN's soccer feed rolled from 84' to 85' between 14:18:30 and 14:18:32; the stream's scorebug (read by OCR) reached 84:00 at 14:18:30-33. So for this stream the feed and the picture were within about 2 s. One game, one minute rollover, no goal: it shows the clock gate is plausible, not that goals are posted as fast. Stage 0 measures this properly.
3. **Best free source: ESPN's public JSON** (`site.api.espn.com`, no key, no account). It covers soccer, NFL, college football and NBA in one format, gives scoring and card events with a clock, and play-by-play for NFL. **Catch (terms): it is unofficial, and Disney's general Terms of Use forbid access "using a robot, spider, script, or other automated means".** Personal low-volume use is a grey area, not a licence. The user must decide (question 1).
4. **Paid, terms-clean alternatives:** API-Football family (about $19 per sport per month, official key), football-data.org (soccer only, EUR 12 to 29 for live data), BallDontLie (NBA/NFL live play-by-play only at $39.99 per sport). Sportradar and SportsDataIO live feeds are business contracts, not for personal use.
5. **Hardware: the saving is real but modest, and the bigger gain is accuracy.** Measured now with one stream and every loop running: GPU use 9-31% (typical 14%), 5.4-5.6 GB of the 10 GB VRAM. The API can cut OCR to a small clock crop every ~10 s and make the commentary listener unnecessary on NFL. It cannot replace audio excitement, replays, ads or the picture state. Estimated GPU duty saving with 4-5 streams: about one half to two thirds of the OCR and speech work (section 4). The API's stronger value is a second source of truth: it reads the scores OCR misses (compact scorebugs, MCI-SUN type cases) and gives exact time-left for leverage.
6. **Recommended plan:** Stage 0 = measure-only recorder (ESPN, no mapping, no effect) for a few game days, then decide the source and the mode with real numbers. Modes: LOCAL (today), BOTH (API events are an extra signal, clock-gated; OCR keeps working), API ("light OCR": clock, team names, ads, state; no score reading, no zoom pass, no digit fallback). Details in section 7.

## 1. Candidate APIs

### 1.1 Comparison

| Source | Sports and leagues | Cost and limits | Live data and speed | Key / terms | Status |
|---|---|---|---|---|---|
| **ESPN public JSON** (`site.api.espn.com/apis/site/v2/sports/...`) | Tested today, all HTTP 200: `football/nfl`, `football/college-football`, `soccer/ita.1`, `soccer/uefa.champions`, `basketball/nba`. Other leagues use the same pattern (`eng.1`, `esp.1`, `fra.1`, national teams): not tested | Free. No published limit. Response header `Cache-Control: max-age=5`, so asking more often than every 5 s is pointless. One request per league returns all its games | Status, clock, period, scores for every game; soccer `details` = goals/cards with the clock in seconds and flags (`redCard`, `penaltyKick`, `ownGoal`); NFL `summary?event=ID` = drives, plays (text, period, clock, running score, `scoringPlay`, UTC `wallclock`) and `scoringPlays`. `playByPlayAvailable` flag on soccer. Lineups, substitutions, VAR in the soccer feed: UNVERIFIED. NFL `situation` (down, distance, red zone): no NFL game was live to check, UNVERIFIED | No key, no account. Undocumented, can change or vanish. **Disney terms (VERIFIED text of disneytermsofuse.com, section 2): personal non-commercial licence, but no access by robot, spider, script or other automated means, and no web scraping.** ESPN-specific terms: not found | VERIFIED (structure, headers); terms read |
| **football-data.org** | Soccer only. Free tier names: Champions League, Premier League, La Liga, Serie A, Ligue 1, Bundesliga, Eredivisie, Primeira Liga, Championship, Brasileirao, World Cup, Euros (12) | Free EUR 0: 10 calls/min, **scores delayed** (length not stated; a third party says about 5 min, UNVERIFIED), no lineups, goals or cards. EUR 12: live scores, 20/min, still no goal/card detail. **EUR 29 "Deep Data": live + lineups, goal scorers, bookings, 30/min.** EUR 49/99/199 add competitions | Live only on paid tiers; no stated latency | Free key by e-mail sign-up. Personal use: not stated on the pricing page | VERIFIED (pricing page) |
| **API-Football (api-sports.io)** and sister products (American football, basketball) | Soccer worldwide; sister APIs for NFL/NCAA and NBA | Free 100 requests/day, 10/min, resets 00:00 UTC. Pro $19/month: 7,500/day, 300/min. Ultra $29 (75,000/day), Mega $39 (150,000/day). All endpoints on every plan. Prepaid, no auto-renewal. One subscription per sport (UNVERIFIED) | Vendor says in-play data updates about every 15 s (SECONDHAND). 100/day is useless for live polling (one game every 15 s = 240 requests/hour) | API key. Personal use terms: not seen | Pricing page seen through a search result (the site itself blocked my fetch); latency SECONDHAND |
| **Sportmonks** | Soccer, 2,300+ leagues | EUR 29/month for any 5 leagues, EUR 99 for 30, EUR 249 for 120; 2,000-3,000 calls per entity per hour; 14-day trial; free plan details unclear | Marketing claims "under 15 s" and per-second livescores, no independent figure | Key. Terms not seen | VERIFIED (pricing page); latency marketing |
| **TheSportsDB** | Soccer, NFL, NBA, MLB, NHL livescore | Premium $9/month; livescore is v2 premium only; 30 requests/min free | **Livescore refreshed every 2 minutes**: slower than any stream, useful only for "which games are live" | Key. Terms not seen | VERIFIED |
| **BallDontLie** | NBA, NFL (and others), sold per sport | Free 5 req/min: teams, players, games only. $9.99 per sport: 60/min. **$39.99 per sport (GOAT): 600/min, live play-by-play (SECONDHAND for the exact endpoint split).** All sports $299.99 | Webhooks advertised "in milliseconds": a marketing claim, no measured figure | Key. Terms not seen | VERIFIED (page), endpoint split SECONDHAND |
| **Highlightly** | Multi-sport | From $7.99/month (7,500/day), free 100/day | Live scores with down and distance (SECONDHAND); no latency figure | Key | SECONDHAND |
| **SportsDataIO** | NFL, NBA, soccer... | Free trial = scrambled fake data. "Discovery Lab" $99-149/month = real data but **delayed one day**. Live feeds need a commercial agreement | Real time only on the commercial contract | Not for personal live use | SECONDHAND |
| **Sportradar** | Everything | B2B contract. Trial: 30 days, 1,000 calls, 1 call/s. NFL game feeds cache 3 s while live, polling "as often as every 3 s" (VERIFIED, their update-frequency page) | The best technically, but business-only | Not for a personal project | VERIFIED docs; terms/price not public |
| **NBA public feed** (`cdn.nba.com/static/json/liveData`) | NBA | Free | My request got **HTTP 403 Access Denied** (CDN block). I did not try to work around it | Unofficial | UNVERIFIED, skip |

Not in the table because they cannot work: nflverse and similar data sets (post-game), and anything that scrapes a broadcaster's page.

### 1.2 What each sport needs, and who can give it

| Need | Soccer | American football | Basketball |
|---|---|---|---|
| Live score, period, clock, status | ESPN (minute-rounded clock, VERIFIED); football-data.org paid | ESPN scoreboard (VERIFIED for finished games; live not seen) | ESPN scoreboard (returns 200) |
| Goals, cards, penalties | ESPN `details` (VERIFIED: goals and cards with seconds); football-data.org EUR 29 | `scoringPlays` and plays (VERIFIED) | not checked |
| VAR, substitutions, lineups | UNVERIFIED on ESPN; football-data.org EUR 29 has lineups | n/a | n/a |
| Play-by-play, down and distance, field position, big plays | n/a | ESPN plays (VERIFIED: text, clock, yards in the text); `situation` UNVERIFIED; BallDontLie GOAT | optional, skip |

### 1.3 Reliability and latency honestly

I could not find an independent figure for any of these feeds' delay behind real time. Vendor claims (15 s, "milliseconds") are marketing. The only measurement is mine: a 2 s clock agreement for ESPN soccer on DAZN today. **The first job of Stage 0 is to produce real numbers per league and per stream** (section 7).

## 2. The timing problem

Streams run 20-60 s behind (DVR rewind: minutes). DAZN's own lag: the only figure I found is an undated, probably old estimate of about 30 s (SECONDHAND); the Como-Roma observation above suggests the DAZN picture and ESPN's clock agree within a couple of seconds. Not generalisable.

### Options

| Option | How | Pros | Cons |
|---|---|---|---|
| **1. Gate by game clock (recommended)** | Keep a timeline of API events with their game clock. Release an event when the stream's OCR clock has reached the event's clock. | No delay estimate. Survives pause and DVR rewind (clock goes back, nothing released early). Same rule for soccer and football. Needs only the clock, a tiny OCR crop | Needs a readable clock (often lost for a few seconds: use the last good clock plus elapsed time while the stream runs; clock jumps already handled by `ScoreWatcher`). Soccer stoppage-time events are not exact: a card shown as `45'+2'` has clock value 2700 s (= 45:00, VERIFIED), so those events can only be gated to the minute (use the `displayValue` text, release at the end of that stoppage minute). Replays show an old clock: ignore events while `tracker.replay` |
| 2. Measured delay per stream | At the first OCR score change, compare with the API time of the same event; delay = difference; then release events after `delay`. | Works when the clock is unreadable | Wrong after any pause, rebuffer or DVR seek (a seek of minutes spoils everything until re-measured). Needs a goal first. Fragile |
| 3. Ranking only | Never use events; use the API for "which games are live", tables, kick-offs, favourites | Zero spoiler risk | Throws away most of the value |

### Which signals are safe early, and which spoil

| Safe to use immediately | Spoils if used before the stream shows it |
|---|---|
| Schedule, kick-off time, "this game is live now", lineups (pre-game), teams, sport, league, standings, the user's favourites | Goals, cards, penalty awards, VAR, red zone and down and distance, touchdowns, plays, final status, **the score (so also leverage/closeness, unless looked up at the stream's clock)**, "half-time"/"full time" (a few seconds early is harmless; minutes is a spoiler) |

Rule for the design: **everything that depends on game progress is looked up at the stream's game clock** (a "time-travel" lookup into the recorded timeline: score at clock T), never at the API's latest value. Safe items are used as they arrive.

## 3. Mapping a tab or tile to an API game

Available inputs, in order of trust: (1) team names OCR reads from the scorebug (already used for favourites; codes like `COM`, `ROM`), (2) the tab's teams field in the popup, (3) the tab title / page text (needs the extension to report it: not reported today, so extension change), (4) a manual pin in the popup.

Method: normalise names (lowercase, strip accents, alias table: Roma/Rome, Como/Côme, PSG/Paris Saint-Germain; French and Spanish forms; 3-letter codes against ESPN's `abbreviation`), then score each live API game by how many of its two teams are found. **A lock needs both teams matched and a consistency check against what OCR sees:** the OCR score and clock must be compatible with the API timeline at that clock (this also rejects a stream that is a replay or an on-demand old game).

Failure modes and handling:

| Failure | Handling |
|---|---|
| Only one team name readable | stay unmapped (local only for that tab) |
| Same team twice in a day, or a rerun/replay of an old game | the consistency check (score at clock) fails; no lock |
| Name in another language / odd abbreviation | alias table grows from logs; unmapped is safe |
| Multiview tile shows another game than the tab's title | map per tile (key `(tab, tile)` like the rest of the daemon); never from the tab title alone |
| Wrong lock | popup shows "mapped to X"; one click to pin or clear; a mismatch seen later (score disagrees for N readings) drops the lock |
| Women's/youth team with a similar name | league must also match when the sport detector knows it |

Bonus: a lock gives sport, both team names and kick-off, which can replace the sport detector and the teams field for that tab.

## 4. What the API can replace locally, and the hardware saving

Measured now (read-only; one stream, Como-Roma, daemon with classifier + OCR + speech + semantic running): GPU utilisation 9-31% over 8 samples (typical 14%); VRAM 5.4-5.6 GB of 10.2 GB. From `daemon.log`: about 24 OCR passes per minute per tile and 8-9 speech transcripts per minute per tab. PLAN.md figures: OCR about 0.2 s per pass, PANNs about 5 ms, speech 0.12-0.9 s per transcript.

Estimate of GPU busy time per minute with 4 streams (arithmetic on those numbers, **not measured with 4 streams**; the user's real setup has 4-5): OCR 4 x 24 x 0.2 = 19 s (about 32%), speech 4 x 9 x ~0.4 = 14 s (about 24%), audio classifier 4 x 60 x 0.005 = 1.2 s (2%). So OCR and speech are where the load is.

| Local job | API replaces it? | Saving |
|---|---|---|
| Score reading (`find_score_geo`, digit fallback, zoom pass, votes) | Yes, for mapped games: the API gives the score; OCR kept only as a cross-check in BOTH mode | Zoom pass and digit fallback (the expensive retries) off; also fixes the unread-score boards |
| Clock and state (live/break/over) | Partly: API status says live / half-time / over, but must be aligned to the stream. **The stream clock is still needed for the gate** | OCR can shrink to a small crop around the clock every ~10 s (from every 2 s per whole tile) |
| Leverage (closeness, time left) | Yes, at the stream clock | Small |
| Anticipation cues (red zone, 4th down, goal to go, big gain, added time) | NFL yes (plays, yards, down text); soccer partly (cards, penalty flag; VAR unverified) | Screen cue code idle |
| Big plays (takeaway, fumble, red card) | Mostly (play text / card events) | Small |
| Speech cues and semantic reading | NFL: yes, the plays say it. Soccer: partly. Both are measure-only today | The speech model could be switched off in API mode: biggest VRAM and GPU relief (model size on the card not measured separately; the main session can measure with `speech_interval_s = 0`) |
| **Audio excitement (crowd roar)** | **No.** It is the core signal and works for anything the API does not list | none |
| Replays, scorebug hidden | No (picture-only; the API cannot know what the broadcast is showing) | none |
| Ads, breaks, placeholders, "what is on the picture" | No | none |
| Sport and team detection | Yes, after a lock | Small |
| Multiview tile focus | No | none |

So **API mode cannot be "no OCR"**: ads, replays, picture state, the clock for the gate and team names for mapping stay local. Realistic result: OCR work down to roughly a third, speech listener optional, classifier unchanged. If the GPU is not actually struggling (1 stream: 14%), the headline benefit is accuracy and a second opinion, not hardware. This is a question for the user (question 4).

## 5. Architecture of the three modes

Setting `[feed] mode = "local" | "both" | "api"` (default `local`; with `local` nothing is imported, no thread starts, no request is sent).

**New pure modules, all taking `now`:** `daemon/feed.py` (provider adapters turn a response into a common model: `GameSnapshot(game_id, league, sport, teams, status, period, clock_s, score, events[])` and `GameEvent(kind, clock_s, team, strength, text)`), `daemon/feed_map.py` (matching, aliases, consistency check, lock/unlock), `daemon/feed_gate.py` (timeline per game, release by stream clock, score-at-clock lookup). Only `server.py` gets one new loop `_feed_loop` that polls in a thread (standard library `urllib`, no new package) every `feed.poll_s` (>= 5 s, one request per league that has a tracked tab, none when nothing is tracked) and hands the result to the gate.

**Where it plugs in (one narrow seam each):**
- Events: released events call the existing `Scorer.set_event(tab, now, strength)` (same path as the OCR watcher), strengths from the existing rules (touchdown 1.0, field goal 0.6, soccer goal 1.0, red card/penalty = `big_play_strength`).
- Leverage: `leverage.py` gets score/period/clock from the gate when mapped, else from `ScoreWatcher` as today.
- Anticipation: gate output feeds `Scorer.set_anticipation` and the same logs.
- Game state: the feed's status is an extra vote in `GameTracker`, never an override of an ad or break the picture shows.
- Config: `[feed]` section in `settings.toml`, mirrored in `Config` and `settings.py LAYOUT` (the existing test keeps them equal); popup shows mode, per-tab "mapped to ..." with Pin/Clear.
- Keys: **never in `settings.toml`, never in `settings.local.json`, never in the popup.** A separate untracked `secrets.local.json` (add it to `.gitignore` in the first commit that creates it) or an environment variable. ESPN needs none.

**BOTH: reconciling.**
- API event and OCR score change within the gate window and agreeing: one event (counted once; dedupe on tab + score pair, like `FIRED_MEMORY_S`). Mark it "confirmed" in the log.
- API event only (OCR missed): fire it. This is the main gain.
- OCR change only (API missing, late, league not covered): fire as today.
- Values disagree (OCR 2-1, API 2-0): log the disagreement once. Events from either source still fire (union), the picture wins for what is on screen, and repeated disagreement (N readings) lowers trust in the mapping, not in the OCR.
- Leverage: API-at-stream-clock if mapped and agreeing, else OCR, else 0 (unknown stays neutral, as today).

**API mode:** the same code with the heavy OCR work switched off for mapped tabs (no score reading, zoom pass, digit fallback; clock crop + team names + ad/break/state checks only). A tab that is unmapped, or whose feed is stale, runs the full local pipeline automatically. So "API only" degrades to LOCAL per tab instead of going blind.

**Failure and network drop:** timeouts of a few seconds; HTTP 429/5xx or JSON errors back off (5 s, 10 s, 30 s, 60 s); a source is "stale" after `feed.stale_s` (30 s) without a good answer; stale = events stop, mapped tabs return to the local pipeline, one warning in `events.log`, and recovery is logged. Events missed during an outage are released late only if their clock is still ahead of the stream's... otherwise dropped (OCR sees them anyway in BOTH).

**Caching:** in memory only: last snapshot per game plus the event timeline (needed for the time-travel lookup). Raw responses are also written to `logs/feed/` (untracked) while measuring, because those become the test fixtures.

**Privacy:** requests carry the user's IP and (for keyed APIs) the key. Poll by league scoreboard rather than by game, so the provider learns which leagues are on, not which stream is on screen. Nothing from the tabs (titles, URLs, audio, screenshots) is sent anywhere. The mapping happens locally. The repo is public: no keys, no recorded full responses committed (fixtures trimmed to team codes, clocks and scores).

## 6. Risks

| Risk | Mitigation |
|---|---|
| ESPN terms / endpoint removal | Provider adapter behind one interface; second provider possible; `local` mode always works; user decides on question 1 |
| Spoiler from a wrong clock gate (OCR clock misread, stream paused) | Gate needs two consistent clock readings; unknown clock = hold; start in shadow mode (log only) |
| Wrong game mapping | Both teams + score consistency; popup shows and can clear it; wrong mapping only affects that tab and is shadow-logged first |
| Double counting events (BOTH) | One dedupe key (tab, score pair) shared with the watcher |
| Feed errors in soccer (late corrections, disallowed goals, a goal retracted) | Corrections after release are logged; a retraction never reverses a swap |
| DST/time zones | Use UTC and game clock only; never local dates |
| Cost drift | No paid provider until Stage 0 numbers justify it |
| Feature creep | Sports in scope only: soccer, NFL, college football, a bit of NBA |

## 7. Staged plan and test strategy

**Stage 0 - measure only (no mapping, no effect on scoring, `[feed] mode = "record"`):** a small recorder polls ESPN scoreboards for the leagues of the day (every 5 s, one request per league), writes normalised snapshots/events with local timestamps to `logs/feed/`. An offline script compares them with `events.log`/`daemon.log` (OCR clocks and score changes) and prints, per stream and event: API first-seen time, stream clock at that moment, implied delay. Answers: how late are goals/cards/plays in the feed, how many seconds is each stream behind, does the clock gate hold. Needs the main session only to restart the daemon once. Deliverable: numbers + recorded fixtures.

**Stage 1 - mapping, still no effect:** match tabs/tiles to games and log `feed map tab X -> game Y (confidence)`; compare with what the user sees.

**Stage 2 - BOTH, shadow:** the gate computes "would fire" events and logs them next to the real OCR events (like the other measure-only signals). Weight 0.

**Stage 3 - BOTH live:** events and leverage on, behind `feed.event_weight`. Anticipation from NFL plays.

**Stage 4 - API mode:** light OCR for mapped tabs, speech listener optional, per-tab fallback.

**Stage 5 - optional second provider** (paid) behind the same adapter, only if Stage 0-3 show a need.

**Tests (every stage):**
- Offline fixtures: trimmed recorded responses (scoreboard JSON; NFL summary JSON) per sport, one game timeline per fixture.
- A fake API server in the tests (`http.server` on localhost serving a scripted sequence of fixture files; the provider base URL is a setting) for polling, backoff, 429, timeouts and stale handling. No real network in the test suite.
- Gate tests with scripted clocks: normal play, clock lost then back, pause, DVR rewind (clock goes back), replay with an old clock.
- Mapping tests with real OCR text from existing fixtures plus alias cases in French/English.
- Replay of saved daemon logs against recorded feed timelines (extend the `scripts/replay_logs.py` approach) to count caught/missed/duplicate events versus today.
- A test that `mode = "local"` imports and starts nothing, and that settings.toml equals the defaults.

## 8. Decisions needed from the user

1. **ESPN's unofficial feed:** use it for Stage 0 despite Disney's "no scripts" clause (personal, one request per league every 5 s)? Alternative: a paid official-key source, about $19 per sport per month for API-Football and its NFL/basketball siblings (soccer-only alternative: football-data.org EUR 29). Recommendation: yes for Stage 0 only, measure, then decide. The risk is that ESPN blocks the IP or changes the feed, not a legal consequence I can quantify.
2. **Timing approach:** clock gate (recommended), measured delay, or ranking only?
3. **What "API only" means:** light OCR with per-tab fallback to local (recommended), or a true "no screen reading" mode (not possible without losing ads/replay/picture state and the clock gate)?
4. **Why do you want it most:** less GPU/VRAM, or better accuracy and leverage? If it is hardware, I can first measure the real load with 4-5 streams and with the speech listener off, which may be cheaper than any API.
5. **Budget** for a paid provider, if any, and for which sports.
6. **DVR/pause behaviour:** accept that after a long rewind API events wait for the clock (nothing lost, just later)?

When you answer, I will turn Stage 0 into a concrete task list for the main session (it owns `server.py` and the restart).

## 9. Decisions taken (user, 2026-10-11)

1. **ESPN is approved for now** (Stage 0 and later), knowing the terms clause in section 1.
2. **The API is wanted EARLY, not delayed.** The point is to be on Main when a goal happens, not after. So timing is a lead, not a hold: an event is released when the stream's clock is `lead_s` seconds BEFORE the event's clock (default 10 s, editable; 0 = release exactly when the stream shows it). Without a stream clock (API-only test mode) it is released `stream_delay - lead_s` seconds after the API reports it (`stream_delay` is a setting, default from Stage 0 numbers). Raw ungated release is rejected: the lead would swing between about 5 s and 50 s per stream. Consequences: a lead event may swap even inside the 20 s dwell (still respecting Make Main, Freeze, ads); events already behind the stream clock (rewind, replay) are dropped; goals, touchdowns, awarded penalties and red cards lead, and the existing cues (red zone, 4th down) can come from the feed too. The audio roar cannot give a lead; only the feed can.
3. **API mode disables all OCR (testing mode).** Main use is BOTH or LOCAL. With no OCR there is no stream clock, no ad/replay/break detection from the picture, and no team-name mapping from the scorebug; the stream delay is manual, mapping comes from the tab's teams field/pin, and unmapped tabs run on audio only. This replaces the "light OCR" idea of section 5 for API mode.
4. **The goal is accuracy**, not hardware. Hardware measurements stay informative only.
5. **No budget**: free sources only (ESPN). No paid provider.
6. **Rewind/pause:** strict clock rule plus dropping events already behind the stream clock.

## 10. Scope answers (user, 2026-10-11)

| Idea | Answer |
|---|---|
| Stakes from standings | Later (after Stage 0) |
| Pre-kickoff arrangement | No |
| API score and time left for leverage when OCR cannot read them | **Yes** (looked up at the stream clock) |
| Multiview tile focus from the feed | **Yes, in scope** (needs per-tile mapping, so it comes after Stage 1) |
| Early half-time / full-time handover | No |
| Disallowed goals and penalty shootouts | Check in Stage 0 only: record and look, no behaviour |

## 11. Stage 0 task list (measure only; nothing here changes scoring or swaps)

Owner: the main session for `server.py` and the restart; the API session can write the pure modules and tests on `external-api`.

1. `daemon/feed.py` (pure): parse an ESPN scoreboard and NFL/college `summary` response into `GameSnapshot` / `GameEvent` (section 5). Fixtures: trimmed recorded responses, no full payloads committed (public repo).
2. `[feed]` in `settings.toml` + `Config` + `settings.py LAYOUT`: `mode = "local"|"record"` (record is Stage 0 only), `poll_s` (5, minimum 5), `leagues` (default `ita.1`, `eng.1`, `esp.1`, `fra.1`, `uefa.champions`, plus `fifa.world` / national-team slugs to be verified, `nfl`, `college-football`, `nba`), `lead_s` (10, unused until Stage 2), `stream_delay_s` (unused). The test that keeps toml equal to defaults must pass.
3. `Server._feed_loop`: standard-library HTTP in a thread, one request per league per `poll_s`, only for leagues that currently have a live or soon-starting game (the scoreboard lists the day's games, so one cheap daily check first); backoff on errors; writes `logs/feed/<date>.jsonl` (snapshots and events with local timestamps) and one line per new event in `events.log` (`feed event ...`). Mode `local`: nothing imported, nothing started.
4. `scripts/feed_compare.py` (offline): compare `logs/feed` with `events.log` / `daemon.log` OCR clocks and score changes; print per event: feed first-seen time, stream clock then, implied lag; the median and spread per league and stream.
5. Record while checking, for the open items: the NFL `situation` field (down, distance, red zone) on a live game; whether VAR, substitutions, disallowed goals and shootout events appear in soccer; which league slugs work for La Liga, Premier League, Ligue 1 and national teams; whether `Cache-Control: max-age=5` really means the data changes no faster.
6. Tests: parsing against fixtures, a fake local server for polling/backoff/429/timeouts, `mode = "local"` starts nothing.
7. After 2-3 game days: report the lag numbers and a recommendation for `lead_s`, then start Stage 1 (mapping) with the user.

## 12. American football situation (end zone) from the feed: measured 2026-10-11 (JAX-PHI, 165 plays)

- The scoreboard `situation` field is EMPTY on every live NFL snapshot (0 of 30). The data is in the `summary` endpoint (already polled every 10 s): every play has `start` and `end` with `down`, `distance`, `yardLine`, `yardsToEndzone`, `downDistanceText` ("1st & Goal at PHI 1"), `shortDownDistanceText` ("1st & Goal"), `possessionText` and `team.id` (the team with the ball). Present on 165 of 165 plays for the numbers, on 155 of 165 for the texts. `wallclock` (UTC, real time of the play) is also there.
- Plays come in two drive lists that overlap (a play can appear twice with the same id) and are not in order: sort by `sequenceNumber`, drop duplicate ids.
- Quirks: timeouts and kickoffs carry `down` -1 or 0 and a copy of the next state; a goal-line start can have `yardsToEndzone` 0 (read as 1); the `end` state of a scrimmage play equals the next play's `start` in only 80 of 103 cases (penalties, touchdowns and change of possession break it), so the **start** state of each play is used, not the end state.
- 36 of 165 plays end within 20 yards of the end zone, 18 are "Goal" situations.
- Code: `feed.parse_situations` -> `Situation(play_id, period, clock_s, down, distance, yards_to_endzone, goal_to_go, offense)`; `FeedGate._end_zone` logs `feed end zone would raise cue: N yds, D & X (...)` in shadow, with the same release rule as events. Measuring only.
