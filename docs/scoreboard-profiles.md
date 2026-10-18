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
* `play_clock_vs_panel`: the play-clock rule (25 / 40 s printed beside the game clock, same size, is not a score) was already in `find_score_geo`; its threshold is now a profile field (1.3 everywhere).

Values live in the profile table in code (like the other scoreboard-reader constants), not in `settings.toml`; the popup does not show them.

## Templates (what exists, all from real data)

* (american football, bein): beIN SPORTS NFL bug, 'PHI| 14 3-1 JAX 7 3RD 9:39 37' (fixtures in `test_samples_1011.py`). The left team's code and score are one item, the right team's code and score follow on the same row; or both are code+score items.
* (american football, espn): ESPN / Disney+ college bug (Kentucky-South Carolina; BYU-TCU and McNeese-LSU as controls). A team record at each end, exactly two score digits between them, much taller than the records, each close to its own record and read with confidence >= 0.8. One digit hidden by the player's controls gives no score.

The college bugs of Fox / FS1 / DAZN tiles and the SEC bug have no template: the profile reads them.

## Adding an entry

1. Get REAL data: a committed fixture, or a frame in `tmp/bench` (git-ignored; never commit frames). Print the boxes with `tmp/bench/dbg_final.py` or `dbg_all.py`.
2. A new SPORT: add a `ScoreboardProfile` to `scoreboard_profiles.py` (`PROFILES`, key as in `scoring.SPORT_KEYS`), only the fields that differ from `GENERIC`; if the reader needs a new rule, add a field with the generic value as default and use it in `find_score_geo`.
3. A new NETWORK or LEAGUE: write a `read_<name>(items, clocks)` in `scoreboard_templates.py` that returns `(left, right)` or None from the layout's structure (positions, sizes, records, codes), and register `Template(sport, network, name, read)` in `TEMPLATES`. Be stricter than the generic reader (confidence, distances): an early weak read stops the rescue passes of the pipeline.
4. Tests (see `daemon/tests/test_scoreboard_profiles.py`): the real boxes must give the right score, and for each rule a control that must still give no score (one digit missing, a neighbour that is not a copy, another network's board).
5. Run the benchmark (`tmp/bench/bench.py [--sport nfl,cfb] [--nosport]`): wrong must stay 0.

## Not done / needs real NFL frames

Only the beIN NFL bug exists as NFL data; ESPN's and FOX's own NFL bugs, a 'Sunday Night Football' bug and a Red Zone feed have no frames yet. Soccer has no templates (the generic reader reads all benchmark soccer bugs); add them in the same way when a layout needs structure the generic rules cannot give.
