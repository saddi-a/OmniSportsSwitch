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
