"""beIN NFL bug of Atlanta-Cincinnati (2026-11-08, tab 452598602): the team record is followed by a SPACE before the code ('5-2 CIN', '3-5 ATL] 10') and
the quarter + clock can be glued behind a score ('3-5ATL10 2ND 4:46'). The loose reader gave nothing, so for hours the watcher had no baseline, three
scores were feed-only and the records ('5-2') were read as a score (false (5, 2) event at 17:18)."""
from daemon.scoreboard_templates import read_bein_nfl


def live(rows):
    """Items from (text, height, x0, y0) rows; boxes.log has no right edge, so it is estimated, except where `WIDTH` knows the real one."""
    return [{"text": t, "x0": x, "y0": y, "x1": x + WIDTH.get(t, int(h * 0.55 * len(t))), "y1": y + h, "conf": 0.9} for t, h, x, y in rows]


WIDTH = {"5-2 CIN": 112}   # the 'CIN' item ends before the '14' that starts at x 558 (measured on the screen)


GLUED = [('LIVE', 14, 75, 849), ('5-2CIN14', 41, 438, 836), ('3-5ATL10 2ND 4:46', 41, 746, 836), ('10', 10, 22, 851)]            # 16:38:03
SPACED = [('DIRECT', 24, 80, 46), ('3', 22, 243, 69), ('5-2 CIN', 48, 436, 834), ('14', 48, 558, 834), ('3-5 ATL] 10', 48, 746, 834),
          ('2ND', 38, 949, 839), ('4:04', 38, 1031, 839), ('Ist & GOAL', 42, 1259, 835)]                                           # 16:38:44


def test_the_bug_is_read_with_a_space_after_the_record_and_a_glued_quarter_and_clock():
    assert read_bein_nfl(live(GLUED), ["4:46"]) == (14, 10)
    assert read_bein_nfl(live(SPACED), ["4:04"]) == (14, 10)


def test_records_alone_are_never_a_score():
    records_only = [('5-2 CIN', 48, 436, 834), ('3-5 ATL', 48, 746, 834), ('2ND', 38, 949, 839), ('4:04', 38, 1031, 839)]
    assert read_bein_nfl(live(records_only), ["4:04"]) is None
    one_team = [r for r in SPACED if not r[0].startswith("5-2") and r[0] != "14"]
    assert read_bein_nfl(live(one_team), ["4:04"]) is None                      # only ATL: no pair


# The 'UPCOMING SCHEDULE' graphic (17:17:52): team records ('5-2', '8-1', '6-3') in a row and a big '8' were read as a score; (5, 2) FIRED in the replay.
SCHEDULE = [('DIRECI', 24, 82, 46), ('NFL', 50, 1546, 100), ('8', 139, 450, 512), ('UPCOMING SCHEDULE', 27, 317, 653), ('ATLANTA FALCONS', 38, 317, 697),
            ('WEEK 10', 30, 364, 750), ('WEEK 12', 30, 560, 750), ('WEEK 13', 30, 756, 750), ('VS', 30, 316, 808), ('5-2', 24, 456, 828), ('AT', 30, 512, 808),
            ('8-1', 24, 654, 828), ('VS', 30, 708, 808), ('5-2', 24, 846, 828), ('6-3', 24, 1042, 828), ('BYE WEEK 11', 28, 324, 864)]


def test_a_schedule_graphic_is_no_score_reading():
    from daemon.score_watch import ScoreWatcher
    w = ScoreWatcher()
    for n in range(6):
        items = live(SPACED)
        w.update([i["text"] for i in items], ["4:04"], 3.0 * n, items, sport="american football")
    assert w.score == (14, 10)
    for n in range(8):
        items = live(SCHEDULE)
        fired = w.update([i["text"] for i in items], [], 30.0 + 3.0 * n, items, sport="american football")
        assert not fired
    assert w.score == (14, 10)
