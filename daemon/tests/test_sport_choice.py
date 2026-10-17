import itertools

from daemon.sport_choice import SOURCES, choose_sport, sport_from_feed
from daemon.sport_detect import BASKETBALL, FOOTBALL, SOCCER

# the user's order: 1 popup, 2 hard scorebug cue, 3 feed league, 4 tab title, 5 league words on screen, 6 remembered sport


def test_the_order_of_the_sources():
    assert SOURCES == ("popup", "hard", "feed", "title", "weak", "memory")


def test_each_source_alone_is_used():
    for name in SOURCES:
        assert choose_sport(**{name: FOOTBALL}) == (FOOTBALL, name)


def test_nothing_known_is_unknown():
    assert choose_sport() == (None, None)


def test_a_higher_source_always_replaces_a_lower_one():
    sports = [FOOTBALL, SOCCER, BASKETBALL]
    for i, high in enumerate(SOURCES):
        for low in SOURCES[i + 1:]:
            for a, b in itertools.permutations(sports, 2):
                assert choose_sport(**{high: a, low: b}) == (a, high), (high, low)


def test_six_different_answers_the_popup_wins_then_dropping_the_top_one_each_time():
    answers = {"popup": BASKETBALL, "hard": FOOTBALL, "feed": SOCCER, "title": BASKETBALL, "weak": FOOTBALL, "memory": SOCCER}
    remaining = dict(answers)
    for name in SOURCES:
        assert choose_sport(**remaining) == (answers[name], name)
        del remaining[name]


def test_empty_values_do_not_count():
    assert choose_sport(popup="", hard=None, feed="", memory=SOCCER) == (SOCCER, "memory")
    assert choose_sport(popup="Auto", memory=SOCCER) == (SOCCER, "memory")        # the popup's "automatic"
    assert choose_sport(popup="Chess", hard="Chess", weak=FOOTBALL) == (FOOTBALL, "weak")   # an unknown name is ignored


def test_the_feed_sport_names_map_to_ours():
    assert sport_from_feed("soccer") == SOCCER
    assert sport_from_feed("american football") == FOOTBALL
    assert sport_from_feed("basketball") == BASKETBALL
    assert sport_from_feed("hockey") is None and sport_from_feed(None) is None and sport_from_feed("") is None


def test_the_names_are_compared_without_case():
    assert choose_sport(popup="american football") == (FOOTBALL, "popup")
    assert choose_sport(hard="BASKETBALL") == (BASKETBALL, "hard")
