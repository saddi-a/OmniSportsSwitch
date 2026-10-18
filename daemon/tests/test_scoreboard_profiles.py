"""Scoreboard profiles (per sport) and templates (per network / league): see docs/scoreboard-profiles.md.

The boxes are real OCR output of the benchmark screenshots (text, x0, y0, x1, y1, confidence), kept as plain numbers."""
from daemon.scoreboard_profiles import AMERICAN_FOOTBALL, GENERIC, SOCCER, profile_for
from daemon.scoreboard_templates import TEMPLATES, read_bein_nfl, read_espn_bug, read_template
from daemon.score_watch import ScoreWatcher, _trim_overlapping_digits, find_score_geo
from daemon.screen_ocr import find_clocks

AF = "american football"


def boxes(rows):
    return [dict(text=t, x0=x0, y0=y0, x1=x1, y1=y1, conf=c) for t, x0, y0, x1, y1, c in rows]


def score(rows, sport=None, clocks=None):
    items = boxes(rows)
    return find_score_geo(items, find_clocks([i["text"] for i in items]) if clocks is None else clocks, sport)


# ---- real boards (benchmark frames) ---------------------------------------------------------------------------------------------
# MD / WAS / BYU / ASU / KEN: Maryland 23 Nebraska 42 (FS1 bug), Washington 7 USC 3 (Fox), BYU - TCU (ESPN, the 7 hidden behind the player's
# pause button), Arizona State 10 Texas Tech 33 (DAZN tile), Kentucky 27 South Carolina 27 (ESPN bug).
MD = [
    ('M', 354, 516, 410, 572, 0.99),
    ('MARYLAND', 449, 533, 542, 554, 0.96),
    ('NEBRASKA', 664, 530, 762, 560, 0.97),
    ('N', 783, 509, 873, 580, 0.99),
    ('23', 548, 523, 605, 572, 1.0),
    ('342', 587, 522, 662, 573, 0.87),
    ('2-2', 522, 555, 545, 572, 0.99),
    ('4-0', 663, 555, 689, 573, 0.94),
    ('TERRAPING', 350, 562, 430, 594, 0.89),
    ('Coruuskzrs', 760, 555, 876, 597, 0.46),
    ('Ath', 488, 582, 524, 610, 0.85),
    ('9.53', 534, 582, 580, 612, 0.98),
    ('29', 590, 582, 624, 614, 1.0),
    ('2nD D0WN', 632, 587, 718, 609, 0.78),
]

WAS = [
    ('18USC', 595, 455, 651, 468, 0.99),
    ('W', 225, 449, 265, 485, 0.85),
    ('WASHINGTIN', 276, 454, 372, 482, 0.8),
    ('3-1', 345, 468, 370, 481, 1.0),
    ('7', 400, 452, 423, 479, 1.0),
    ('1:46', 451, 463, 495, 481, 0.83),
    ('3', 520, 452, 544, 480, 1.0),
    ('4-1', 614, 468, 639, 480, 1.0),
    ('5', 691, 445, 727, 489, 0.92),
    ('{39', 473, 479, 497, 493, 0.5),
]

BYU = [
    ('SL', 769, 707, 923, 767, 0.42),
    ('1:51:26', 1295, 753, 1363, 769, 0.99),
    ('10 BYU', 331, 771, 401, 791, 0.43),
    ('Ist & 10', 642, 764, 754, 790, 0.78),
    ('TCU', 1019, 771, 1065, 791, 0.45),
    ('3-0', 435, 801, 467, 819, 0.96),
    ('0', 832, 784, 892, 832, 1.0),
    ('2-2', 929, 799, 963, 819, 0.87),
    ('10', 614, 819, 630, 831, 1.0),
    ('511', 659, 801, 733, 849, 0.75),
    ('Tcu', 991, 795, 1071, 839, 0.43),
    ('(1-0)', 432, 818, 467, 834, 0.95),
    ('10', 765, 818, 783, 834, 0.99),
    ('(0-1)', 929, 818, 964, 834, 0.96),
]

ASU = [
    ('#1', 778, 604, 833, 649, 0.87),
    ('ARIZONA ST', 442, 644, 552, 671, 0.96),
    ('10', 554, 637, 575, 677, 0.9),
    ('103', 560, 634, 638, 691, 0.99),
    ('33', 613, 635, 676, 690, 1.0),
    ('33', 659, 637, 680, 677, 0.9),
    ('TEXASTECH', 682, 645, 790, 671, 0.99),
    ('82', 527, 671, 555, 691, 0.64),
    ('5-0', 677, 671, 705, 691, 0.68),
    ('RED', 829, 677, 869, 700, 0.64),
    ('RATDERS', 805, 686, 881, 722, 0.88),
    ('2nd', 492, 702, 532, 732, 0.55),
    ('1.12', 562, 704, 608, 734, 0.93),
    ('ATh &1', 646, 704, 718, 736, 0.51),
]

KEN = [
    ('24KENTUCKY', 733, 1193, 971, 1220, 1.0),
    ('3RD&2', 1204, 1191, 1349, 1223, 0.96),
    ('SOUTH CAROLINA', 1543, 1189, 1821, 1225, 0.65),
    ('27', 1016, 1206, 1148, 1299, 1.0),
    ('2:08', 1219, 1234, 1325, 1270, 0.98),
    ('3-1', 883, 1241, 937, 1271, 0.99),
    ('27', 1395, 1204, 1533, 1306, 1.0),
    ('2-2', 1611, 1239, 1674, 1272, 0.99),
    ('42,088', 1189, 1227, 1359, 1312, 0.6),
    ('(1-1)', 884, 1271, 935, 1299, 0.92),
    ('(0-2)', 1610, 1272, 1677, 1299, 0.87),
]
# beIN NFL (Eagles-Jaguars, the same boards as test_samples_1011.py; box widths are estimated from the text height)
def eagles(rows):
    return [dict(text=t, x0=x, y0=y, x1=x + 12 * len(t) * h / 40, y1=y + h, conf=1.0) for t, h, x, y in rows]


BEIN = eagles([("2-2", 36, 605, 1165), ("2", 35, 638, 1164), ("PHI| 14", 74, 654, 1144), ("3-1", 33, 1038, 1163), ("JAX", 64, 1081, 1145),
               ("7", 57, 1218, 1149), ("3RD", 56, 1305, 1149), ("9:39", 55, 1421, 1149), ("37", 52, 1603, 1153)])
BEIN_BOTH = eagles([("2-2", 26, 436, 844), ("PHI/ 14", 52, 474, 830), ("3-1", 26, 750, 844), ("JAX] 10", 50, 782, 830), ("3RD", 40, 949, 835),
                    ("7:05", 42, 1029, 833), ("25", 42, 1161, 833)])


# ---- profiles --------------------------------------------------------------------------------------------------------------------
def test_profile_lookup_and_soccer_keeps_the_generic_values():
    assert profile_for("american football") is AMERICAN_FOOTBALL and profile_for("  American   Football ") is AMERICAN_FOOTBALL
    assert profile_for("football") is SOCCER and profile_for(None) is GENERIC and profile_for("tennis") is GENERIC
    assert SOCCER.play_clock_vs_panel == GENERIC.play_clock_vs_panel and not SOCCER.glued_rank_names and not SOCCER.trim_overlapping_digits


def test_a_glued_ranking_is_still_a_team_name_in_american_football_only():
    assert score(WAS, AF) == (7, 3)
    assert score(WAS, "football") is None and score(WAS) is None   # soccer / unknown: unchanged (no score rather than a wrong one)


def test_a_ranking_name_without_scores_gives_nothing():
    rows = [r for r in WAS if r[0] not in ("7", "3")]
    assert score(rows, AF) is None


def test_the_digit_copied_in_front_of_a_score_is_trimmed():
    assert score(MD, AF) == (23, 42)
    assert score(MD, "football") is None


def test_trim_leaves_everything_else_alone():
    lone = boxes([("342", 587, 522, 662, 573, 0.87)])
    assert _trim_overlapping_digits(lone) == lone                                    # no neighbour: untouched
    asu = boxes(ASU)
    assert [i["text"] for i in _trim_overlapping_digits(asu)] == [i["text"] for i in asu]   # "103" over "10": not a copy of its last digit
    assert score(ASU, AF) == (10, 33)                                                 # the real board that a naive trim read as (10, 0)
    assert score(ASU) == (10, 33) and score(ASU, "football") == (10, 33)


def test_down_text_alone_tells_the_sport_when_none_is_given():
    rows = [r for r in WAS] + [("1ST & 10", 640, 452, 700, 480, 0.9)]
    assert score(rows) == (7, 3)         # inferred as American football, glued ranking accepted
    assert score(WAS) is None


# ---- templates -------------------------------------------------------------------------------------------------------------------
def test_template_registry_is_keyed_by_sport_and_network():
    keys = [(t.sport, t.network) for t in TEMPLATES]
    assert ("american football", "bein") in keys and ("american football", "espn") in keys and len(keys) == len(set(keys))


def test_bein_nfl_template_reads_both_layouts():
    assert read_bein_nfl(BEIN, ["9:39"]) == (14, 7)
    assert read_bein_nfl(BEIN_BOTH, ["7:05"]) == (14, 10)
    hit = read_template(BEIN, ["9:39"], AF)
    assert hit is not None and hit[0].network == "bein" and hit[1] == (14, 7)
    assert find_score_geo(BEIN, ["9:39"], AF) == (14, 7)


def test_bein_template_controls():
    no_second = [i for i in BEIN if i["text"] not in ("JAX", "7")]
    assert read_bein_nfl(no_second, ["9:39"]) is None                                   # only one team: never guess
    same = [dict(i, text="PHI") if i["text"] == "JAX" else i for i in BEIN]
    assert read_bein_nfl(same, ["9:39"]) is None                                         # the same code twice
    channel = eagles([("LIVE 3", 60, 100, 100), ("MON", 60, 400, 100), ("5", 55, 600, 100)])
    assert read_bein_nfl(channel, []) is None                                            # "LIVE 3" is a channel label
    assert read_template(BEIN, ["9:39"], "football") is None                             # a template belongs to its sport


def test_espn_template_reads_the_bug_from_its_records_and_digits():
    items = boxes(KEN)
    assert read_espn_bug(items, ["2:08"]) == (27, 27)
    hit = read_template(items, ["2:08"], AF)
    assert hit is not None and hit[0].network == "espn"


def test_espn_template_controls():
    items = boxes(BYU)                                                                    # one digit hidden behind the player's controls
    assert read_espn_bug(items, []) is None
    assert find_score_geo(items, ["1:51:26"], AF) is None
    weak = [dict(i, conf=0.6) if i["text"] == "27" else i for i in boxes(KEN)]
    assert read_espn_bug(weak, []) is None                                                # weak digits: the rescue passes get their turn
    far = [dict(i, x0=i["x0"] + 400, x1=i["x1"] + 400) if i["text"] == "27" and i["x0"] > 1300 else i for i in boxes(KEN)]
    assert read_espn_bug(far, []) is None                                                 # a number far from its record is not a score


def test_the_watcher_passes_the_sport_to_the_reader():
    items = boxes(MD)
    texts = [i["text"] for i in items]
    for sport, expected in ((AF, (23, 42)), ("football", None)):
        w = ScoreWatcher()
        for n in range(5):
            w.update(texts, find_clocks(texts), 3.0 * n, items, sport=sport)
        assert w.score == expected or (expected is None and w.score != (23, 42))
