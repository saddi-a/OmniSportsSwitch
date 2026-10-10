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
