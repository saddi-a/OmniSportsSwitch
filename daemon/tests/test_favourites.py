from daemon.favourites import match_multiplier, match_slots

FAVS = [{"name": "PSG", "mult": 2}, {"name": "Napoli", "mult": 1.5}, {"name": "AS", "mult": 3}]


def test_scoreboard_short_name_matches():
    assert match_multiplier(["PSG", "1", "13", "LMFC", "1"], FAVS) == 2


def test_case_is_ignored_and_names_may_sit_inside_longer_text():
    assert match_multiplier(["napoli vs fro"], FAVS) == 1.5


def test_best_multiplier_wins_when_two_favourites_play():
    assert match_multiplier(["PSG", "Napoli"], FAVS) == 2


def test_no_favourite_means_one():
    assert match_multiplier(["UCLA", "OREGON", "3"], FAVS) == 1.0
    assert match_multiplier([], FAVS) == 1.0


def test_very_short_names_must_be_a_whole_item():
    assert match_multiplier(["HOCKEY", "ASTON VILLA"], FAVS) == 1.0
    assert match_multiplier(["AS"], FAVS) == 3


def test_bad_entries_are_ignored():
    assert match_multiplier(["PSG"], [{"name": "", "mult": 5}, {"name": "PSG", "mult": "x"}, {"mult": 2}]) == 1.0


RULES = [{"name": "PSG", "mult": 1, "slots": ["MAIN", "LEFT"]}, {"name": "Lyon", "mult": 1, "slots": ["LEFT", "RIGHT_BOT"]},
         {"name": "Napoli", "mult": 2}]


def test_team_rule_gives_its_slots():
    assert match_slots(["PSG", "1", "0"], RULES) == {"MAIN", "LEFT"}


def test_no_rule_when_team_is_absent_or_has_no_slots():
    assert match_slots(["UCLA", "OREGON"], RULES) is None
    assert match_slots(["Napoli"], RULES) is None


def test_two_rules_keep_only_slots_both_allow():
    assert match_slots(["PSG", "Lyon"], RULES) == {"LEFT"}


def test_clashing_rules_do_not_leave_nothing():
    teams = [{"name": "PSG", "slots": ["MAIN"]}, {"name": "Lyon", "slots": ["RIGHT_BOT"]}]
    assert match_slots(["PSG", "Lyon"], teams) == {"MAIN"}
