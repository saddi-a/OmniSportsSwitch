import asyncio
import copy
import datetime
import json
import logging
import pathlib
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from daemon import feed
from daemon.feed import (FeedRecorder, FeedSession, FeedTracker, Poller, clock_seconds, fetch_json, game_elapsed_s, parse_scoreboard,
                         parse_summary)
from daemon.scoring import Config, Scorer
from daemon.server import Server

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
UTC = datetime.timezone.utc


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def soccer():
    return parse_scoreboard(load("feed_soccer_scoreboard.json"), "ita.1")


def nfl():
    return parse_scoreboard(load("feed_nfl_scoreboard.json"), "nfl")


# ---------------------------------------------------------------- parsing
def test_soccer_game_fields_and_events():
    como = soccer()[0]
    assert (como.home.abbr, como.away.abbr) == ("COMO", "ROMA")
    assert (como.state, como.status, como.period, como.display, como.clock_s) == ("in", "STATUS_SECOND_HALF", 2, "84'", 5040.0)
    assert (como.home_score, como.away_score) == (0, 0)
    kinds = [(e.kind, e.display, e.team) for e in como.events]
    assert kinds[0] == ("yellow card", "45'+2'", "COMO")
    red = [e for e in como.events if e.kind == "red card"]
    assert len(red) == 1 and red[0].clock_s == 3164.0 and red[0].display == "53'"
    assert soccer()[1].state == "pre" and soccer()[1].events == ()


def test_stoppage_time_events_keep_the_feed_clock_of_the_period_end():
    # a card shown as 45'+2' has clock 2700 s (45:00): events there can only be placed to the minute
    first = soccer()[0].events[0]
    assert first.display == "45'+2'" and first.clock_s == 2700.0


def test_soccer_event_kinds():
    def kind(**flags):
        return feed._soccer_kind({"type": {"text": "x"}, "scoringPlay": False, "redCard": False, "yellowCard": False,
                                  "penaltyKick": False, "ownGoal": False, "shootout": False, **flags})
    assert kind(scoringPlay=True) == "goal"
    assert kind(scoringPlay=True, penaltyKick=True) == "penalty goal"
    assert kind(scoringPlay=True, ownGoal=True) == "own goal"
    assert kind(redCard=True) == "red card" and kind(yellowCard=True) == "yellow card"
    assert kind(penaltyKick=True) == "penalty"
    assert kind(shootout=True, scoringPlay=True) == "shootout"
    assert feed._soccer_kind({"type": {"text": "Substitution"}}) == "substitution"


def test_american_football_scoreboard_games():
    pre, post = nfl()
    assert pre.state == "pre" and pre.clock_s == 0.0 and pre.sport == "american football"
    assert post.state == "post" and (post.home_score, post.away_score) == (16, 24) and post.period == 4
    assert post.events == () and post.situation is None


def test_the_situation_of_a_live_football_game_is_kept_as_given():
    data = copy.deepcopy(load("feed_nfl_scoreboard.json"))
    data["events"][0]["competitions"][0]["situation"] = {"down": 3, "distance": 4, "isRedZone": True}
    assert parse_scoreboard(data, "nfl")[0].situation == {"down": 3, "distance": 4, "isRedZone": True}


def test_a_game_that_cannot_be_read_is_skipped_with_a_warning(caplog):
    data = {"events": [{"id": "1"}, load("feed_soccer_scoreboard.json")["events"][0]]}
    with caplog.at_level(logging.WARNING, logger="daemon"):
        snaps = parse_scoreboard(data, "ita.1")
    assert len(snaps) == 1 and "could not be read" in caplog.text
    assert parse_scoreboard(None, "ita.1") == [] and parse_scoreboard({}, "nfl") == []


def test_clock_helpers():
    assert clock_seconds("12:21") == 741.0 and clock_seconds("0:07.5") == 7.5
    assert clock_seconds("84'") is None and clock_seconds(None) is None and clock_seconds("") is None
    assert game_elapsed_s("soccer", 2, 3164.0) == 3164.0
    assert game_elapsed_s("american football", 2, 741.0) == 900 + (900 - 741)
    assert game_elapsed_s("american football", None, 100.0) is None and game_elapsed_s("soccer", 1, None) is None
    assert game_elapsed_s("tennis", 1, 5.0) is None


def test_summary_keeps_only_plays_worth_knowing():
    post = nfl()[1]
    events = parse_summary(load("feed_nfl_summary.json"), post)
    assert events and all(e.kind for e in events)
    kinds = {e.kind for e in events}
    assert "touchdown" in kinds
    td = next(e for e in events if e.kind == "touchdown")
    assert td.period in (1, 2, 3, 4) and td.clock_s is not None and td.text
    assert len({e.key for e in events}) == len(events)
    assert parse_summary({}, post) == () and parse_summary(None, post) == ()


def test_play_kinds():
    def kind(text, **extra):
        return feed._play_kind({"type": {"text": text}, **extra})
    assert kind("Rushing Touchdown") == "touchdown" and kind("Passing Touchdown") == "touchdown"
    assert kind("Field Goal Good") == "field goal good" and kind("Field Goal Missed") == "field goal"
    assert kind("Pass Interception Return") == "interception" and kind("Sack") == "sack" and kind("Penalty") == "penalty"
    assert kind("Pass Reception", statYardage=35) == "big play" and kind("Pass Reception", statYardage=5) == ""
    assert kind("Rush", scoringPlay=True) == "score" and kind("Kickoff") == ""


# ---------------------------------------------------------------- what changed
def test_nothing_is_reported_for_what_was_already_there_when_a_game_is_first_seen():
    tracker = FeedTracker()
    notes = tracker.update(soccer())
    assert [n.kind for n in notes] == ["game", "game"]       # no event notes for the old cards
    assert tracker.update(soccer()) == []                    # and nothing when nothing changed


def test_a_new_event_a_score_change_and_a_status_change_are_reported_once():
    tracker = FeedTracker()
    tracker.update(soccer())
    later = soccer()
    old = later[0]
    goal = feed.FeedEvent(old.game_id, (old.game_id, "Goal", 5100.0, "104"), "goal", None, 5100.0, "85'", "ROMA", "Goal")
    later[0] = dataclasses_replace(old, away_score=1, status="STATUS_HALFTIME", events=old.events + (goal,))
    notes = tracker.update(later)
    assert sorted(n.kind for n in notes) == ["event", "score", "status"]
    assert next(n for n in notes if n.kind == "score").text == "0-0 -> 0-1"
    assert next(n for n in notes if n.kind == "event").event.kind == "goal"
    assert tracker.update(later) == []


def dataclasses_replace(obj, **changes):
    import dataclasses
    return dataclasses.replace(obj, **changes)


def test_plays_found_in_a_summary_are_reported_once_and_not_on_the_first_sight():
    tracker = FeedTracker()
    post = nfl()[1]
    tracker.update([post])
    events = parse_summary(load("feed_nfl_summary.json"), post)
    assert tracker.add_events(post.game_id, events, first=True) == []
    assert tracker.add_events(post.game_id, events, first=False) == []     # already seen
    fresh = feed.FeedEvent(post.game_id, (post.game_id, "play", "new"), "touchdown", 4, 30.0, "0:30", "", "TD")
    notes = tracker.add_events(post.game_id, (fresh,), first=False)
    assert [n.event.kind for n in notes] == ["touchdown"]
    assert tracker.add_events("unknown", (fresh,), first=False) == []


# ---------------------------------------------------------------- when to ask
def test_a_quiet_league_is_asked_rarely_and_a_live_one_every_five_seconds():
    poller = Poller(["ita.1", "nfl"], poll_s=5.0, idle_s=120.0)
    utc = datetime.datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
    assert poller.due(0.0) == ["ita.1", "nfl"]
    poller.done("ita.1", 0.0, soccer(), utc)                # Como-Roma is live
    poller.done("nfl", 0.0, [nfl()[1]], utc)                # only a finished game
    assert poller.due(4.9) == [] and poller.due(5.0) == ["ita.1"] and poller.due(120.0) == ["ita.1", "nfl"]


def test_a_game_starting_soon_makes_a_league_active():
    pre = soccer()[1]
    start = datetime.datetime.fromisoformat(pre.start.replace("Z", "+00:00"))
    poller = Poller(["ita.1"], 5.0, 120.0)
    poller.done("ita.1", 0.0, [pre], start - datetime.timedelta(minutes=10))
    assert poller.next_at["ita.1"] == 5.0
    poller.done("ita.1", 0.0, [pre], start - datetime.timedelta(hours=3))
    assert poller.next_at["ita.1"] == 120.0


def test_the_feed_is_never_asked_more_often_than_every_five_seconds():
    poller = Poller(["nfl"], poll_s=1.0, idle_s=1.0)
    assert (poller.poll_s, poller.idle_s) == (5.0, 5.0)


def test_errors_back_off_and_two_404s_switch_a_league_off():
    poller = Poller(["a", "b"], 5.0, 120.0)
    waits = []
    for _ in range(6):
        poller.failed("a", 100.0, 500)
        waits.append(poller.next_at["a"] - 100.0)
    assert waits == [5.0, 10.0, 30.0, 60.0, 60.0, 60.0]
    assert poller.failed("b", 0.0, 404) is False and "b" not in poller.disabled
    assert poller.failed("b", 10.0, 404) is True and "b" in poller.disabled and "b" not in poller.due(1e6)
    poller.failed("a", 0.0, None)                       # a network error is not a 404
    assert "a" not in poller.disabled
    poller.done("a", 5.0, [], datetime.datetime.now(UTC))
    assert poller.errors["a"] == 0


# ---------------------------------------------------------------- disk and network
class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/ok":
            body = b'{"events": []}'
        elif self.path == "/bad":
            body = b"not json"
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def fake_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_fetch_json(fake_server):
    assert fetch_json(fake_server + "/ok") == (200, {"events": []})
    assert fetch_json(fake_server + "/missing") == (404, None)
    assert fetch_json(fake_server + "/bad") == (None, None)
    assert fetch_json("http://127.0.0.1:1/x", timeout=1.0) == (None, None)    # nothing listens there


def test_the_recorder_writes_changes_only(tmp_path):
    recorder = FeedRecorder(tmp_path / "feed")
    when = datetime.datetime(2026, 10, 11, 14, 18, 30, 123000)
    snaps = soccer()
    recorder.snaps(snaps, when)
    recorder.snaps(snaps, when)                                  # unchanged: nothing written
    changed = [dataclasses_replace(snaps[0], display="85'", clock_s=5100.0), snaps[1]]
    recorder.snaps(changed, when + datetime.timedelta(seconds=1))
    lines = [json.loads(x) for x in (tmp_path / "feed" / "2026-10-11.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x["type"] for x in lines] == ["snap", "snap", "snap"]
    assert lines[0]["t"] == "2026-10-11T14:18:30.123" and lines[2]["display"] == "85'"
    recorder.error("nfl", 500, when)
    assert json.loads((tmp_path / "feed" / "2026-10-11.jsonl").read_text(encoding="utf-8").splitlines()[-1]) == {
        "t": "2026-10-11T14:18:30.123", "type": "error", "league": "nfl", "status": 500}


def test_the_recorder_writes_events(tmp_path):
    recorder = FeedRecorder(tmp_path)
    snap = soccer()[0]
    event = feed.FeedEvent(snap.game_id, ("k",), "goal", None, 5100.0, "85'", "ROMA", "Goal")
    recorder.notes([feed.Note("event", snap, "Goal", event), feed.Note("score", snap, "0-0 -> 0-1")],
                   datetime.datetime(2026, 10, 11, 14, 19))
    rows = [json.loads(x) for x in (tmp_path / "2026-10-11.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["type"] == "event" and rows[0]["kind"] == "goal" and rows[0]["home"] == "COMO"


def test_a_disk_error_does_not_raise(tmp_path, caplog):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with caplog.at_level(logging.WARNING, logger="daemon"):
        FeedRecorder(blocker / "feed").error("nfl", 500, datetime.datetime.now())
    assert "could not write" in caplog.text


# ---------------------------------------------------------------- the whole step
class FakeFeed:
    """Serves fixtures by URL and counts the requests."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, url):
        self.calls.append(url)
        for part, answer in self.routes.items():
            if part in url:
                return answer
        return 404, None


def live_football(games=2):
    data = copy.deepcopy(load("feed_nfl_scoreboard.json"))
    data["events"] = data["events"][:games]
    for e in data["events"]:
        e["status"]["type"]["state"] = "in"
    return data


def test_a_step_reads_the_due_leagues_and_records(tmp_path):
    fake = FakeFeed({"soccer/ita.1": (200, load("feed_soccer_scoreboard.json"))})
    session = FeedSession(Config(), FeedRecorder(tmp_path), fetch=fake, leagues=["ita.1", "uefa.champions"])
    wall = datetime.datetime(2026, 10, 11, 14, 18, tzinfo=UTC)
    notes = session.step(0.0, wall)
    assert [n.kind for n in notes] == ["game", "game"]
    assert len(fake.calls) == 2 and fake.calls[0].endswith("/soccer/ita.1/scoreboard")
    assert session.step(1.0, wall) == [] and len(fake.calls) == 2           # not due yet
    # the champions league answered 404: asked again after the back-off, switched off at the second 404
    assert "uefa.champions" not in session.poller.disabled
    session.step(6.0, wall)
    assert "uefa.champions" in session.poller.disabled
    assert any("switched off" in m for m in session.messages)
    assert (tmp_path / "2026-10-11.jsonl").exists()


def test_a_step_fetches_the_plays_of_live_football_games_only(tmp_path):
    fake = FakeFeed({"/scoreboard": (200, live_football()), "summary?event=": (200, load("feed_nfl_summary.json"))})
    session = FeedSession(Config(), None, fetch=fake, leagues=["nfl"])
    wall = datetime.datetime(2026, 10, 11, 18, 0, tzinfo=UTC)
    session.step(0.0, wall)
    summaries = [u for u in fake.calls if "summary" in u]
    assert len(summaries) == 2                                              # one per live game
    session.step(6.0, wall)
    assert len([u for u in fake.calls if "summary" in u]) == 2              # summaries wait feed_summary_s (10 s)
    session.step(11.0, wall)
    assert len([u for u in fake.calls if "summary" in u]) == 4


def test_summaries_can_be_switched_off():
    fake = FakeFeed({"/scoreboard": (200, live_football())})
    session = FeedSession(Config(feed_summary_s=0), None, fetch=fake, leagues=["nfl"])
    session.step(0.0, datetime.datetime(2026, 10, 11, 18, 0, tzinfo=UTC))
    assert not [u for u in fake.calls if "summary" in u]


def test_a_new_play_in_a_later_summary_becomes_a_note():
    summary = load("feed_nfl_summary.json")
    fake = FakeFeed({"/scoreboard": (200, live_football(1)), "summary?event=": (200, summary)})
    session = FeedSession(Config(), None, fetch=fake, leagues=["nfl"])
    wall = datetime.datetime(2026, 10, 11, 18, 0, tzinfo=UTC)
    session.step(0.0, wall)
    later = copy.deepcopy(summary)
    later["drives"]["previous"][0]["plays"].append({"id": "999", "text": "Y. Z 40 yard touchdown", "period": {"number": 4},
        "clock": {"displayValue": "0:45"}, "scoringPlay": True, "type": {"text": "Passing Touchdown"}, "statYardage": 40})
    fake.routes["summary?event="] = (200, later)
    notes = session.step(11.0, wall)
    assert [n.event.kind for n in notes] == ["touchdown"] and notes[0].event.clock_s == 45.0


def test_a_failing_league_never_raises(tmp_path):
    session = FeedSession(Config(), FeedRecorder(tmp_path), fetch=lambda url: (None, None), leagues=["nfl"])
    assert session.step(0.0, datetime.datetime(2026, 10, 11, 18, 0, tzinfo=UTC)) == []
    assert session.poller.next_at["nfl"] == 5.0


# ---------------------------------------------------------------- the server
def test_the_default_mode_is_local_and_starts_nothing():
    assert Config().feed_mode == 0
    assert Server(Scorer(Config())).feed is None


@pytest.mark.asyncio
async def test_the_server_logs_what_the_feed_reports(caplog, monkeypatch):
    snap = soccer()[0]
    event = feed.FeedEvent(snap.game_id, ("k",), "red card", None, 3164.0, "53'", "ROMA", "Red Card")

    class FakeSession:
        messages = ["feed league xyz not available (HTTP 404), switched off"]
        calls = 0

        def games(self):
            return [snap]

        def situations(self):
            return {}

        def fresh_game_ids(self, now):
            return set()

        def step(self, now, wall):
            FakeSession.calls += 1
            return [feed.Note("event", snap, "Red Card", event)] if FakeSession.calls == 1 else []

    server = Server(Scorer(Config(feed_mode=1)))
    server.feed = FakeSession()
    real_sleep = asyncio.sleep
    monkeypatch.setattr("daemon.server.asyncio.sleep", lambda s: real_sleep(0.01))
    with caplog.at_level(logging.INFO, logger="daemon"):
        task = asyncio.create_task(server._feed_loop())
        await real_sleep(0.2)
        task.cancel()
    assert "feed event ita.1 COMO-ROMA" in caplog.text and "red card 53' ROMA: Red Card" in caplog.text
    assert "switched off" in caplog.text
    assert FakeSession.messages == []


@pytest.mark.asyncio
async def test_a_crash_in_the_feed_is_logged_and_the_loop_goes_on(caplog, monkeypatch):
    class Broken:
        messages: list = []
        calls = 0

        def step(self, now, wall):
            Broken.calls += 1
            raise RuntimeError("boom")

    server = Server(Scorer(Config(feed_mode=1)))
    server.feed = Broken()
    real_sleep = asyncio.sleep
    monkeypatch.setattr("daemon.server.asyncio.sleep", lambda s: real_sleep(0.01))
    with caplog.at_level(logging.WARNING, logger="daemon"):
        task = asyncio.create_task(server._feed_loop())
        await real_sleep(0.2)
        task.cancel()
    assert Broken.calls > 1 and "feed step failed: RuntimeError: boom" in caplog.text


def test_feed_lines_reach_events_log():
    from daemon.logsetup import EventFilter
    record = logging.LogRecord("daemon", logging.INFO, "", 0, "feed event ita.1 COMO-ROMA [84'] goal", None, None)
    assert EventFilter().filter(record)
