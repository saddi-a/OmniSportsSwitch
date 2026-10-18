"""Scoreboard PROFILES: the rules and thresholds of the score reader (`score_watch.find_score_geo`) that belong to one SPORT.

A profile is about the sport (what its scorebugs contain: a play clock and a down text in American football, rankings glued to
college team names...). The look of one network's or league's scoreboard is a TEMPLATE and lives in `scoreboard_templates.py`;
the two are never mixed. Lookup order in `find_score_geo`: a recognised template first, else the profile of the sport, else
`GENERIC`. See docs/scoreboard-profiles.md for how to add a sport.

`GENERIC` is exactly the reader as it was before profiles existed, so a sport with no entry (and every call that passes no sport)
reads as before. `SOCCER` is the same shape with the same values: soccer's handling needed no change.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ScoreboardProfile:
    name: str
    play_clock_vs_panel: float = 1.3     # a number right of the clock / quarter text and at most this much taller than it is the play clock
    glued_rank_names: bool = False       # a ranking read glued to the name ("24KENTUCKY", "18USC") is still a team name (college football)
    trim_overlapping_digits: bool = False   # a number box that starts inside the box of the number before it ("23" + "342") lost its first digit(s) to that neighbour
    down_text_marks_sport: bool = False   # a down-and-distance text ("1ST & 10") on screen proves the sport when none is given
    stacked_scores: bool = True          # two scores stacked in a column are a scoreboard (LaLiga bug); American football has none: a stacked pair is the corner ticker of other games (CBS)
    not_team_words: frozenset = frozenset()   # lowercase words that are a network logo, never a team name ("TNF" beside the centre panel was taken for a team)


GENERIC = ScoreboardProfile("generic")
SOCCER = ScoreboardProfile("soccer")
AMERICAN_FOOTBALL = ScoreboardProfile("american football", glued_rank_names=True, trim_overlapping_digits=True, down_text_marks_sport=True,
                                      stacked_scores=False,
                                      not_team_words=frozenset({"tnf", "snf", "mnf", "nfl", "prime"}))

PROFILES: dict[str, ScoreboardProfile] = {
    "generic": GENERIC,
    "football": SOCCER,                       # same keys as scoring.SPORT_KEYS: "football" is soccer
    "american football": AMERICAN_FOOTBALL,
}


def profile_for(sport) -> ScoreboardProfile:
    """The profile of a sport (key as in `scoring.sport_key`: lowercase, spaces collapsed); unknown or None gives GENERIC."""
    return PROFILES.get(" ".join(str(sport or "").lower().split()), GENERIC)
