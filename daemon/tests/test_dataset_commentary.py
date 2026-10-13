"""Commentary events (ESPN finished-game data) in the dataset builder. Synthetic data only: no network, no logs/."""
import json

from daemon import dataset_builder as db
from daemon import espn_history as eh
from daemon.tests.test_dataset_builder import SPAN, T0, always, feed, make_rows, swap


def com(kind, off_wallclock, clock_s, game="gA", tier=2, family=None, period=1, team="AAA"):
    """A normalised espn_history event: wallclock = T0 + off_wallclock."""
    return {"game_id": game, "league": "eng.1", "wallclock_epoch": T0 + off_wallclock, "game_seconds": clock_s, "period": period, "kind": kind,
            "family": family, "tier": tier, "team": team, "text": "secret words", "src": "commentary"}


def goal_com(off_wallclock, clock_s, game="gA"):
    return com("goal", off_wallclock, clock_s, game, tier=1, family="goal")


def build(commentary, goals=(), others=(), swaps=(), main_of=always(1), **kw):
    kw_rows = {k: kw.pop(k) for k in ("clocks", "games") if k in kw}
    events_file = list(swaps) + kw.pop("changes", [])
    return db.build(make_rows(main_of, **kw_rows), events_file, feed(goals, others), commentary=commentary, **kw)


def test_commentary_event_is_placed_by_the_clock_and_labels_rows():
    # the picture shows the save at T0 + 100 (clock 700); the operator entered it 30 s later
    r = build([com("save", 130, 700.0)])
    ev = [e for e in r["events"] if e["src"] == "commentary"]
    assert len(ev) == 1 and ev[0]["tab"] == 1
    placed = r["commentary_events"]
    assert len(placed) == 1 and placed[0]["align"] == "clock" and abs(placed[0]["pt"] - (T0 + 100)) < 1
    rows1 = [x for x in r["rows"] if x["tab"] == 1]
    row = next(x for x in rows1 if x["t"] == T0 + 60)
    assert row["y_t12_60"] == 1 and row["y_t12_60_feed"] == 0 and row["next_event_kind"] == "save"
    assert next(x for x in rows1 if x["t"] == T0 + 20)["y_t12_60"] == 0                     # more than 60 s ahead
    assert "secret words" not in json.dumps(r["events"], default=str)                        # no text in the outputs


def test_commentary_for_an_unwatched_game_is_ignored():
    r = build([com("save", 130, 700.0, game="other")])
    assert r["commentary_events"] == []


def test_goal_in_both_feed_and_commentary_is_counted_once():
    r = build([goal_com(130 - 10, 700.0)], goals=[("gA", 100, "goal")])      # the feed event is at T0+140; the operator entered it at +120
    goals = [e for e in r["events"] if e["goal"]]
    assert len(goals) == 1 and goals[0]["src"] == "feed"
    assert r["commentary_events"] == [] and r["commentary_report"]["merged"] == 1
    assert r["feed_events"][0].get("also_commentary") is True


def test_a_second_goal_only_the_commentary_knows_is_added_as_a_goal():
    r = build([goal_com(250, 800.0)], goals=[("gA", 100, "goal")])
    assert [e["kind"] for e in r["commentary_events"]] == ["goal"]
    assert sorted(e["src"] for e in r["events"] if e["goal"]) == ["commentary", "feed"]


def test_wallclock_plus_lag_places_events_when_the_screen_clock_is_missing():
    # no on-screen clock; the OCR score changed at T0+212, the feed goal is at pt T0+200; the operator entered the goal at T0+230 and a save at T0+130
    changes = [{"kind": "score_change", "t": T0 + 212, "tab": 1, "home": 1, "away": 0}]
    r = build([goal_com(230, 800.0), com("save", 130, 700.0)], goals=[("gA", 200, "goal")], clocks=False, changes=changes)
    assert r["games"] and next(g for g in r["games"] if g["game"] == "gA")["lag_wc_s"] == -30.0
    save = next(e for e in r["events"] if e["kind"] == "save")
    assert save["align"] == "wc-lag" and abs(save["t"] - (T0 + 100)) < 1


def test_no_clock_and_no_goal_leaves_the_commentary_event_unplaced_and_blocks_nothing():
    r = build([com("save", 130, 700.0)], clocks=False)
    assert r["commentary_events"] == [] and r["commentary_report"]["unplaced"] == 1
    assert all(x["aligned"] for x in r["rows"] if x["tab"] == 1)


def test_first_half_stoppage_is_not_placed_by_the_clock():
    # game_seconds above 2700 in period 1 is ambiguous with the second half: the clock method is not used
    r = build([com("save", 130, 2750.0, period=1)])
    assert r["commentary_events"] == []
    r2 = build([com("save", 130, 700.0, period=1)])
    assert len(r2["commentary_events"]) == 1


def test_commentary_save_makes_a_swap_away_from_it_a_regret():
    swaps = [swap(100, 2, 1)]
    main_of = lambda t: 1 if t < T0 + 100 else 2                                                           # noqa: E731
    save_on_old = [com("save", 150, 710.0, game="gA")]                                                      # picture T0+110, tab 1 shows gA
    with_com = build(save_on_old, swaps=swaps, main_of=main_of)
    without = build(None, swaps=swaps, main_of=main_of)
    assert without["swaps"][0]["verdict"] == "tie"
    d = with_com["swaps"][0]
    assert d["verdict"] == "regret" and d["tier_old"] == 2 and d["tier_new"] == 3 and d["events_old"][0]["kind"] == "save"
    s = with_com["summary"]["football"]
    assert s["regrets"] == 1 and s["tier12_commentary"] == 1 and s["misses_commentary"] >= 0


def test_commentary_miss_is_counted_apart():
    # a save on the tab that is not Main, no swap: a miss, and the summary says it came from the commentary
    r = build([com("save", 130, 700.0, game="gB")], main_of=always(1))
    ev = next(e for e in r["events"] if e["kind"] == "save")
    assert ev["src"] == "commentary" and ev["tab"] == 2 and ev["status"] == "miss"
    s = r["summary"]["football"]
    assert s["misses_commentary"] == 1
    assert "ESPN commentary" in db.format_summary(r["summary"])


def test_load_commentary_reads_only_the_cache(tmp_path):
    d = tmp_path / "eng.1" / "gA"
    d.mkdir(parents=True)
    summary = {"header": {"competitions": [{"id": "gA", "status": {"type": {"state": "post", "completed": True}}}]},
               "commentary": [{"time": {"value": 700.0}, "text": "x", "play": {"id": "1", "type": {"id": "106", "text": "Shot On Target"}, "text": "Attempt saved.",
                                                                               "wallclock": "2026-10-13T17:00:00Z", "period": {"number": 1}}}]}
    (d / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    got = db.load_commentary(tmp_path, {"gA": "eng.1", "gB": "eng.1", "gN": "nfl"}, watched={"gA", "gN"})
    assert [e["kind"] for e in got] == ["save"]
    assert eh.game_events(tmp_path, "eng.1", "gB") == []
    assert db.game_leagues([{"game": "gA", "league": "eng.1"}, {"foo": 1}]) == {"gA": "eng.1"}


def test_build_without_commentary_is_unchanged():
    base = build(None, goals=[("gA", 100, "goal")])
    assert base["commentary_events"] == [] and base["commentary_report"] == {}
    assert SPAN > 0
