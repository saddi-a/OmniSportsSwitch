"""Scoreboards from the screenshots of 2026-10-11 (texts and boxes as the reader returned them; no pictures)."""
from daemon.game_state import GameTracker
from daemon.score_watch import ScoreWatcher, find_score, find_score_card, find_score_geo
from daemon.screen_ocr import find_clocks


def boxes(rows):
    return [{"text": t, "x0": x, "y0": y, "x1": x + h * 0.6 * len(t), "y1": y + h} for t, h, x, y in rows]


def read(items):
    texts = [i["text"] for i in items]
    return texts, find_clocks(texts)


# Texas 7 - 7 Oklahoma, 12:53, 2nd & 10. The first reading missed the left 7 and lost the right one's team row: the records
# "4-0" and "2-2" sit between the names and look like a score ("TEXAS 4-0 ... OKLAHOMA").
TEXAS_FIRST_PASS = boxes([("1 TEXAS", 39, 653, 1220), ("2nd & 10", 38, 1169, 1221), ("OKLAHOMA", 38, 1635, 1221), ("4-0", 32, 818, 1278),
                          ("12853", 42, 1183, 1269), ("40", 36, 1279, 1313), ("7", 88, 1430, 1246), ("=", 36, 1552, 1304),
                          ("22", 36, 1625, 1277), ("(0-1", 30, 1626, 1312)])
TEXAS_ZOOMED = boxes([("1 TEXAS", 39, 653, 1220), ("4-0", 32, 818, 1278), ("7", 88, 1030, 1246), ("2nd & 10", 38, 1169, 1221),
                      ("12853", 42, 1183, 1269), ("7", 88, 1430, 1246), ("OKLAHOMA", 38, 1635, 1221), ("2-2", 36, 1625, 1277),
                      ("(0-1)", 30, 1626, 1312)])


def test_team_records_below_the_names_are_not_the_score():
    assert find_score_geo(TEXAS_FIRST_PASS, find_clocks([i["text"] for i in TEXAS_FIRST_PASS])) is None


def test_the_big_digits_win_over_the_records_when_they_are_read():
    texts, clocks = read(TEXAS_ZOOMED)
    assert find_score_geo(TEXAS_ZOOMED, clocks) == (7, 7)


def test_a_score_pair_on_the_names_own_row_is_still_a_score():
    items = boxes([("NAP", 26, 216, 50), ("0-1", 26, 288, 52), ("FRO", 30, 360, 50)])
    assert find_score_geo(items, []) == (0, 1)


# FRA 0-0 BEL (TF1): the right team name was lost, the left one is read together with the score
def test_a_team_and_its_score_pair_in_one_item():
    texts = ["21.10", "FRA 0-1"]
    assert find_score(texts, find_clocks(texts), anchored_only=True) == (0, 1)
    w = ScoreWatcher()
    items = boxes([("21.10", 26, 74, 46), ("FRA 0-1", 30, 188, 44)])
    for t in (0, 3, 6):
        w.update([i["text"] for i in items], ["21:10"], t, items=items)
    assert w.score == (0, 1)


def test_a_team_code_followed_by_numbers_is_not_a_score_without_a_pair():
    assert find_score(["FRA 12", "CLOCK"], [], anchored_only=True) is None


# SGR 0-0 REN (Europa League): digits as small as the team codes, but right beside them
def test_compact_scorebug_with_a_code_each_side():
    items = boxes([("0:38", 24, 76, 44), ("SGR", 24, 158, 44), ("2", 28, 245, 40), ("0", 28, 290, 40), ("REN", 24, 348, 44)])
    assert find_score_geo(items, ["0:38"]) == (2, 0)


# ATM / RMA (DAZN LaLiga): the two scores are stacked, each beside its own team; the second team is read together with the clock
def test_stacked_scoreboard_with_one_team_per_row():
    items = boxes([("ATM", 24, 122, 40), ("2", 30, 186, 38), ("1", 30, 186, 78)])
    items.insert(2, {"text": "24.47 [RMA", "x0": 60, "y0": 80, "x1": 160, "y1": 106})   # as read from the screenshot
    assert find_score_geo(items, ["24:47"]) == (2, 1)


def test_two_unrelated_numbers_in_a_column_are_not_a_stacked_score():
    items = boxes([("ATM", 24, 122, 40), ("2", 30, 600, 38), ("OTHER", 26, 60, 400), ("1", 30, 600, 78)])
    assert find_score_geo(items, []) is None


# A scorebug with a clock and team codes but no readable digits is a live game, not an ad countdown (3 of 8 screenshots read "break")
def test_clock_with_team_codes_is_live_not_an_ad_countdown():
    for texts in (["0.38", "SGR", "REN", "Feed Your PASSION"], ["ATM", "DA", "ZN", "24.47 RMA", "FROMMADRID IQ RIYADH"], ["21.10", "FRA 0-0"]):
        g = GameTracker("football")
        states = [g.update(t, texts, find_clocks(texts)) for t in (0, 3, 6, 9)]
        assert states == ["live"] * 4, texts
        assert g.on_ad is False


def test_a_lone_countdown_next_to_ad_words_is_still_an_ad():
    g = GameTracker("football")
    texts = ["WATCH", "2:27"]
    assert [g.update(t, texts, ["2:27"]) for t in (0, 3, 6)][-1] == "break"


def test_both_team_codes_and_the_score_in_one_item():
    texts = ["Ue", "21.10", "FRA 2-0 BEL"]
    assert find_score(texts, find_clocks(texts), anchored_only=True) == (2, 0)


# Eagles-Jaguars (2026-10-11 17:42, boxes.log): "PHI| 14" is the code and the score in one item, "JAX" sits between the two
# scores, the play clock "37" is to the right of the game clock. It was read (7, 37) and the real 14 later fired a false touchdown.
EAGLES = boxes([("bein spoRts", 36, 121, 93), ("LONDON", 51, 1747, 1136), ("2-2", 36, 605, 1165), ("2", 35, 638, 1164),
                ("PHI| 14", 74, 654, 1144), ("3-1", 33, 1038, 1163), ("JAX", 64, 1081, 1145), ("7", 57, 1218, 1149),
                ("3RD", 56, 1305, 1149), ("9:39", 55, 1421, 1149), ("37", 52, 1603, 1153), ("UFL", 32, 330, 1172)])


def test_team_code_and_score_in_one_item_with_the_other_team_between_the_scores():
    assert find_score_geo(EAGLES, ["9:39"]) == (14, 7)


# Eagles-Jaguars 17:54:21 (boxes.log): the separator after the team code is read as "/" and "]"; "25" is the play clock after "7:05".
EAGLES_FG = boxes([("DIRECT", 24, 80, 46), ("Hony", 30, 810, 628), ("2-2", 24, 436, 844), ("PHI/ 14", 52, 474, 830), ("3-1", 26, 750, 844),
                   ("JAX] 10", 50, 782, 830), ("3RD", 40, 949, 835), ("7:05", 42, 1029, 833), ("25", 42, 1161, 833), ("LONDON", 42, 1236, 821)])


def test_team_code_separator_read_as_slash_or_bracket():
    assert find_score_geo(EAGLES_FG, ["7:05"]) == (14, 10)


# ---- the full-screen score card shown after a score (Eagles-Jaguars, boxes.log 17:50:39 and 18:00:03) ------------------------------
# No team names are read on it: 'WEEK 5', one item '14 10' (2.5x the height of the labels) and '3RD QTR'. A clock may be beside it.
CARD = boxes([("bein spoRTs", 36, 121, 93), ("WEEK 5", 36, 465, 997), ("14 10", 90, 405, 1065), ("3RD QTR", 36, 403, 1189)])
CARD_CLOCK = boxes([("DIRECI", 28, 114, 66), ("25", 187, 1088, 763), ("WEEK 5", 36, 465, 997), ("14 10", 90, 403, 1065),
                    ("3RD QTR", 36, 403, 1189), ("9,39", 36, 577, 1187)])


def watch(readings, w=None, sport="american football"):
    w = w or ScoreWatcher()
    fired = []
    for n, items in enumerate(readings):
        texts = [it["text"] for it in items]
        if w.update(texts, find_clocks(texts), float(n * 3), items, sport=sport):
            fired.append(n)
    return w, fired


def test_the_score_card_is_read_as_a_score_consistent_with_the_last_scorebug():
    w, fired = watch([EAGLES] * 5)
    assert w._stable == (14, 7) and fired == []
    w, fired = watch([CARD, CARD_CLOCK, CARD, CARD_CLOCK, CARD], w)
    assert w._stable == (14, 10) and len(fired) == 1       # the field goal, found while the scorebug was hidden
    assert w.strength == 0.6                                # +3 = field goal size


def test_a_score_card_with_no_known_score_before_it_is_not_read():
    w, fired = watch([CARD] * 6)
    assert w._stable is None and fired == []


def test_a_card_that_goes_backwards_is_not_a_score():
    w, fired = watch([EAGLES] * 5)
    reversed_card = boxes([("WEEK 5", 36, 465, 997), ("7 14", 90, 405, 1065), ("3RD QTR", 36, 403, 1189)])
    w, fired = watch([reversed_card] * 6, w)
    assert w._stable == (14, 7) and fired == []


def test_a_card_too_far_from_the_last_score_is_not_read():
    w, fired = watch([EAGLES] * 5)
    far = boxes([("WEEK 5", 36, 465, 997), ("45 10", 90, 405, 1065), ("3RD QTR", 36, 403, 1189)])
    w, fired = watch([far] * 6, w)
    assert w._stable == (14, 7) and fired == []


def test_a_stats_table_a_results_ticker_and_a_record_are_not_score_cards():
    w, fired = watch([EAGLES] * 5)
    stats = boxes([("PASSING YARDS", 36, 465, 997), ("232 129", 90, 405, 1065), ("3RD QTR", 36, 403, 1189), ("WEEK 5", 36, 465, 900)])
    ticker = boxes([("SCORES", 36, 100, 1000), ("24 17", 90, 405, 1065), ("FINAL", 36, 403, 1189)])
    record = boxes([("PHILADELPHIA", 36, 100, 1000), ("2-2", 90, 405, 1065), ("3RD QTR", 36, 403, 1189), ("WEEK 5", 36, 465, 997)])
    no_quarter = boxes([("WEEK 5", 36, 465, 997), ("24 17", 90, 405, 1065), ("3:48", 36, 403, 1189)])
    for other in (stats, ticker, record, no_quarter):
        assert find_score_card(other, [], (14, 7)) is None
        w, fired = watch([other] * 6, w)
        assert fired == []


def test_a_normal_scorebug_is_still_read_first():
    w, fired = watch([EAGLES] * 5)
    assert w._stable == (14, 7)



def test_team_code_separator_read_as_a_brace():
    items = [dict(i, text=i["text"].replace("JAX]", "JAX}")) for i in EAGLES_FG]   # 'JAX} 10' at 18:00:00
    assert find_score_geo(items, ["7:05"]) == (14, 10)


# ---- Liverpool-Man City 18:16:20 (boxes.log): a "TOP 3" player list was read as a score ----------------------------------------------------------
# The scorebug digits are not read in this frame ('0' '0' missing); the list has names (Adverse, BARCOLA, Cherki) with small numbers
# in a column. The text-only reader took BARCOLA 5 ... Cherki 2 for "TEAM n TEAM n" and fired (5, 2) at 0-0: false.
TOP3 = boxes([("LIV", 52, 132, 84), ("MCI", 50, 454, 84), ("CANALT", 44, 2041, 69), ("44833", 42, 291, 133), ("TOP 3", 30, 2000, 234),
              ("Adverse", 30, 1984, 290), ("BARCOLA", 30, 1894, 360), ("5", 26, 2179, 361), ("3", 28, 2179, 403), ("Cherki", 30, 1898, 448),
              ("2", 26, 2179, 449), ("Premio Lcague", 44, 2006, 1154)])
SCOREBUG = boxes([("LIV", 50, 132, 84), ("0", 68, 267, 71), ("0", 58, 368, 74), ("MCI", 50, 454, 84), ("CANAL+", 44, 2041, 71), ("44505", 42, 291, 133)])


def test_a_top_3_player_list_is_not_a_score():
    w, fired = watch([SCOREBUG] * 5)
    assert w._stable == (0, 0)
    w, fired = watch([TOP3] * 8, w)
    assert fired == [] and w._stable == (0, 0)


def test_the_stat_list_words_of_that_graphic_are_stat_graphics():
    from daemon.score_watch import STAT_GRAPHIC_RE
    for text in ("TOP 3", "ToP 3", "Match Statistics", "BALLONS SURFACE", "Statistiques du match"):
        assert STAT_GRAPHIC_RE.search(text), text


def test_numbers_in_a_column_with_a_third_number_between_are_not_a_stacked_score():
    items = boxes([("ATM", 24, 122, 40), ("OTHER", 26, 60, 400), ("1", 30, 600, 78), ("2", 30, 600, 40), ("3", 30, 600, 60)])
    texts = [i["text"] for i in items]
    assert find_score_geo(items, []) is None
    w = ScoreWatcher()
    assert w.update(texts, [], 0.0, items) is False


def test_the_column_of_stats_is_rejected_by_its_geometry_alone():
    items = [i for i in TOP3 if i["text"] != "TOP 3"]       # even with no stat word on screen
    w, fired = watch([SCOREBUG] * 5)
    w, fired = watch([items] * 8, w)
    assert fired == [] and w._stable == (0, 0)


# ---- Liverpool-Man City 18:41:23-34 (boxes.log): a per-period shots table with sponsor rows was read as a score ----------------------------------
# Scorebug: LIV 0 ... MCI (the second digit is lost). Table: '5' and '3' on one row (shots in the 1st period), 'Periode', 'TIRS IRE',
# sponsor 'PayPal' items. The text reader took MCI 5 'Periode' 3 for TEAM n TEAM n = (5, 3), and fired at 0-0: a wrong swap.
SHOTS_A = boxes([("LIV", 39, 93, 60), ("MCI", 40, 329, 59), ("[CANAL+", 32, 1478, 52), ("5", 44, 1357, 275), ("Periode", 24, 1426, 294),
                 ("3", 42, 1537, 275), ("4", 36, 1362, 372), ("Periode", 24, 1424, 390), ("Premier League", 31, 1453, 837), ("2020/27", 24, 1526, 862)])
SHOTS_B = boxes([("LIV", 38, 95, 61), ("0", 50, 193, 51), ("MCI", 38, 329, 61), ("CANAL+", 32, 1480, 52), ("52.52", 30, 212, 98),
                 ("PayPa", 26, 196, 262), ("PayPal", 26, 364, 260), ("JayPal", 24, 532, 258), ("Pa Pal", 24, 986, 248), ("PoyPal", 26, 1134, 244),
                 ("5", 44, 1357, 275), ("PeriODE", 26, 1426, 294), ("TiRS IRE", 24, 1430, 274), ("3", 42, 1537, 275), ("4", 34, 1364, 372),
                 ("Periode", 26, 1424, 390), ("Premier Lcague", 31, 1454, 837), ("2026/27", 24, 1526, 862)])
SHOTS_C = boxes([("LIV", 39, 95, 60), ("0", 50, 193, 51), ("MCI", 38, 331, 61), ("CANAL+", 32, 1479, 52), ("5", 42, 1360, 276),
                 ("Periode", 24, 1426, 294), ("TiRS IRE", 24, 1430, 274), ("3", 38, 1538, 276), ("TIRS 2NDE", 24, 1418, 370),
                 ("Periode", 24, 1424, 390), ("Premier Lcague", 31, 1454, 837), ("2026/27", 24, 1526, 862)])


def test_a_shots_per_period_table_is_not_a_score():
    w, fired = watch([SCOREBUG] * 5)
    w, fired = watch([SHOTS_A, SHOTS_B, SHOTS_C] * 4, w, sport="football")
    assert fired == []
    for items in (SHOTS_A, SHOTS_B, SHOTS_C):
        texts = [i["text"] for i in items]
        assert find_score(texts, [], anchored_only=True) in (None, (5, 3), (0, 5))   # the text reader alone is fooled...
        assert ScoreWatcher().update(texts, [], 0.0, items) is False               # ...the watcher with boxes is not
        assert w._stable == (0, 0)


def test_the_text_reader_needs_each_number_beside_a_team_on_its_row():
    from daemon.score_watch import _numbers_read_as_a_score
    assert _numbers_read_as_a_score((5, 3), SHOTS_A) is False        # 5 and 3 share a row but no team name is there
    assert _numbers_read_as_a_score((0, 5), SHOTS_B) is False        # one digit beside LIV, the other one far away
    board = boxes([("LIV", 50, 132, 84), ("2", 68, 267, 71), ("0", 58, 368, 74), ("MCI", 50, 454, 84)])
    assert _numbers_read_as_a_score((2, 0), board) is True
    stacked = boxes([("ATM", 24, 122, 40), ("2", 30, 190, 38), ("RMA", 24, 122, 78), ("1", 30, 190, 76)])
    assert _numbers_read_as_a_score((2, 1), stacked) is True


def test_shot_and_period_words_are_not_team_names():
    from daemon.score_watch import STAT_GRAPHIC_RE
    for text in ("TIRS 2NDE", "TiRS IRE", "Shots on target", "TIRS CADRES"):
        assert STAT_GRAPHIC_RE.search(text), text


# ---- a NEW score read during a replay counts at once (the user's rule, 2026-10-11); a replay of an old score changes nothing ---------------------------
# Eagles-Jaguars 18:51:41 (boxes.log): the Jaguars touchdown (10 -> 16) on the scorebug; the separator after the code was read as 'E'.
TD_BUG = boxes([("DIRECI", 24, 80, 46), ("bein spoets", 28, 88, 66), ("QB", 28, 328, 788), ("2-2", 24, 438, 844), ("PHI| 14", 50, 474, 830),
                ("3-1", 26, 752, 844), ("JAXE 16", 50, 782, 830), ("Ath", 44, 946, 833), ("3:20", 40, 1029, 833), ("08", 40, 1159, 835),
                ("LONDON", 42, 1264, 821)])
OLD_BUG = boxes([("2-2", 24, 438, 844), ("PHI| 14", 50, 474, 830), ("3-1", 26, 752, 844), ("JAX} 10", 50, 782, 830), ("3RD", 44, 946, 833),
                 ("7:05", 40, 1029, 833), ("25", 40, 1161, 835), ("LONDON", 42, 1264, 821)])
SOCCER_0_1 = boxes([("LIV", 50, 132, 84), ("0", 68, 267, 71), ("1", 58, 368, 74), ("MCI", 50, 454, 84)])
SOCCER_0_2 = boxes([("LIV", 50, 132, 84), ("0", 68, 267, 71), ("2", 58, 368, 74), ("MCI", 50, 454, 84)])
SOCCER_1_0 = boxes([("LIV", 50, 132, 84), ("1", 68, 267, 71), ("0", 58, 368, 74), ("MCI", 50, 454, 84)])


def watch_replay(readings, w, replay, sport):
    fired = []
    for n, items in enumerate(readings):
        texts = [it["text"] for it in items]
        if w.update(texts, find_clocks(texts), float(1000 + n * 3), items, replay=replay, sport=sport):
            fired.append(n)
    return fired


def test_the_team_code_separator_read_as_e():
    assert find_score_geo(TD_BUG, ["3:20"]) == (14, 16)


def test_a_new_score_during_a_replay_fires_without_waiting_for_the_replay_to_end():
    w, _ = watch([SOCCER_0_1] * 5, sport="football")
    assert w._stable == (0, 1)
    fired = watch_replay([SOCCER_0_2] * 5, w, replay=True, sport="football")
    assert len(fired) == 1 and fired[0] <= 2          # as soon as the usual 3 readings agree
    assert w._stable == (0, 2)


def test_the_replay_of_a_counted_score_gives_no_second_event():
    w, _ = watch([SOCCER_0_1] * 5, sport="football")
    watch_replay([SOCCER_0_2] * 4, w, replay=True, sport="football")           # counted during the replay
    assert watch_replay([SOCCER_0_2] * 6, w, replay=True, sport="football") == []   # the same replay goes on
    assert watch_replay([SOCCER_0_2] * 6, w, replay=False, sport="football") == []  # and the live scorebug shows it too


def test_a_replay_of_an_old_goal_with_the_old_scoreboard_changes_nothing():
    w, _ = watch([SOCCER_0_2] * 5, sport="football")
    assert watch_replay([SOCCER_0_1] * 60, w, replay=True, sport="football") == []   # minutes of replays of the first goal
    assert w._stable == (0, 2) and w._peak == (0, 2)                           # not even the slow "a lower score that lasts" timer moved
    assert watch_replay([SOCCER_0_2] * 5, w, replay=False, sport="football") == []


def test_a_replay_showing_the_sides_the_other_way_round_is_not_a_new_score():
    w, _ = watch([SOCCER_0_2] * 5, sport="football")
    assert watch_replay([SOCCER_1_0] * 6, w, replay=True, sport="football") == []   # (1, 0): one side up, the other down: not the same ordering


def test_a_replay_with_no_score_known_yet_is_ignored():
    w = ScoreWatcher()
    assert watch_replay([SOCCER_0_2] * 6, w, replay=True, sport="football") == [] and w._stable is None


def test_a_replay_clock_does_not_throw_the_score_away():
    w, _ = watch([SOCCER_0_2] * 5, sport="football")
    jump = boxes([("LIV", 50, 132, 84), ("0", 68, 267, 71), ("1", 58, 368, 74), ("MCI", 50, 454, 84), ("12:34", 30, 300, 130)])
    watch_replay([jump] * 3, w, replay=True, sport="football")                      # a replay of an early moment shows an old clock
    assert w._stable == (0, 2)


def test_american_football_touchdown_and_field_goal_during_a_replay():
    w, _ = watch([OLD_BUG] * 5, sport="american football")          # JAX 10, PHI 14
    assert w._stable == (14, 10)
    fired = watch_replay([TD_BUG] * 5, w, replay=True, sport="american football")      # 18:51:41: JAX 16 (touchdown + miss: +6)
    assert len(fired) == 1 and w._stable == (14, 16) and w.strength == 1.0
    w2, _ = watch([EAGLES] * 5, sport="american football")           # PHI 14, JAX 7
    fired = watch_replay([EAGLES_FG] * 5, w2, replay=True, sport="american football")  # 17:50: JAX 10 (field goal, +3)
    assert len(fired) == 1 and w2._stable == (14, 10) and w2.strength == 0.6
    assert watch_replay([EAGLES_FG] * 8, w2, replay=True, sport="american football") == []


# ---- the NFL play clock is not a score (Eagles-Jaguars 19:13:39-42, boxes.log) ------------------------------------------------------------------------
# The play clock sits right of the game clock, in the same size as the clock text, so it was read as a score ("PHI 17" + '21' = (17, 21),
# JAX '16' + '17' = (16, 17)). Real big digits of a centre-panel scorebug are taller than the panel text (ratio 1.4 to 2.7 on the logs).
PLAY_CLOCK_A = boxes([("DIRECI", 24, 82, 46), ("bein spoeTS", 24, 86, 68), ("5", 198, 323, 43), ("ONY", 48, 962, 205), ("SCORING DRIVE", 30, 380, 786),
                      ("9 PLAYS, 32 YDS, 1.27 TOP", 30, 656, 788), ("2-2", 24, 438, 844), ("PHI/ 17", 50, 474, 830), ("3-1", 24, 750, 844),
                      ("21", 40, 1161, 835), ("LONDON", 42, 1264, 821)])
PLAY_CLOCK_B = boxes([("NEW ORLEANS SAINTS", 24, 6, 30), ("MINNESOTA VIKINGS", 24, 230, 30), ("NFL", 22, 4, 54), ("5e journée", 20, 43, 55),
                      ("bein spoets", 36, 121, 93), ("5", 64, 1111, 665), ("0", 64, 1237, 665), ("SCORING DRIVE", 37, 525, 1087),
                      ("2-2 PHI", 59, 602, 1148), ("17", 64, 772, 1145), ("16", 62, 1202, 1148), ("Ath", 58, 1305, 1149), ("008", 52, 1423, 1151),
                      ("17", 52, 1604, 1153), ("LIVE", 14, 83, 1209)])
BEIN_BUG = boxes([("bein [spoRTS", 38, 122, 92), ("64", 42, 721, 513), ("2-2", 32, 606, 1166), ("PHI| 14", 72, 656, 1142), ("3-1", 36, 1039, 1165),
                  ("JAX} 10", 72, 1082, 1142), ("Ath", 52, 1308, 1152), ("8:15", 54, 1421, 1151), ("05", 56, 1602, 1152), ("Ist & GOAL", 60, 1737, 1147)])
CENTRE_PANEL = boxes([("HOUSTON", 30, 100, 800), ("31", 90, 330, 790), ("2nd", 36, 520, 800), ("10:45", 36, 600, 800), ("17", 90, 760, 790), ("K-STATE", 30, 880, 800)])


def test_the_play_clock_beside_the_game_clock_is_not_a_second_score():
    assert find_score_geo(BEIN_BUG, ["8:15"]) == (14, 10)            # '05' (same height as the clock, right of it) is ignored
    assert find_score_geo(PLAY_CLOCK_B, []) != (16, 17)               # '17' right of 'Ath' '008' is the play clock
    # PLAY_CLOCK_A (19:13:39: the clock and quarter text are hidden under the drive card) is a known gap: nothing to measure the '21' against;
    # one such reading cannot make a baseline (3 of the last 5 readings must agree), see the sequence test below


def test_a_centre_panel_scorebug_keeps_its_right_score():
    # 'HOUSTON 31 [2nd 10:45] 17 K-STATE': the right score is right of the panel but much taller than its text
    assert find_score_geo(CENTRE_PANEL, ["10:45"]) == (31, 17)


# ---- the whole sequence of the last field goal, Eagles-Jaguars 19:09:30-19:14:40 (real boxes of tab 485660207, OCR items only) ----------------------------
# PHI 14 JAX 16 -> a PHI field goal (17-16) at 19:12 -> the drive card and the play clock ('21', '25') read as scores, the game clock jumps to 0:00 ->
# JAX field goal as time runs out (17-19, JAXE 19 on the scorebug for two readings, then the FINAL card). It was never announced.
def _sequence_1914():
    import json
    import pathlib
    path = pathlib.Path(__file__).parent / "fixtures" / "nfl_final_field_goal_1914.json"
    for frame in json.loads(path.read_text(encoding="utf-8")):
        yield frame["t"], [{"text": t, "x0": x, "y0": y, "x1": x + h * 0.55 * len(t), "y1": y + h} for t, h, x, y in frame["items"]]


def test_the_last_field_goal_of_the_game_is_announced():
    w = ScoreWatcher()
    events = []
    for t, items in _sequence_1914():
        texts = [i["text"] for i in items]
        if w.update(texts, find_clocks(texts), t, items, sport="american football"):
            events.append((round(t), w.score, w.strength))
    assert [(score, strength) for _, score, strength in events] == [((17, 16), 0.6), ((17, 19), 0.6)], events
    assert events[-1][0] <= 19 * 3600 + 14 * 60 + 40       # before the FINAL card ends


# ---- a clock jump does not swallow a score change (B) ------------------------------------------------------------------------------------------------

def _board(left, right, clock):
    return boxes([("2-2", 24, 438, 844), (f"PHI| {left}", 50, 474, 830), ("3-1", 26, 752, 844), (f"JAX| {right}", 50, 782, 830), ("3RD", 44, 946, 833),
                  (clock, 60, 1029, 833), ("LONDON", 42, 1264, 821)])


def _run(w, left, right, clock, start, n=4, sport="american football"):
    fired = []
    for i in range(n):
        items = _board(left, right, clock)
        texts = [it["text"] for it in items]
        if w.update(texts, [clock], start + i * 3.0, items, sport=sport):
            fired.append(start + i * 3.0)
    return fired


def _after_a_clock_jump(first_clock="9:39", jump_clock="14:55", sport="american football"):
    w = ScoreWatcher()
    assert _run(w, 17, 16, first_clock, 0.0, 5, sport) == []
    assert w.score == (17, 16)
    _run(w, 17, 16, jump_clock, 20.0, 3, sport)         # two confirmations of the jump
    return w


def test_a_clock_jump_resets_the_baseline_but_the_next_baseline_that_rose_is_an_event():
    w = _after_a_clock_jump()
    assert w.score is None                                 # reset
    fired = _run(w, 17, 19, "14:50", 40.0, 4)              # JAX field goal while the baseline was gone
    assert len(fired) == 1 and w.score == (17, 19) and w.strength == 0.6


def test_a_new_baseline_that_is_lower_equal_or_too_far_is_no_event():
    for left, right in ((17, 13), (14, 16), (17, 16), (17, 27), (30, 16)):    # lower, lower, same, +11, +13
        w = _after_a_clock_jump()
        assert _run(w, left, right, "14:50", 40.0, 4) == [], (left, right)
        assert w.score == (left, right)


def test_a_touchdown_that_happened_across_the_jump_is_a_full_event():
    w = _after_a_clock_jump()
    fired = _run(w, 17, 23, "14:50", 40.0, 4)
    assert len(fired) == 1 and w.strength == 1.0


def test_a_baseline_long_after_the_reset_is_no_event():
    w = _after_a_clock_jump()
    assert _run(w, 17, 19, "10:00", 600.0, 4) == []        # 10 minutes later: too old to say what changed


def test_a_clock_jump_to_the_last_ten_seconds_of_an_nfl_quarter_does_not_reset():
    w = _after_a_clock_jump(jump_clock="0:05")
    assert w.score == (17, 16)                             # kept
    fired = _run(w, 17, 19, "0:03", 40.0, 4)
    assert len(fired) == 1 and w.score == (17, 19)         # and the field goal at 0:03 is an event on the normal path


def test_other_sports_still_reset_on_a_jump_to_0_05():
    w = _after_a_clock_jump(jump_clock="0:05", sport="football")
    assert w.score is None


def test_a_new_baseline_where_both_sides_differ_is_another_game_not_a_goal():
    w = _after_a_clock_jump()
    assert _run(w, 20, 17, "14:50", 40.0, 4) == []         # (17, 16) -> (20, 17): both moved


# real frames for the play clock rule: the compact college bug keeps its right score (7:26 is 29 px, the right '7' is 40: ratio 1.38), and the
# false (14, 25) score change of 18:56:13 (tab 485660207) is gone
USC_PENN = boxes([("Da", 24, 1306, 64), ("ZN", 28, 1308, 86), ("19 USC", 24, 424, 706), ("5-1", 20, 477, 727), ("7", 44, 603, 703), ("7:26", 29, 675, 717),
                  ("7", 40, 785, 705), ("PENN STATE", 26, 862, 704), ("3-2", 20, 863, 727)])
FALSE_1856 = boxes([("DIRECI", 24, 80, 46), ("78", 77, 953, 116), ("GaTC", 34, 306, 235), ("2-2", 24, 438, 844), ("PHI| 14", 50, 476, 830),
                    ("3-1", 26, 750, 842), ("Ath", 41, 947, 834), ("1:59", 42, 1031, 833), ("25", 42, 1161, 833), ("LONDON", 42, 1264, 821)])


def test_a_compact_college_bug_keeps_its_right_score_beside_the_clock():
    assert find_score_geo(USC_PENN, ["7:26"]) == (7, 7)


def test_the_false_score_change_of_18_56_13_is_gone():
    assert find_score_geo(FALSE_1856, ["1:59"]) is None
    w = ScoreWatcher()
    texts = [i["text"] for i in FALSE_1856]
    assert [w.update(texts, ["1:59"], 1000.0 + 3 * n, FALSE_1856, sport="american football") for n in range(8)] == [False] * 8
