"""Scoreboards from the screenshots of 2026-10-11 (texts and boxes as the reader returned them; no pictures)."""
from daemon.game_state import GameTracker
from daemon.score_watch import ScoreWatcher, find_score, find_score_geo
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
