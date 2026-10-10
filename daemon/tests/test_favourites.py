from daemon.favourites import match_multiplier

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
    assert match_multiplier(["BASKETBALL", "ASTON VILLA"], FAVS) == 1.0
    assert match_multiplier(["AS"], FAVS) == 3


def test_bad_entries_are_ignored():
    assert match_multiplier(["PSG"], [{"name": "", "mult": 5}, {"name": "PSG", "mult": "x"}, {"mult": 2}]) == 1.0
