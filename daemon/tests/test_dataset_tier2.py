"""Tier 2 split into 2a / 2b and goals cancelled by a commentary 'goal overturned'. Synthetic data only."""
from daemon import dataset_builder as db
from daemon import espn_history as eh
from daemon.tests.test_dataset_commentary import build, com, goal_com
from daemon.tests.test_dataset_builder import always


def test_tier2_group_table():
    kinds = ("save", "shot on target", "hit woodwork", "var goal", "var no goal", "goal overturned", "handball", "corner", "shot blocked",
             "shot off target", "free kick")
    assert [eh.tier2_group(k, 2) for k in kinds] == ["2a"] * 7 + ["2b"] * 4
    assert eh.tier2_group("goal", 1) is None and eh.tier2_group("foul", 3) is None


def test_classified_events_carry_the_group():
    kind, tier = eh.classify("106", "Shot On Target", "Attempt saved.", 90, 50)
    assert (kind, tier) == ("save", 2)
    ev = eh._event("g", "l", 1.0, 1.0, 1, {}, kind, tier, "t", "x", 90, 50, "commentary")
    assert ev["group"] == "2a"


def test_misses_are_reported_per_group():
    r = build([com("save", 130, 700.0, game="gB"), com("corner", 200, 770.0, game="gB")], main_of=always(1))
    ev = {e["kind"]: e for e in r["events"]}
    assert ev["save"]["group"] == "2a" and ev["corner"]["group"] == "2b"
    s = r["summary"]["football"]
    assert (s["tier2a_events"], s["misses_2a"], s["tier2b_events"], s["misses_2b"]) == (1, 1, 1, 1) and s["misses"] == 2
    assert "tier 2a" in db.format_summary(r["summary"])


def test_feed_goal_followed_by_goal_overturned_within_90s_is_cancelled():
    r = build([com("goal overturned", 140, 740.0, game="gA")], goals=[("gA", 100, "goal")])
    assert r["commentary_report"]["goals_cancelled"] == 1
    f = r["feed_events"][0]
    assert f["goal"] is False and f["tier"] == 2 and f["group"] == "2a" and f["cancelled_goal"] is True
    assert not [e for e in r["events"] if e["goal"]]
    assert r["summary"]["football"]["goals"] == 0


def test_goal_overturned_later_than_90s_does_not_cancel():
    r = build([com("goal overturned", 400, 1000.0, game="gA")], goals=[("gA", 100, "goal")])
    assert r["commentary_report"]["goals_cancelled"] == 0
    assert r["feed_events"][0]["goal"] is True


def test_one_overturn_cancels_one_goal_commentary_goals_too():
    r = build([goal_com(100, 700.0), goal_com(120, 720.0), com("goal overturned", 150, 750.0, game="gA")])
    assert r["commentary_report"]["goals_cancelled"] == 1
    assert len([e for e in r["events"] if e["goal"]]) == 1
