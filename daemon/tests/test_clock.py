"""One clock parser for the whole daemon (`daemon/clock.py`). Before 2026-10-18 four copies (`dataset_builder.parse_clock`, `feed.clock_seconds`,
`feed_compare.parse_clock`, `score_watch.clock_seconds`) answered differently on odd input; these tests pin the chosen behaviour for every case."""
import pytest

from daemon import clock, dataset_builder, feed, feed_compare, score_watch

CASES = [   # text, seconds (None = not a clock)
    ("12:21", 741.0), ("0:07", 7.0), ("45:00", 2700.0), ("105:00", 6300.0), ("130:00", 7800.0),
    ("0:07.5", 7.5),                 # ESPN's last-minute display: only `feed` knew it, now every caller does
    (" 12:21 ", 741.0), ("12:21\n", 741.0),   # surrounding blanks do not matter (score_watch refused the leading one)
    ("12:75", None),                 # seconds above 59 are an OCR misread (`12:85`), not a time: feed, compare and score_watch took it as 795 s
    ("150:00", None), ("999:59", None), ("1234:00", None),   # no game clock reads past 130 minutes (dataset_builder's limit, now everywhere)
    ("9:5", None), ("12:2", None),   # one digit of seconds is a misread too (feed and dataset_builder took it)
    ("12:21:05", None), ("45+2", None), ("", None), ("abc", None), ("-1:00", None), ("12.21", None),
    (None, None), (741, None),       # non-text input: score_watch and feed_compare used to raise
]


@pytest.mark.parametrize("text, expected", CASES)
def test_the_shared_parser(text, expected):
    assert clock.clock_seconds(text) == expected


@pytest.mark.parametrize("text, expected", CASES)
def test_every_old_entry_point_gives_the_shared_answer(text, expected):
    assert dataset_builder.parse_clock(text) == expected
    assert feed.clock_seconds(text) == expected
    assert feed_compare.parse_clock(text) == expected
    assert score_watch.clock_seconds(text) == expected


def test_the_answer_is_always_a_float():
    assert isinstance(clock.clock_seconds("12:21"), float) and isinstance(score_watch.clock_seconds("12:21"), float)


def test_the_first_readable_clock_of_a_list():
    assert clock.first_clock_seconds(["junk", "9:5", "12:21", "0:07"]) == 741.0
    assert clock.first_clock_seconds([]) is None and clock.first_clock_seconds(["12:75"]) is None
