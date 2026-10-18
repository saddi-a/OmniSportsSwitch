from daemon.screen_ocr import find_clocks, looks_like_team_name


def test_finds_clock_strings():
    assert find_clocks(["PSG", "1", "32:19", "LMFC"]) == ["32:19"]
    assert find_clocks(["4th 3:08", "2nd & 10"]) == ["3:08"]


def test_normalises_dot_and_spaces():
    assert find_clocks(["13 : 16", "07.54"]) == ["13:16", "07:54"]


def test_ignores_scores_and_plain_numbers():
    assert find_clocks(["20", "17", "1 - 0", "2024"]) == []


def test_accepts_comma_misread():
    assert find_clocks(["3,08   19"]) == ["3:08"]


# ---- the digit fallback, on a real Big Ten scorebug whose big white digits the normal reading misses ------------

import pathlib  # noqa: E402

import numpy as np  # noqa: E402
import pytest  # noqa: E402

from daemon.score_watch import find_score_geo  # noqa: E402
from daemon.screen_ocr import MODEL_DIR, ScreenReader, find_clocks  # noqa: E402

BIGTEN = pathlib.Path(__file__).parent / "fixtures" / "bigten_scorebug.png"


@pytest.mark.skipif(not MODEL_DIR.exists() or not any(MODEL_DIR.iterdir()), reason="the OCR models are not downloaded")
def test_big_digits_beside_a_team_name_are_found_when_the_normal_reading_misses_them():
    from PIL import Image

    image = np.asarray(Image.open(BIGTEN).convert("RGB"))
    try:
        reader = ScreenReader()
    except Exception as e:  # e.g. no GPU in the test environment
        pytest.skip(f"the OCR reader could not start: {e}")
    items = reader.read_items(image)
    assert find_score_geo(items, find_clocks([i["text"] for i in items])) is None   # the normal reading finds nothing
    extra = reader.read_digits_beside_names(image, items)
    assert [e["text"] for e in extra] == ["16", "45"]                                # MARYLAND 16, OHIO STATE 45
    assert extra[0]["x1"] < extra[1]["x0"]                                           # boxes do not overlap
    texts = [i["text"] for i in items + extra]
    assert find_score_geo(items + extra, find_clocks(texts)) == (16, 45)


def test_team_names_for_the_digit_fallback_include_ranked_names_but_not_plain_words():
    for name in ("MARYLAND", "K-STATE", "OHIO STATE", "2 Georgia", "6 ALABAMA", "10 Ohio State"):
        assert looks_like_team_name(name), name
    for text in ("Search", "Home", "3rd & 4", "12", "2nd", "1st & 10", "4-0", "Ath"):
        assert not looks_like_team_name(text), text


def test_banner_words_are_not_team_names():
    for text in ("RULING ON FIELD", "Targeting", "REPLAY REVIEW", "INSTANT REPLAY", "COMMERCIAL BREAK", "FINAL"):
        assert not looks_like_team_name(text), text
    assert looks_like_team_name("OHIO STATE") and looks_like_team_name("2 Georgia") and looks_like_team_name("K-STATE")


def test_a_clock_whose_colon_was_read_as_a_digit_is_recovered():
    assert find_clocks(["12853"]) == ["12:53"]
    assert find_clocks(["09331"]) == ["09:31"]
    assert find_clocks(["14848"]) == ["14:48"]
    assert find_clocks(["12:53"]) == ["12:53"]      # a normal clock is untouched
    assert find_clocks(["12963"]) == []             # the middle digit must be a 3 or an 8
    assert find_clocks(["12877"]) == []             # seconds of 60 or more are not a clock
    assert find_clocks(["128530"]) == []            # exactly five digits


def _item(text, x0, y0, x1, y1):
    return {"text": text, "conf": 0.9, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def test_the_zoom_pass_reads_a_strip_around_the_clock_and_maps_boxes_back():
    import numpy as np
    from daemon.screen_ocr import ScreenReader, merge_zoom

    reader = ScreenReader.__new__(ScreenReader)
    seen = {}

    def fake_read_items(image):
        seen["shape"] = image.shape
        return [_item("7", 40, 20, 60, 50)]       # in the enlarged crop

    reader.read_items = fake_read_items
    image = np.zeros((400, 800, 3), dtype=np.uint8)
    items = [_item("12:53", 400, 100, 460, 120), _item("TEX", 300, 100, 340, 120)]
    crop, zoomed = reader.zoom_pass(image, items)
    x0, y0, x1, y1 = crop
    assert (x1 - x0) * 2 == seen["shape"][1] and (y1 - y0) * 2 == seen["shape"][0]   # twice the size
    assert zoomed == [_item("7", x0 + 20, y0 + 10, x0 + 30, y0 + 25)]
    merged = merge_zoom(items, crop, zoomed)
    assert [it["text"] for it in merged] == ["7"]   # the clock and the code lie inside the crop: the zoomed reading replaces them


def test_the_zoom_pass_needs_a_clock_or_a_down_to_anchor_on():
    import numpy as np
    from daemon.screen_ocr import ScreenReader

    reader = ScreenReader.__new__(ScreenReader)
    assert reader.zoom_pass(np.zeros((100, 100, 3), dtype=np.uint8), [_item("TEX", 1, 1, 30, 20)]) is None


def test_merge_zoom_keeps_what_is_outside_the_crop():
    from daemon.screen_ocr import merge_zoom

    items = [_item("HEADLINE", 0, 0, 100, 20), _item("TEX", 300, 100, 340, 120), _item("OKL", 360, 100, 400, 120)]
    merged = merge_zoom(items, (280, 90, 420, 130), [_item("TEX", 300, 100, 340, 120), _item("7", 345, 100, 358, 120)])
    assert sorted(it["text"] for it in merged) == ["7", "HEADLINE", "TEX"]


def test_the_video_players_position_over_length_is_not_a_game_clock():
    assert find_clocks(["71:27", "11:16/14:06"]) == ["71:27"]
    assert find_clocks(["11:16 / 14:06"]) == []


def _boxes(rows):
    return [{"text": t, "x0": a, "y0": b, "x1": c, "y1": d, "conf": q} for t, a, b, c, d, q in rows]


# Real boxes (benchmark frames, 2026-10-18 / 2026-10-10 / 2026-10-04), a few unrelated items left out.
STRA_PSG = _boxes([("STRA", 83, 27, 123, 45, .98), ("0", 138, 30, 148, 44, .98), ("PSG", 83, 49, 115, 69, .99), ("0", 138, 52, 148, 67, .97),
                   ("03.03", 37, 71, 79, 91, .82), ("k", 845, 243, 867, 259, .51), ("1.20", 111, 505, 143, 523, .54),
                   ("HD", 784, 506, 797, 515, .96), ("00", 7, 504, 27, 527, .92), ("10.49", 151, 509, 191, 523, .65)])
LIV_MCI = _boxes([("LIV", 61, 34, 98, 55, .79), ("02", 100, 27, 193, 60, .99), ("MCI", 196, 34, 242, 58, .99), ("71-27", 127, 57, 169, 73, .67),
                  ("Premier Loayue", 849, 488, 932, 502, .84), ("00", 26, 510, 46, 533, .74), ("11:16", 131, 513, 171, 529, .94),
                  ("14.06", 179, 513, 219, 529, .97), ("HD", 803, 512, 815, 522, .97)])
INDIANA = _boxes([("INDIANA", 371, 441, 429, 461, .97), ("3", 443, 436, 463, 472, .94), ("3", 490, 435, 510, 472, .99),
                  ("MEBRASKA", 522, 438, 600, 464, .55), ("colandrea", 723, 469, 779, 485, .42), ("7/12,78 YDs", 785, 469, 841, 485, .56),
                  ("140", 420, 484, 456, 508, 1.0), ("38", 464, 484, 492, 508, 1.0), ("Ist & 10", 498, 484, 560, 508, .71)])
MIAMI = _boxes([("MIAMI", 53, 985, 167, 1021, .94), ("CLEMSON", 1185, 985, 1363, 1021, .74), ("17", 320, 1010, 458, 1085, 1.0),
                ("3", 950, 1010, 1039, 1083, 1.0), ("2ND", 516, 1053, 599, 1088, .98), (":18", 635, 1051, 738, 1088, .84),
                ("25", 794, 1053, 858, 1086, 1.0)])


def test_player_bar_times_are_dropped_by_position_when_they_are_two_items():
    assert find_clocks([i["text"] for i in STRA_PSG]) == ["03:03", "1:20", "10:49"]   # the texts alone cannot tell
    assert find_clocks([i["text"] for i in STRA_PSG], STRA_PSG) == ["03:03"]
    assert find_clocks([i["text"] for i in LIV_MCI], LIV_MCI) == []                 # the '71-27' dash read is no clock; the bar is gone


def test_two_clocks_that_are_not_the_bottom_line_are_both_kept():
    # a control: the same two clocks with text below them are not a player bar (a scorebug may sit low on the screen)
    below = STRA_PSG + _boxes([("FOOTER", 100, 530, 160, 545, .9)])
    assert find_clocks([i["text"] for i in below], below) == ["03:03", "1:20", "10:49"]
    # and one clock alone on the bottom line is the game clock
    alone = [i for i in STRA_PSG if i["text"] != "10.49"]
    assert find_clocks([i["text"] for i in alone], alone) == ["03:03", "1:20"]


def test_seconds_of_sixty_or_more_are_not_a_clock():
    assert find_clocks([i["text"] for i in INDIANA], INDIANA) == []   # '12,78' from '7/12,78 YDs'
    assert find_clocks(["7/12,78 YDs"]) == []
    assert find_clocks(["12:59", "0:00"]) == ["12:59", "0:00"]


def test_a_bare_seconds_clock_beside_its_quarter_is_read():
    assert find_clocks([i["text"] for i in MIAMI], MIAMI) == ["0:18"]
    assert find_clocks([i["text"] for i in MIAMI]) == []   # without boxes nothing is guessed


def test_a_bare_seconds_text_without_a_quarter_or_sure_reading_is_ignored():
    no_quarter = [i for i in MIAMI if i["text"] != "2ND"]
    assert find_clocks([i["text"] for i in no_quarter], no_quarter) == []
    unsure = [dict(i, conf=0.5) if i["text"] == ":18" else i for i in MIAMI]
    assert find_clocks([i["text"] for i in unsure], unsure) == []
    far = [dict(i, x0=i["x0"] + 600, x1=i["x1"] + 600) if i["text"] == ":18" else i for i in MIAMI]
    assert find_clocks([i["text"] for i in far], far) == []
