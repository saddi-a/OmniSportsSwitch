"""The gap pass: a scorebug with the team codes at both ends and the scores between them (CANAL+ Premier League:
"LIV 0 [lion logo] 1 MCI"), where the normal reading misses the thin white right digit. Boxes are the real ones from the
Liverpool - Man City frames of 2026-10-11 (18:56)."""
from daemon.screen_ocr import find_name_pair, gap_score_items, merge_zoom, find_clocks
from daemon.score_watch import find_score_geo


def item(text, x0, y0, x1, y1, conf=0.9):
    return {"text": text, "conf": conf, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


LIV, MCI = item("LIV", 132, 86, 222, 134), item("MCI", 454, 84, 560, 134)
FRAME = [LIV, item("0", 268, 70, 322, 138), MCI, item("CANAL+", 2041, 71, 2215, 115)]   # the right digit was not read


def test_two_team_codes_on_one_row_are_found():
    assert find_name_pair(FRAME) == (LIV, MCI)


def test_a_banner_word_or_a_far_away_word_is_not_a_team_code():
    flag = item("FLAG", 454, 84, 560, 134)
    assert find_name_pair([LIV, flag]) is None
    far = item("MCI", 2000, 84, 2100, 134)
    assert find_name_pair([LIV, far]) is None                       # too far: not the two ends of one scorebug
    below = item("MCI", 454, 400, 560, 450)
    assert find_name_pair([LIV, below]) is None                     # not on the same row


def test_three_digits_are_left_logo_right():
    left, right = gap_score_items(LIV, MCI, "021", 0.6)
    assert (left["text"], right["text"]) == ("0", "1")
    left, right = gap_score_items(LIV, MCI, "080", 0.6)             # the logo read as an 8
    assert (left["text"], right["text"]) == ("0", "0")


def test_untrusted_readings_give_nothing():
    assert gap_score_items(LIV, MCI, "02", 0.6) is None             # two digits across a wide gap: a logo is in there
    assert gap_score_items(LIV, MCI, "1020", 0.6) is None           # four digits
    assert gap_score_items(LIV, MCI, "021", 0.1) is None            # not sure enough
    assert gap_score_items(LIV, MCI, "0a1", 0.9) is None


def test_two_digits_count_only_when_the_gap_has_no_room_for_a_logo():
    a, b = item("LIV", 100, 80, 170, 130), item("MCI", 260, 80, 330, 130)   # gap 90 px = 1.8 heights
    left, right = gap_score_items(a, b, "21", 0.8)
    assert (left["text"], right["text"]) == ("2", "1")


def test_the_gap_reading_completes_the_score_for_the_geometry_reader():
    assert find_score_geo(FRAME, find_clocks([i["text"] for i in FRAME])) is None
    made = gap_score_items(LIV, MCI, "021", 0.6)
    merged = merge_zoom(FRAME, (222, 60, 454, 160), made)           # replaces the stray left digit inside the strip
    assert find_score_geo(merged, find_clocks([i["text"] for i in merged])) == (0, 1)
    made = gap_score_items(LIV, MCI, "020", 0.6)
    merged = merge_zoom(FRAME, (222, 60, 454, 160), made)
    assert find_score_geo(merged, find_clocks([i["text"] for i in merged])) == (0, 0)
