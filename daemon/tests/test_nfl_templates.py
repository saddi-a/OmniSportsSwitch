"""NFL scoreboard templates built from real frames (Prime TNF, centre-panel bugs of CBS / ESPN MNF / NFL Network, NBC SNF, Fox) and the two
American-football profile rules that came with them. Boxes: `nfl_boxes.py` (real OCR output, frames never committed). See docs/scoreboard-profiles.md."""
from daemon.score_watch import find_score_geo
from daemon.scoreboard_profiles import AMERICAN_FOOTBALL, GENERIC
from daemon.scoreboard_templates import (read_centre_panel, read_fox_nfl, read_nbc_snf, read_prime_tnf, read_template)
from daemon.screen_ocr import find_clocks
from daemon.tests import nfl_boxes as B

AF = "american football"


def boxes(rows):
    return [dict(text=t, x0=x0, y0=y0, x1=x1, y1=y1, conf=c) for t, x0, y0, x1, y1, c in rows]


def geo(rows, sport=AF):
    items = boxes(rows)
    return find_score_geo(items, find_clocks([i["text"] for i in items]), sport)


def without(rows, *texts):
    return [r for r in rows if r[0] not in texts]


def only_template(rows):
    hit = read_template(boxes(rows), [], AF)
    return None if hit is None else (hit[0].network, hit[1])


# ---- Prime TNF ---------------------------------------------------------------------------------------------------------------------
def test_prime_tnf_reads_the_crest_only_bug():
    assert read_prime_tnf(boxes(B.TNF_BARE), []) == (24, 10)
    assert read_prime_tnf(boxes(B.TNF_ZERO), []) == (0, 0)        # records 0-4 / 2-2 and the play clock 40 are no scores
    assert geo(B.TNF_BARE) == (24, 10)


def test_prime_tnf_reads_the_replay_banner_variant():
    assert read_prime_tnf(boxes(B.TNF_REPLAY), []) == (14, 10)    # 'THURSDAY NIGHT' banner instead of the TNF logo


def test_prime_tnf_never_guesses():
    assert read_prime_tnf(boxes(B.TNF_NO7), []) is None           # the left 7 was not read: only the 10 is left
    assert geo(B.TNF_NO7) is None
    assert read_prime_tnf(boxes(without(B.TNF_BARE, "TNF")), []) is None                       # no logo, no banner: not recognised
    assert read_prime_tnf(boxes(B.TNF_BARE + [("TNF", 100, 100, 200, 150, 1.0)]), []) is None   # two anchors
    assert read_prime_tnf(boxes(without(B.TNF_BARE, "2nd & 10")), []) is None                  # no down text / quarter between the scores
    low = [(t, x0, y0, x1, y1, 0.5 if t == "10" else c) for t, x0, y0, x1, y1, c in B.TNF_BARE]
    assert read_prime_tnf(boxes(low), []) is None                 # a weak read of a digit


def test_prime_tnf_play_clock_and_records_are_not_scores():
    small = [(t, x0, y0, x1, y1, c) if t != "24" else (t, x0, y0 + 40, x1, y1, c) for t, x0, y0, x1, y1, c in B.TNF_BARE]   # a left number as small as the play clock
    assert read_prime_tnf(boxes(small), []) is None
    assert read_prime_tnf(boxes(without(B.TNF_BARE, "24", "10")), []) is None   # only records and the play clock left


def test_the_tnf_logo_is_not_a_team_name_for_the_profile():
    """'TNF' was taken for a team name: with the big field numbers of the replay it produced (3, 0) for a 0-7 board."""
    assert "tnf" in AMERICAN_FOOTBALL.not_team_words and not GENERIC.not_team_words
    assert geo(B.TNF_JUNK) is None


# ---- centre-panel bugs (CBS, ESPN MNF, NFL Network) ---------------------------------------------------------------------------------
def test_centre_panel_reads_cbs_with_the_corner_ticker_on_screen():
    assert read_centre_panel(boxes(B.CBS_PLAIN), []) == (12, 3)
    assert geo(B.CBS_ATH) == (6, 14)           # down text 'AtH & GOAL'
    assert geo(B.CBS_TICKER_LOWCONF) == (17, 38)   # CIN 28 / MIA 18 ticker top left; the 38 was read with confidence 0.68


def test_centre_panel_without_a_down_text_uses_the_quarter_clock_play_clock_row():
    assert read_centre_panel(boxes(B.CBS_NODOWN), []) == (7, 21)
    assert read_centre_panel(boxes(without(B.CBS_NODOWN, "3RD")), []) is None     # no ordinal: no anchor


def test_centre_panel_reads_espn_mnf_with_a_clock_glued_to_the_quarter():
    assert read_centre_panel(boxes(B.MNF_GLUED), []) == (13, 7)


def test_centre_panel_never_guesses():
    assert read_centre_panel(boxes(B.CBS_NO_RIGHT), []) is None                    # the right score was not read
    assert read_centre_panel(boxes(without(B.CBS_PLAIN, "3")), []) is None
    assert read_centre_panel(boxes(without(B.CBS_PLAIN, "2nD & GOAL")), []) is None   # no anchor left but the ordinal beside a clock? no play clock after it
    ticker = [r for r in B.CBS_TICKER_LOWCONF if r[2] < 200]
    assert read_centre_panel(boxes(ticker), []) is None and geo(ticker) is None   # the ticker alone is not the bug


def test_the_corner_ticker_is_never_the_score_of_an_american_football_board():
    """A stacked CIN 28 / MIA 18 ticker was read as the score when the real digit was missing: American football has no stacked scoreboard."""
    assert not AMERICAN_FOOTBALL.stacked_scores and GENERIC.stacked_scores
    assert geo(B.CBS_NO_RIGHT) is None


def test_centre_panel_does_not_read_other_networks():
    for rows in (B.NBC_BIG, B.FOX_ORD, B.FOX_CHIP, B.TNF_NO7):
        assert read_centre_panel(boxes(rows), []) is None


# ---- NBC SNF -----------------------------------------------------------------------------------------------------------------------
def test_nbc_reads_the_codes_and_scores_around_the_disc():
    assert read_nbc_snf(boxes(B.NBC_BIG), []) == (9, 17)       # field yard numbers 2 / 0 (height 130) sit above the bug
    assert read_nbc_snf(boxes(B.NBC_GOAL), []) == (6, 7)
    assert geo(B.NBC_BIG) == (9, 17)


def test_nbc_never_guesses():
    assert read_nbc_snf(boxes(without(B.NBC_BIG, "7:02")), []) is None            # no clock between the codes
    assert read_nbc_snf(boxes(without(B.NBC_BIG, "9")), []) is None
    assert read_nbc_snf(boxes(without(B.NBC_BIG, "ATL")), []) is None
    for rows in (B.CBS_PLAIN, B.FOX_ORD, B.TNF_BARE):
        assert read_nbc_snf(boxes(rows), []) is None


# ---- Fox ---------------------------------------------------------------------------------------------------------------------------
def test_fox_reads_the_chip_with_a_quarter_under_the_clock():
    assert read_fox_nfl(boxes(B.FOX_ORD), []) == (13, 7)       # clock chip '2:49' over '3RD', stat card bottom left
    assert geo(B.FOX_ORD) == (13, 7)


def test_fox_reads_a_chip_whose_quarter_is_glued_to_the_clock():
    assert read_fox_nfl(boxes(B.FOX_CHIP), []) == (28, 10)     # '7;20' read as one tall item
    assert read_fox_nfl(boxes(B.FOX_CHIP2), []) == (38, 14)


def test_fox_never_guesses():
    assert read_fox_nfl(boxes(B.FOX_MERGED), []) is None                           # '27 W' merged with the crest
    assert read_fox_nfl(boxes(without(B.FOX_ORD, "7")), []) is None
    assert read_fox_nfl(boxes(without(B.FOX_CHIP, "2-2", "0-4")), []) is None      # chip-only form needs a record under the bug
    assert read_fox_nfl(boxes(without(B.FOX_CHIP, "7;20")), []) is None            # no chip between the scores
    assert read_fox_nfl(boxes(B.FOX_ORD + [("9", 1330, 1100, 1400, 1210, 1.0)]), []) is None   # a third tall number right of the chip
    for rows in (B.CBS_PLAIN, B.NBC_BIG, B.TNF_BARE):
        assert read_fox_nfl(boxes(rows), []) is None


# ---- the registry ------------------------------------------------------------------------------------------------------------------
def test_each_real_board_is_read_by_its_own_template():
    assert only_template(B.TNF_BARE) == ("prime", (24, 10))
    assert only_template(B.CBS_PLAIN) == ("centre-panel", (12, 3))
    assert only_template(B.NBC_BIG) == ("nbc", (9, 17))
    assert only_template(B.FOX_ORD) == ("fox", (13, 7))
    assert only_template(B.TNF_NO7) is None and only_template(B.CBS_NO_RIGHT) is None
