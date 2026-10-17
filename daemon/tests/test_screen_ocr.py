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
