from daemon.score_watch import ScoreWatcher, find_score


def read(w, texts, clocks=()):
    return w.update(list(texts), list(clocks))


def test_finds_scores_in_the_layouts_seen_live():
    assert find_score(["PSG", "1", "LMFC", "0", "34:10"], ["34:10"]) == (1, 0)      # team, score, team, score
    assert find_score(["NAP", "2-0", "FRO", "33:25"], ["33:25"]) == (2, 0)          # "2-0" pair
    assert find_score(["INDIANA", "20", "17", "NEBRASKA", "4th", "3:08"], ["3:08"]) == (20, 17)  # adjacent


def test_numbers_scattered_across_a_graphic_are_not_a_score():
    texts = ["0", "CCBS SPORTS", "PLAYERS TO WATCH", "K", "9", "This Season", "LOSS"]
    assert find_score(texts, []) is None


def test_a_lone_number_is_not_a_score():
    assert find_score(["Da", "BA", "12", "Adrien Rabiot"], []) is None


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


def test_score_going_down_is_not_an_event():
    w = ScoreWatcher()
    for _ in range(3):
        read(w, ["PSG", "13", "LMFC", "0"])           # '13' = logo misread as a number
    for _ in range(2):
        result = read(w, ["PSG", "1", "LMFC", "0"])
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


def test_stat_graphic_appearing_does_not_trigger_an_event():
    w = ScoreWatcher()
    for _ in range(3):
        read(w, ["PSG", "1", "LMFC", "0"])
    graphic = ["0", "CCBS SPORTS", "PLAYERS TO WATCH", "K", "9", "This Season", "LOSS"]
    for _ in range(3):
        assert not read(w, graphic)
