from daemon.screen_ocr import find_clocks


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
