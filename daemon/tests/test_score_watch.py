from daemon.score_watch import ScoreWatcher, score_numbers


def read(w, texts, clocks=()):
    return w.update(list(texts), list(clocks))


def test_numbers_exclude_clock_and_split_pairs():
    assert score_numbers(["PSG", "1", "LMFC", "0", "34:10"], ["34:10"]) == (1, 0)
    assert score_numbers(["NAP", "2-0", "FRO", "33:25"], ["33:25"]) == (2, 0)
    assert score_numbers(["INDIANA", "20", "17", "NEBRASKA", "4th", "3:08"], ["3:08"]) == (20, 17)


def test_goal_is_confirmed_on_second_matching_reading():
    w = ScoreWatcher()
    for _ in range(3):
        assert not read(w, ["PSG", "1", "LMFC", "0"])
    assert not read(w, ["PSG", "2", "LMFC", "0"])   # first sighting of the new score
    assert read(w, ["PSG", "2", "LMFC", "0"])        # confirmed


def test_one_noisy_reading_is_not_an_event():
    w = ScoreWatcher()
    for _ in range(3):
        read(w, ["PSG", "1", "LMFC", "0"])
    assert not read(w, ["PSG", "7", "LMFC", "0"])    # misread, then back to normal
    assert not read(w, ["PSG", "1", "LMFC", "0"])
    assert not read(w, ["PSG", "1", "LMFC", "0"])


def test_score_going_down_or_structure_change_is_not_an_event():
    w = ScoreWatcher()
    for _ in range(3):
        read(w, ["PSG", "13", "LMFC", "0"])           # '13' = logo misread as a number
    for _ in range(2):
        result = read(w, ["PSG", "1", "LMFC", "0"])
    assert not result
    for _ in range(2):
        result = read(w, ["PSG", "1"])                 # numbers disappeared (overlay)
    assert not result


def test_big_jump_is_not_a_scoring_play():
    w = ScoreWatcher()
    for _ in range(3):
        read(w, ["A", "1", "B", "0"])
    for _ in range(2):
        result = read(w, ["A", "1", "B", "27"])
    assert not result


def test_american_football_touchdown():
    w = ScoreWatcher()
    for _ in range(3):
        read(w, ["INDIANA", "20", "17", "NEBRASKA"])
    read(w, ["INDIANA", "20", "24", "NEBRASKA"])
    assert read(w, ["INDIANA", "20", "24", "NEBRASKA"])


def test_nothing_read_means_no_event():
    w = ScoreWatcher()
    assert not read(w, [])
    assert not read(w, ["Quitter le mode plein ecran"])


def test_a_lone_number_changing_is_not_a_score():
    w = ScoreWatcher()
    for _ in range(3):
        read(w, ["Da", "BA", "11", "Adrien Rabiot"])
    for _ in range(3):
        result = read(w, ["Da", "BA", "12", "Adrien Rabiot"])
    assert not result
