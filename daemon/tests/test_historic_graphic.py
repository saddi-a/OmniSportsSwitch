"""A flashback graphic ("SEPTEMBER 23,2001": NY Jets 10, New England 3, live 2026-10-18 20:11 on the CBS broadcast of Jets-Patriots) is never a score source."""
import datetime

from daemon.score_watch import HISTORIC_DATE_RE, ScoreWatcher, historic_graphic

YEAR = datetime.date.today().year

# the real texts of the daemon.log lines at 20:11:22, 20:11:27 and 20:11:32
FLASHBACK = [
    ["SEPTEMBER 23,2001", "@CBS SPORTS", "NYJETS", "10", "NEW ENGLAND", "3", "BRADY", "19", "o"],
    ["SEPTEMBER 23,2001", "@CBS SPORTS", "NY JETS", "10", "NEW ENGLAND", "3", "BRADY", "19", "USAA"],
    ["SEPTEMBER 23,2001", "@CBS SPORTS", "NY JETS", "10", "NEW ENGLAND", "3", "BRADY", "1?"],
]
LIVE = ["@CBS SPORTS", "NY JETS", "3", "NEW ENGLAND", "7", "2ND", "2:25"]


def fires(w, frames, clocks=()):
    return [w.update(list(t), list(clocks)) for t in frames]


def test_the_real_flashback_texts_are_recognised_as_historic():
    for texts in FLASHBACK:
        assert historic_graphic(texts)
    assert historic_graphic(["NY JETS AT NEW ENGLAND", "@CBS SPORTS", "SEPTEMBER", "2001", "5", "9"])   # the same graphic with the date split in two items


def test_the_flashback_makes_no_score_change_and_no_baseline():
    w = ScoreWatcher()
    assert not any(fires(w, [LIVE] * 5))
    assert not any(fires(w, FLASHBACK * 3))                 # nine readings of 10-3: nothing
    assert not any(fires(w, [LIVE] * 3))                    # and the real board (3-7) afterwards is still the unchanged baseline
    assert w.score == (3, 7)


def test_control_without_the_date_the_same_board_still_reads():
    w = ScoreWatcher()
    fires(w, [LIVE] * 5)
    real_change = ["@CBS SPORTS", "NY JETS", "10", "NEW ENGLAND", "7", "2ND", "2:25"]      # a real touchdown for the Jets (3-7 -> 10-7)
    assert any(fires(w, [real_change] * 4))


def test_control_a_date_of_this_year_or_text_without_a_month_is_no_flashback():
    assert not historic_graphic([f"OCTOBER 18, {YEAR}", "NY JETS", "3", "NEW ENGLAND", "7"])
    assert not historic_graphic(["Premier League 2026/27", "BHA", "1", "CRY", "0"])
    assert not historic_graphic(["LALIGA 2025", "1-0", "MAY"])                 # a year and a month word apart from each other, not a date
    assert not historic_graphic(["2:00", "JETS", "3", "PATRIOTS", "7", "2ND", "1ST & 10"])
    assert not historic_graphic(["NY JETS", "10", "NEW ENGLAND", "3", "BRADY", "2001"])   # a year alone (a player's draft class) is no date


def test_a_past_year_with_a_month_in_french_or_abbreviated_is_historic():
    assert historic_graphic(["23 SEPTEMBRE 2001", "NYJ", "10", "NE", "3"])
    assert historic_graphic(["SEP. 23, 1999", "NYJ", "10", "NE", "3"])
    assert historic_graphic(["JANUARY 2002", "NYJ", "10", "NE", "3"])
    assert HISTORIC_DATE_RE.search("DECEMBRE 1998")
