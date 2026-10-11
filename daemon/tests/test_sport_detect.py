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


# ---- league words on the screen: weak evidence, a separate answer (`weak_sport`), never the hard `sport` --------------------------------
# Header and scorebug of the Eagles-Jaguars tab (daemon.log 17:41): 'NFL' and 'NFL - 5e journee' are on screen from the first frame.
NFL_BREAK = ["JACKSONVILLE JAGUARS / PHILADELPHIA EAGLES", "NFL", "5e journée", "bein spoets", "LIVE", "TOUTES LES CHAINES"]


def weak(readings):
    d = SportDetector()
    for texts in readings:
        d.update(texts, [])
    return d


def test_league_words_give_a_weak_guess_after_a_few_readings():
    assert weak([NFL_BREAK]).weak_sport is None            # one reading is not enough
    d = weak([NFL_BREAK] * 4)
    assert d.weak_sport == FOOTBALL
    assert d.sport is None                                 # the hard answer stays "unknown": no scorebug cue yet


def test_league_words_in_french_english_and_the_other_sports():
    assert weak([["NBA", "bein"]] * 4).weak_sport == BASKETBALL
    assert weak([["Premier League", "2026/27"]] * 4).weak_sport == SOCCER
    assert weak([["LIGUE 1 McDonald's"]] * 4).weak_sport == SOCCER
    assert weak([["Premier Lcague"]] * 4).weak_sport == SOCCER      # OCR mangle seen in the logs
    assert weak([["NCAA FOOTBALL"]] * 4).weak_sport == FOOTBALL
    assert weak([["LIGUE DES CHAMPIONS"]] * 4).weak_sport == SOCCER


def test_words_inside_other_words_do_not_count():
    assert weak([["NFLUENCER", "INFLATION", "BANAL", "DINBA"]] * 6).weak_sport is None
    assert weak([["bein SPORTS 1", "DIRECT"]] * 6).weak_sport is None


def test_two_leagues_at_once_give_no_guess():
    assert weak([["NFL", "NBA"]] * 6).weak_sport is None


def test_a_hard_cue_is_not_overridden_by_league_words():
    d = SportDetector()
    for _ in range(4):
        d.update(["NFL", "bein"], [])
    for i in range(4):
        sec = 52 * 60 + 54 + 3 * i
        d.update(["PSG", "1", "LMFC", "0", "NFL"], [f"{sec // 60}:{sec % 60:02d}"])
    assert d.sport == SOCCER                               # the hard answer is its own; the caller ranks the sources


# ---- the tab title (sent by the extension): whole league words, a title naming two sports gives nothing -------------------------------------------

def test_sport_from_title():
    from daemon.sport_detect import sport_from_title
    assert sport_from_title("JACKSONVILLE JAGUARS / PHILADELPHIA EAGLES - NFL") == FOOTBALL
    assert sport_from_title("NFL - 5e journée") == FOOTBALL
    assert sport_from_title("Liverpool v Manchester City | Premier League | DAZN") == SOCCER
    assert sport_from_title("Ligue 1 : PSG - OM en direct") == SOCCER
    assert sport_from_title("Lakers - Celtics | NBA") == BASKETBALL
    assert sport_from_title("NCAA Football: Ohio State at Penn State") == FOOTBALL


def test_a_title_with_no_league_word_or_with_two_sports_gives_none():
    from daemon.sport_detect import sport_from_title
    for title in ("DAZN", "Prime Video", "", None, "bein SPORTS 1", "NFL and NBA highlights", "INFLATION report"):
        assert sport_from_title(title) is None, title
