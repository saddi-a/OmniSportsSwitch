from daemon.score_watch import ScoreWatcher, find_score


def feed(w, readings):
    """Feed (texts, clocks) readings; returns the indexes at which an event fired."""
    return [i for i, (texts, clocks) in enumerate(readings) if w.update(list(texts), list(clocks))]


# ---- readings copied from the live daemon log -------------------------------------------------------------

NAPOLI_BEFORE = [
    (["DA", "31.38", "NAP", "1-0", "FRO", "ZN", "o", "o", "TORADO", "DONGFENG"], ["31:38"]),
    (["DA", "31.41", "NAP", "1-0", "FRO", "ZN"], ["31:41"]),
    (["DA", "31.44", "NAP", "1-0", "FRO", "STIRP", "NUOV", "Sanita'/", "ifeng"], ["31:44"]),
    (["DA", "31.47", "NAP", "1-0", "FRO", "ZN"], ["31:47"]),
    (["DA", "31.50", "NAP", "1-0", "FRO", "ZN"], ["31:50"]),
]
NAPOLI_GOAL = [
    (["DA", "31.53", "NAP", "2-0", "FRO", "Zn", "70", "7"], ["31:53"]),
    (["DA", "31-56", "NAP", "2-0", "FRO", "ZN", "3", "70", "MSC", "1-0"], []),   # clock misread with a dash
    (["DA", "31-59", "NAP", "2-0", "FRO", "DI LGF", "19", "70", "MSC", "2-0"], []),
    (["DA", "32.02", "NAP", "2-0", "FRO", "ZN"], ["32:02"]),
]

PSG_BEFORE = [
    (["PSG", "1", "LMFC", "S", "0", "52.54", "A", "30"], ["52:54"]),
    (["PSG", "1", "LMFC", "0", "52.57"], ["52:57"]),
    (["PSG", "13", "LMFC", "0", "53.22"], ["53:22"]),                  # logo read as 13 swallowed the 1
    (["PSG", "1", "13", "LMFC", "0", "53.25"], ["53:25"]),
    (["BUT", "LEMANSFC", "1+3", "53.28"], ["53:28"]),                  # no usable score at all
]
PSG_GOAL = [
    (["PSG", "1", "13", "LMFC", "1", "53:31", "Cf"], ["53:31"]),
    (["PSG", "1", "LMFC", "1", "53.34", "Fifa"], ["53:34"]),
    (["PSG", "1", "13", "LMFC", "1", "53:37", "PRIVEE"], ["53:37"]),
    (["PSG", "1", "13", "LMFC", "1", "53.40", "4", "e"], ["53:40"]),
]


def test_napoli_goal_from_the_live_log_is_detected_within_three_readings():
    w = ScoreWatcher()
    assert feed(w, NAPOLI_BEFORE) == []
    fired = feed(w, NAPOLI_GOAL)
    assert fired and fired[0] <= 2          # not 12 s later, as before the fix
    assert len(fired) == 1                  # and only once


def test_psg_equaliser_from_the_live_log_is_detected_despite_the_channel_logo():
    w = ScoreWatcher()
    assert feed(w, PSG_BEFORE) == []
    fired = feed(w, PSG_GOAL)
    assert len(fired) == 1


def test_find_score_handles_the_layouts_seen_live():
    assert find_score(["PSG", "1", "LMFC", "0", "34:10"], ["34:10"]) == (1, 0)
    assert find_score(["PSG", "1", "13", "LMFC", "1"], []) == (1, 1)                # logo between the numbers
    assert find_score(["DA", "31-56", "NAP", "2-0", "FRO"], []) == (2, 0)           # dash clock is not a score
    assert find_score(["INDIANA", "20", "17", "NEBRASKA", "4th", "3:08"], ["3:08"]) == (20, 17)


def test_numbers_scattered_across_a_graphic_are_not_a_score():
    texts = ["0", "CCBS SPORTS", "PLAYERS TO WATCH", "K", "9", "This Season", "LOSS"]
    assert find_score(texts, []) is None


def test_a_lone_number_is_not_a_score():
    assert find_score(["Da", "BA", "12", "Adrien Rabiot"], []) is None


def test_one_or_two_bad_readings_do_not_make_a_goal():
    w = ScoreWatcher()
    feed(w, [(["PSG", "1", "LMFC", "0"], [])] * 5)
    bad = [(["PSG", "7", "LMFC", "0"], []), (["PSG", "7", "LMFC", "0"], [])]
    assert feed(w, bad) == []
    assert feed(w, [(["PSG", "1", "LMFC", "0"], [])] * 3) == []


def test_score_going_down_is_not_an_event():
    w = ScoreWatcher()
    feed(w, [(["PSG", "13", "LMFC", "0"], [])] * 5)               # logo noise as the baseline
    assert feed(w, [(["PSG", "1", "LMFC", "0"], [])] * 5) == []


def test_big_jump_is_not_a_scoring_play():
    w = ScoreWatcher()
    feed(w, [(["A", "1", "B", "0"], [])] * 5)
    assert feed(w, [(["A", "1", "B", "27"], [])] * 5) == []


def test_american_football_touchdown():
    w = ScoreWatcher()
    feed(w, [(["INDIANA", "20", "17", "NEBRASKA"], [])] * 5)
    assert len(feed(w, [(["INDIANA", "20", "24", "NEBRASKA"], [])] * 4)) == 1


def test_stat_graphic_appearing_does_not_trigger_an_event():
    w = ScoreWatcher()
    feed(w, [(["PSG", "1", "LMFC", "0"], [])] * 5)
    graphic = ["0", "CCBS SPORTS", "PLAYERS TO WATCH", "K", "9", "This Season", "LOSS"]
    assert feed(w, [(graphic, [])] * 5) == []


def test_nothing_read_means_no_event():
    w = ScoreWatcher()
    assert feed(w, [([], []), (["Quitter le mode plein ecran"], [])]) == []
