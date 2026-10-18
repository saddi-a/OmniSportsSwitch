"""Game clocks of NFL bugs (centre panel, Fox chip, Prime TNF) and the video-player bar, from real OCR boxes of the week 5 replay frames
(`nfl_clock_boxes.py`; the frames are never committed). A wrong clock is worse than none: every rule has a control that stays None."""
import pytest

from daemon.scoreboard_templates import fox_clock, panel_clock, parse_panel_slot
from daemon.screen_ocr import find_clocks
from daemon.tests import nfl_clock_boxes as B


def boxes(rows):
    return [dict(text=t, x0=x0, y0=y0, x1=x1, y1=y1, conf=c) for t, x0, y0, x1, y1, c in rows]


def clocks(rows):
    items = boxes(rows)
    return find_clocks([i["text"] for i in items], items)


# ---- the slot text ------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("text, want", [
    ("5:35", "5:35"), ("8.24", "8:24"), ("5-41", "5:41"), ("13,19", "13:19"), ("10-54", "10:54"),   # a mangled colon
    ("6240", "6:40"), ("8451", "8:51"), ("3810", "3:10"), ("11830", "11:30"),                         # the colon read as a digit
    (":47", "0:47"), ("7:31/40", "7:31"),                                                             # under a minute; play clock after a slash
])
def test_slot_text_is_normalised(text, want):
    assert parse_panel_slot(text) == want


@pytest.mark.parametrize("text", [
    "6871",    # seconds 71
    "1230",    # 1:30 with a mangled colon or 12:30 with a missing one: two clocks, no answer
    "347",     # ':47' with the colon read as a 3, or 3:47: no answer
    "62:40", "16:30", "40", "7", "", "abc",   # minutes past a quarter, a play clock, junk
])
def test_slot_text_that_is_not_one_clock_gives_none(text):
    assert parse_panel_slot(text) is None


def test_a_slot_box_as_tall_as_two_characters_is_not_trusted():
    assert parse_panel_slot("13227", 1.74) is None             # width 1.74 heights for 5 characters: a clock glued to its quarter
    assert parse_panel_slot("11830", 2.46) == "11:30"          # control: the same shape at a single-line width


# ---- centre panel (CBS, ESPN MNF, NFL Network) -----------------------------------------------------------------------------------
def test_panel_clock_ignores_the_corner_ticker():
    assert clocks(B.CBS_TICKER_GARBLED_SLOT) == ["6:40"]    # slot '6240'; the ticker of other games says 11:43
    assert clocks(B.CBS_DOT_CLOCK) == ["9:25"]


def test_panel_with_an_unreadable_slot_gives_no_clock_not_the_tickers():
    assert panel_clock(boxes(B.CBS_TICKER_UNREAD_SLOT)) == (True, None)   # slot '6871' (seconds 71); the ticker says 2:39 of another game
    assert clocks(B.CBS_TICKER_UNREAD_SLOT) == []


def test_panel_other_networks():
    assert clocks(B.NFLN_COLON_AS_DIGIT) == ["8:51"]
    assert clocks(B.ESPN_DASH) == ["8:04"]


def test_a_board_without_a_panel_keeps_the_generic_clock_search():
    items = boxes([("14:35", 100, 50, 190, 80, 0.9), ("Q2", 40, 50, 80, 80, 0.9)])
    assert panel_clock(items) == (False, None)
    assert find_clocks([i["text"] for i in items], items) == ["14:35"]


# ---- Prime TNF ------------------------------------------------------------------------------------------------------------------
def test_tnf_clock_with_the_play_clock_glued_on():
    assert clocks(B.TNF_GLUED_PLAY) == ["5:27"]    # '5:2740' = 5:27 and the play clock 40
    assert clocks(B.TNF_SLASH_PLAY) == ["7:31"]    # '7:31/40'


# ---- Fox chip ---------------------------------------------------------------------------------------------------------------------
def test_fox_chip_clock_read_by_its_place():
    assert fox_clock(boxes(B.FOX_8800)) == "8:00"
    assert fox_clock(boxes(B.FOX_5859)) == "5:59"


def test_fox_chip_that_could_be_two_clocks_or_is_glued_gives_none():
    assert fox_clock(boxes(B.FOX_AMBIGUOUS)) is None    # '247': 2:47 or ':47'
    assert fox_clock(boxes(B.FOX_TALL_GLUED)) is None   # '13227', box as tall as two lines (really 13:17)


# ---- the video player's bar ----------------------------------------------------------------------------------------------------------
def test_player_bar_position_with_the_length_colon_unread_is_not_a_clock():
    assert clocks(B.FOX_AMBIGUOUS) == []    # '3:46 / 2252' (one text)
    assert clocks(B.FOX_5859) == ["5:59"]   # '16.05' beside '2252' (two texts) is dropped; the Fox chip answers
