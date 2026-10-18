# Scoreboard profiles and templates

Two separate ideas in the score reader (`find_score_geo` in `daemon/score_watch.py`):

* A PROFILE is about a SPORT: the rules and thresholds of that sport's scorebugs. Module `daemon/scoreboard_profiles.py`.
* A TEMPLATE is about the LOOK of one network's or league's scoreboard, keyed by (sport, network or league). Module `daemon/scoreboard_templates.py`.

Lookup order: a recognised template reads the board first; if no template recognises it (or one cannot read a safe score) the profile of the sport reads it; no sport (or an unknown one) uses the generic profile. With no sport given, a down text on screen ("1ST & 10") means American football. A wrong score is worse than none, so a template returns None when unsure and never lowers what the profile would read.

## Profiles (what exists)

`GENERIC` is the reader exactly as it was before profiles. `SOCCER` ("football") has the same values: soccer needed no change. `AMERICAN_FOOTBALL` adds:

* `glued_rank_names`: a ranking read glued to the name ("24KENTUCKY", "18USC") counts as a team name (the digits were rejected when the name was not seen as a team).
* `trim_overlapping_digits`: a number whose box starts inside the previous number's box and whose first digits copy that neighbour's last digits ("23" then "342", the real "42" with a copy of the 3 in front) loses those digits. A box that merely overlaps ("103" over "10") is left alone.
* `down_text_marks_sport`: a down-and-distance text proves American football when no sport is passed.
* `not_team_words`: network logos that were taken for a team name (`TNF`, `SNF`, `MNF`, `NFL`, `prime`); with the big field numbers of a replay "TNF" produced a wrong score.
* `stacked_scores = False`: American football has no stacked scoreboard; a stacked pair is the corner ticker of other games (CBS "CIN 28 / MIA 18"), which was read as the score whenever the real digits were missing (9 wrong scores in 23 CBS frames).
* `play_clock_vs_panel`: the play-clock rule (25 / 40 s printed beside the game clock, same size, is not a score) was already in `find_score_geo`; its threshold is now a profile field (1.3 everywhere).

Values live in the profile table in code (like the other scoreboard-reader constants), not in `settings.toml`; the popup does not show them.

## Templates (what exists, all from real data)

* (american football, bein): beIN SPORTS NFL bug, 'PHI| 14 3-1 JAX 7 3RD 9:39 37' (fixtures in `test_samples_1011.py`). The left team's code and score are one item, the right team's code and score follow on the same row; or both are code+score items.
* (american football, espn): ESPN / Disney+ college bug (Kentucky-South Carolina; BYU-TCU and McNeese-LSU as controls). A team record at each end, exactly two score digits between them, much taller than the records, each close to its own record and read with confidence >= 0.8. One digit hidden by the player's controls gives no score.

NFL templates, built from 79 hand-labelled frames of a highlights replay (2325x1308 full-video captures, tab 2290, 2026-10-18; frames git-ignored, boxes in `daemon/tests/nfl_boxes.py`). Each reads ONE tall number on each side of a recognised centre block and returns None on any ambiguity:

* (american football, prime): Prime Video TNF. Crests only, records under the crests. Anchor: exactly one `TNF` text (or, in replays, a `THURSDAY NIGHT` banner) with a down text or quarter between the scores; the score is the one tall number left of the logo and the one right of the panel, same height class, confidence >= 0.8. Play clock (40), records (0-4, 2-2) and field numbers never qualify.
* (american football, centre-panel): CBS, ESPN Monday Night, NFL Network (same structure, one reader). Anchor: one down text ("1st & 10", "AtH & GOAL") with a quarter or clock row under it; or, when the down text was not read, a quarter ordinal + clock + two-digit play clock on one row. Score = the one tall number just left of the panel and the one just right. The corner ticker of other games is far too small and is never near the panel.
* (american football, nbc): NBC SNF, "BAL 9 (disc) 17 ATL". Two team codes of similar height on one row with a clock between; the score is the one number between the left code and the clock and the one between the clock and the right code.
* (american football, fox): Fox bug, floating digits around a small clock chip with the quarter under the clock. Anchor: one ordinal with a short text right above it (the clock text is often garbled, only its place counts), or, when the ordinal was not read / glued to the clock, a pair of same-height numbers with exactly one clock-looking item between them and a team record under the bug.

Measured on the 79 frames (scores right / wrong / none, before -> after): Prime TNF 0/1/5 -> 4/0/2, NFL Network 0/0/6 -> 3/0/3, Fox 7/1/24 -> 18/0/14, CBS 10/9/4 -> 20/0/3, NBC SNF 4/1/0 -> 5/0/0, ESPN MNF 0/0/7 -> 7/0/0. What is still none is a score digit the OCR never read (crest touching the digit, "27 W" merged with the crest, digit hidden by a banner) or a frame without a bug; the NFL Network replay mini-bug (ESPN style, no down text) has no template.

The college bugs of Fox / FS1 / DAZN tiles and the SEC bug have no template: the profile reads them.

## Adding an entry

1. Get REAL data: a committed fixture, or a frame in `tmp/bench` (git-ignored; never commit frames). Print the boxes with `tmp/bench/dbg_final.py` or `dbg_all.py`.
2. A new SPORT: add a `ScoreboardProfile` to `scoreboard_profiles.py` (`PROFILES`, key as in `scoring.SPORT_KEYS`), only the fields that differ from `GENERIC`; if the reader needs a new rule, add a field with the generic value as default and use it in `find_score_geo`.
3. A new NETWORK or LEAGUE: write a `read_<name>(items, clocks)` in `scoreboard_templates.py` that returns `(left, right)` or None from the layout's structure (positions, sizes, records, codes), and register `Template(sport, network, name, read)` in `TEMPLATES`. Be stricter than the generic reader (confidence, distances): an early weak read stops the rescue passes of the pipeline.
4. Tests (see `daemon/tests/test_scoreboard_profiles.py`): the real boxes must give the right score, and for each rule a control that must still give no score (one digit missing, a neighbour that is not a copy, another network's board).
5. Run the benchmark (`tmp/bench/bench.py [--sport nfl,cfb] [--nosport]`): wrong must stay 0.

## Not done / needs real NFL frames

ESPN's own NFL bug (as in the college bug), a Red Zone feed, and the NFL Network replay mini-bug have no templates. The clock side of the NFL frames is untouched (39 of 79 game clocks read): `find_clocks` still misses garbled clocks ("2'27", "14808", "8800"). Soccer has no templates (the generic reader reads all benchmark soccer bugs); add them in the same way when a layout needs structure the generic rules cannot give.

## New network checklist

1. Put 5 or more labelled frames of the network in `tmp/bench/frames_nfl`-style folder (git-ignored) and print the pipeline's boxes for them (`tmp/bench/nfl_run.py` caches the final items, `nfl_show.py` prints them, `nfl_eval.py -v` lists failures).
2. Find the centre block that identifies the layout (logo text, down text, quarter + clock + play clock, team codes) and read the score as the ONE tall number on each side of it; give every tunable number a module constant with the measured range in the comment.
3. Return None for any ambiguity (two candidates, a side missing, weak confidence, anchor not unique). Check that the profile does not then read something wrong (the TNF-as-team and corner-ticker cases were profile problems, not template problems).
4. Paste the real boxes as a fixture, add the real boards, and a control for each rule (digit missing, play clock, record, another network's board).
5. Run the full suite, `nfl_eval.py` (wrong must stay 0) and `bench.py` (28/0/2, clocks 24/30 or better).
