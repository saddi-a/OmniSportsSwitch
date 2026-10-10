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


# ---- clock jumps: a different game in the slot, or a rewind -------------------------------------------------

def sequence(score, start_sec, n, step=3):
    """n readings of the Napoli scorebug with a clock that advances `step` seconds per reading."""
    out = []
    for i in range(n):
        sec = start_sec + i * step
        clock = f"{sec // 60}:{sec % 60:02d}"
        out.append((["DA", clock.replace(":", "."), "NAP", score, "FRO", "ZN"], [clock]))
    return out


def timed(w, readings, t0, step=3.0):
    """Feed readings with timestamps; returns the indexes at which an event fired."""
    return [i for i, (texts, clocks) in enumerate(readings) if w.update(list(texts), list(clocks), t0 + i * step)]


def test_a_different_game_moving_into_the_slot_is_not_a_goal():
    w = ScoreWatcher()
    assert timed(w, sequence("2-0", 31 * 60, 6), 0) == []
    other_game = [(["CBS", "UCLA", "3", "OREGON", "6", f"12:{i * 3:02d}"], [f"12:{i * 3:02d}"]) for i in range(8)]
    assert timed(w, other_game, 20) == []


def test_after_a_rewind_the_goal_is_detected_once_a_baseline_is_rebuilt():
    w = ScoreWatcher()
    assert timed(w, sequence("2-0", 72 * 60, 6), 0) == []                # watching the end of the game
    replay = sequence("0-0", 27 * 60, 6) + sequence("1-0", 27 * 60 + 18, 6)   # rewound: 0-0, then the goal
    fired = timed(w, replay, 30)
    assert len(fired) == 1


def test_a_lone_kickoff_time_does_not_reset_the_baseline():
    w = ScoreWatcher()
    assert timed(w, sequence("1-0", 31 * 60, 6), 0) == []
    stray = [(["Match started at 20:45", "NAP", "1-0", "FRO"], ["20:45"])]
    assert timed(w, stray, 18) == []                                      # one odd clock reading
    goal = sequence("2-0", 31 * 60 + 21, 6)                               # the real clock carries on
    assert len(timed(w, goal, 21)) == 1


# ---- Maryland at Ohio State: the scores were read as one item, "10/20", between the team names ---------------

def test_merged_scores_between_the_team_names_are_a_score():
    assert find_score(["M", "MARYLAND", "10/20", "OHIO STATE", "41", "2wo", "8.32", "16"], ["8:32"]) == (10, 20)


def test_slash_numbers_elsewhere_are_not_a_score():
    assert find_score(["QB", "10/20", "253 YDS", "JEREMIAH SMITH"], []) is None
    assert find_score(["JEREMIAH SMITH", "10/20", "2 REC"], []) is None      # a name on one side only


def test_a_touchdown_and_extra_point_are_detected_from_the_merged_reading():
    w = ScoreWatcher()
    before = [(["M", "MARYLAND", "10/20", "OHIO STATE", "41", "8.32"], ["8:32"])] * 5
    after = [(["M", "MARYLAND", "10/27", "OHIO STATE", "41", "8.20"], ["8:20"])] * 4
    assert feed(w, before) == []
    assert len(feed(w, after)) == 1


# ---- layouts where the team and its score are one item (seen on a free-licensed Army-Navy mock-up) ------------

def test_team_and_score_in_one_item():
    assert find_score(["ARMY 12", "NAVY 58", "3.29", "3RD & 10"], ["3:29"]) == (12, 58)


def test_labels_with_a_number_are_not_teams():
    assert find_score(["TIMEOUTS 3", "FOULS 4"], []) is None
    assert find_score(["ARMY 12", "TIMEOUTS 3"], []) is None            # only one real team


def test_a_number_before_the_name_is_a_ranking_not_a_score():
    assert find_score(["21 UCLA", "14 OREGON"], []) is None


def test_the_same_team_twice_is_not_a_score():
    assert find_score(["NAVY 58", "NAVY 58"], []) is None


# ---- stat graphics read as goals (found in the live log) ----------------------------------------------------

def boxes(rows):
    """OCR items from (text, height, x0, y0) rows, as logged in logs/boxes.log (width estimated from the text)."""
    return [{"text": t, "x0": x, "y0": y, "x1": x + h * 0.6 * len(t), "y1": y + h} for t, h, x, y in rows]


BOARD = boxes([("HOUSTON", 30, 100, 800), ("31", 60, 330, 790), ("17", 60, 430, 790), ("K-STATE", 30, 520, 800), ("2nd", 30, 700, 800)])
RUSHING = boxes([("RUSHING COMPARISON", 52, 225, 366), ("RUSHES", 20, 583, 427), ("YARDS", 20, 755, 427), ("HOUSTON", 44, 279, 451),
                 ("33", 42, 583, 455), ("187", 42, 749, 455), ("K-STATE", 44, 279, 503), ("19", 40, 587, 507), ("95", 42, 753, 505)])
POSSESSION = boxes([("28:13", 30, 630, 770), ("TIME OF POSSESSION", 30, 820, 770), ("12:59", 30, 1200, 770), ("UCLA", 24, 574, 806),
                    ("10", 100, 716, 804), ("6", 84, 1116, 816), ("0", 90, 1261, 821), ("OREGON", 24, 1272, 806)])
HALFTIME = boxes([("10", 88, 60, 100), ("ohio state", 30, 200, 120), ("37", 80, 500, 100), ("HALFTIME", 30, 700, 110),
                  ("NORTHWESTERN def BALL STATE", 24, 900, 300), ("45-14", 24, 1300, 300)])


def feed_items(w, readings, repeat=1):
    fired = []
    for n, items in enumerate(readings * repeat):
        texts = [it["text"] for it in items]
        if w.update(texts, [t for t in texts if ":" in t and len(t) <= 5], items=items):
            fired.append(n)
    return fired


def test_stat_graphics_do_not_fire_an_event_or_change_the_remembered_score():
    w = ScoreWatcher()
    assert feed_items(w, [BOARD], 5) == []
    assert feed_items(w, [RUSHING] * 5 + [POSSESSION] * 5 + [HALFTIME] * 5) == []
    assert feed_items(w, [BOARD], 4) == []        # back to the real board: still 31-17, nothing happened
    assert w._stable == (31, 17)


# ---- UCLA / Oregon layout: a big play-clock digit between the two scores (live log, 00:43) ---------------------

UCLA_WITHOUT_CLOCK = boxes([("Ucla _", 91, 705, 1150), ("21 UCLA", 32, 718, 1112), ("4-0", 26, 860, 1216), ("17", 112, 957, 1115),
                            ("Ath", 40, 1125, 1199), ("CBS | BIG TIME", 44, 1145, 1125), ("6852", 44, 1257, 1195), ("20", 32, 1396, 1202),
                            ("0", 122, 1698, 1136), ("OREGON", 30, 1714, 1113)])
UCLA_WITH_CLOCK = boxes([("Ucla _", 90, 705, 1151), ("21 UCLA", 30, 718, 1114), ("4-0", 26, 860, 1216), ("17", 114, 957, 1115),
                         ("Ath", 42, 1125, 1197), ("Isr & GOAL", 52, 1140, 1120), ("6849", 44, 1257, 1195), ("17", 32, 1398, 1202),
                         ("6", 120, 1495, 1125), ("0", 126, 1696, 1132), ("OREGON", 30, 1714, 1114)])


def test_a_logo_read_as_a_digit_does_not_replace_the_real_score():
    from daemon.score_watch import find_score_geo
    assert find_score_geo(UCLA_WITH_CLOCK, []) == (17, 6)        # was (6, 0): the "0" is the Oregon logo


def test_the_ucla_scoreboard_flickering_between_readings_fires_nothing():
    w = ScoreWatcher()
    assert feed_items(w, [UCLA_WITH_CLOCK], 4) == []                  # 17-6
    assert feed_items(w, [UCLA_WITHOUT_CLOCK] * 5 + [UCLA_WITH_CLOCK] * 5, 3) == []   # the logo "0" replaces the 6, and back


def test_a_real_touchdown_still_fires_after_the_flicker():
    w = ScoreWatcher()
    feed_items(w, [UCLA_WITH_CLOCK] * 4 + [UCLA_WITHOUT_CLOCK] * 5 + [UCLA_WITH_CLOCK] * 5)
    scored = [dict(it, text="23") if it["text"] == "17" and it["x0"] < 1000 else it for it in UCLA_WITH_CLOCK]   # 23-6
    assert feed_items(w, [scored], 5) != []
