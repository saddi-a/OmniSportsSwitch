import datetime

from daemon.feed_compare import (Measurement, crossing, measure, parse_clock, parse_feed, parse_ocr, shows_teams, summarise,
                                 tiles_of)

D = "2026-10-11"


def ocr(time, clock, texts=("DA", "COM", "0-0", "ROM"), tab=7, tile=0):
    clocks = f"['{clock}']" if clock else "[]"
    return f"{D} {time}   ocr tab {tab}[{tile}]: clocks={clocks} texts={list(texts)!r}"


def t(text):
    return datetime.datetime.fromisoformat(f"{D}T{text}")


def readings():
    # the stream clock passes 84:00 at 14:18:31 (real log shape: the colon is sometimes lost, the clock sometimes missing)
    lines = [ocr("14:18:25,578", "83:54"), ocr("14:18:28,355", "83:57"), ocr("14:18:30,722", None),
             ocr("14:18:33,265", "84:02"), ocr("14:18:35,834", "84:05")]
    return parse_ocr(lines)


def snap(time, display, **extra):
    return {"t": t(time), "type": "snap", "league": "ita.1", "game": "1", "home": "COMO", "away": "ROMA", "state": "in",
            "display": display, **extra}


def test_ocr_lines_are_parsed_and_junk_is_skipped():
    r = readings()
    assert [x.clock_s for x in r] == [5034.0, 5037.0, None, 5042.0, 5045.0]
    assert r[0].tab == 7 and r[0].texts == ("DA", "COM", "0-0", "ROM")
    assert parse_ocr(["garbage", f"{D} 14:00:00,000 something else"]) == []
    assert parse_clock("83:54") == 5034.0 and parse_clock("83.54") is None and parse_clock("") is None


def test_feed_lines_are_parsed_and_junk_is_skipped():
    rows = parse_feed(['{"t": "2026-10-11T14:18:32.000", "type": "snap"}', "nope", '{"type": "snap"}'])
    assert len(rows) == 1 and rows[0]["t"] == t("14:18:32.000")


def test_teams_are_recognised_by_their_codes_without_accents():
    assert shows_teams(["DA", "COM", "ROM"], "COMO", "ROMA")
    assert not shows_teams(["DA", "COM"], "COMO", "ROMA")
    assert shows_teams(["PARIS", "Lyon"], "PAR", "LYO")
    assert shows_teams(["CÔM", "RÖM"], "COMO", "ROMA")


def test_a_tile_needs_several_readings_with_both_teams():
    r = readings()
    assert tiles_of(r, "COMO", "ROMA") == [(7, 0)]
    assert tiles_of(r[:2], "COMO", "ROMA") == []
    assert tiles_of(r, "JUVE", "ROMA") == []


def test_crossing_up_and_down():
    r = readings()
    assert crossing(r, (7, 0), 5040.0, True, t("14:18:32")) == t("14:18:33.265")
    assert crossing(r, (7, 0), 5100.0, True, t("14:18:32")) is None                  # never reached
    assert crossing(r, (7, 0), 5000.0, True, t("14:18:32")) is None                  # already past it at the first reading
    down = parse_ocr([ocr("18:00:00,000", "12:30"), ocr("18:00:03,000", "12:27"), ocr("18:00:06,000", "12:24")])
    assert crossing(down, (7, 0), 12 * 60 + 25.0, False, t("18:00:05")) == t("18:00:06")
    assert crossing(r, (9, 9), 5040.0, True, t("14:18:32")) is None                  # another tile
    assert crossing(r, (7, 0), 5040.0, True, t("15:30:00")) is None                  # too far from the feed's time


def test_a_clock_jump_is_not_a_crossing():
    jump = parse_ocr([ocr("14:00:00,000", "10:00"), ocr("14:00:03,000", "40:00")])
    assert crossing(jump, (7, 0), 20 * 60.0, True, t("14:00:02")) is None


def test_a_minute_turning_over_gives_the_lead():
    rows = [snap("14:18:17.000", "84'"), snap("14:18:32.000", "85'")]
    ms = measure(rows, readings())
    assert len(ms) == 1 and ms[0].what == "minute 85" and round(ms[0].lead_s, 1) == 1.3     # stream reached 84:00 at 14:18:33.265


def test_a_feed_that_is_behind_the_picture_has_a_negative_lead():
    rows = [snap("14:18:17.000", "84'"), snap("14:18:40.000", "85'")]
    assert measure(rows, readings())[0].lead_s < 0


def test_only_consecutive_minutes_in_play_count():
    rows = [snap("14:18:17.000", "84'"), snap("14:18:32.000", "86'"), snap("14:18:40.000", "HT"),
            snap("14:18:50.000", "85'", state="pre")]
    assert measure(rows, readings()) == []


def test_an_event_is_matched_to_the_stream_clock():
    row = {"t": t("14:18:20.000"), "type": "event", "league": "ita.1", "game": "1", "home": "COMO", "away": "ROMA",
           "kind": "red card", "display": "84'", "clock_s": 5040.0}
    ms = measure([row], readings())
    assert ms[0].what == "red card 84'" and round(ms[0].lead_s, 1) == 13.3


def test_nothing_is_measured_without_a_tile_or_for_unknown_leagues():
    rows = [snap("14:18:17.000", "84'"), snap("14:18:32.000", "85'")]
    assert measure(rows, []) == []
    assert measure([{**rows[1], "league": "xx.9"}], readings()) == []


def test_the_summary_lists_moments_and_statistics():
    m = [Measurement("ita.1 COMO-ROMA", "minute 85", t("14:18:32"), t("14:18:34"), (7, 0)),
         Measurement("ita.1 COMO-ROMA", "minute 86", t("14:19:32"), None, (7, 0))]
    text = summarise(m)
    assert "lead +2 s" in text and "stream clock not found" in text and "1 moments matched: median lead +2 s" in text
    assert "no moment could be matched" in summarise([])
