from daemon.sport_detect import BASKETBALL, FOOTBALL, SOCCER, SportDetector


def run(readings):
    d = SportDetector()
    result = None
    for texts, clocks in readings:
        result = d.update(texts, clocks)
    return result


# readings copied from the live daemon log
SOCCER_LIVE = [
    (["PSG", "1", "LMFC", "0", "52.54"], ["52:54"]),
    (["PSG", "1", "LMFC", "0", "52.57"], ["52:57"]),
    (["PSG", "1", "LMFC", "1", "53.34"], ["53:34"]),
]
AMERICAN_LIVE = [
    (["INDIANA", "20", "17", "NEBRASKA", "4th", "3,08 19", "2no & 10"], []),
    (["TEXAS AGM", "2nd & 10", "27 -", "14 Missouri"], []),
    (["HOUSTON", "0", "0", "K-STATE", "1ST 10:45", "3", "1ST & GOAL"], ["10:45"]),
]


def test_soccer_is_recognised_from_a_running_clock_past_twenty_minutes():
    d = SportDetector()
    for i in range(4):                                   # readings about 3 s apart, as in the live log
        sec = 52 * 60 + 54 + 3 * i
        d.update(["PSG", "1", "LMFC", "0"], [f"{sec // 60}:{sec % 60:02d}"])
    assert d.sport == SOCCER


def test_american_football_is_recognised_from_down_and_distance():
    assert run(AMERICAN_LIVE) == FOOTBALL


def test_basketball_is_recognised_from_quarters_and_a_short_countdown_clock():
    readings = [(["LAL", "98", "BOS", "101", "Q4", "FOULS", "2:31"], ["2:31"]),
                (["LAL", "98", "BOS", "101", "Q4", "BONUS", "2:27"], ["2:27"]),
                (["LAL", "100", "BOS", "101", "Q4", "2:20"], ["2:20"])]
    assert run(readings) == BASKETBALL


def test_stoppage_time_means_soccer_even_early_in_the_second_half():
    assert run([(["PSG", "2", "LMFC", "1", "+4", "45+2"], ["45:00"]),
                (["45+3", "PSG"], [])]) == SOCCER


def test_one_noisy_reading_is_not_enough():
    assert run([(["1ST & 10"], [])]) is None


def test_nothing_recognisable_gives_no_answer():
    assert run([(["Quitter le mode plein ecran"], []), (["CBS SPORTS", "DAZN"], [])] * 5) is None


def test_mixed_evidence_without_a_clear_winner_gives_no_answer():
    d = SportDetector()
    d.update(["2nd & 10"], [])      # football +3
    d.update(["Q4"], [])            # basketball +2
    assert d.sport is None


def test_evidence_fades_so_a_new_game_in_the_slot_can_take_over():
    d = SportDetector()
    for _ in range(3):
        d.update(["1ST & 10"], [])
    assert d.sport == FOOTBALL
    for i in range(1500):           # about 75 minutes of readings, the clock running 3 s per reading
        sec = 30 * 60 + 3 * i
        d.update(["PSG", "LMFC"], [f"{sec // 60}:{sec % 60:02d}"])
    assert d.sport == SOCCER


def test_a_static_kickoff_time_is_not_a_soccer_clock():
    d = SportDetector()
    for _ in range(20):
        d.update(["Match started at 20:45", "COMING UP AT HALFTIME", "HOOSIERS RALLY PAST HUSKERS"], ["20:45"])
    assert d.sport is None


def test_a_running_clock_beyond_twenty_minutes_is_soccer():
    d = SportDetector()
    for i in range(4):
        d.update(["PSG", "LMFC"], [f"{50 + (3 * i) // 60}:{(3 * i) % 60 + 10:02d}"])
    assert d.sport == SOCCER


def test_a_lone_ot_or_one_stray_quarter_is_not_basketball():
    d = SportDetector()
    d.update(["OT"], [])
    d.update(["Q4"], [])
    assert d.sport is None
