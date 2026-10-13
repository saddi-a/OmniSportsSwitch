import json

from daemon import espn_history as eh

LEAGUE, GAME = "uefa.champions", "900001"


def play(pid, type_id, type_text, text, wallclock="2026-10-13T17:00:00Z", x=None, y=None, team="Team A", period=1):
    p = {"id": pid, "type": {"id": type_id, "text": type_text}, "text": text, "period": {"number": period}, "team": {"displayName": team}}
    if wallclock:
        p["wallclock"] = wallclock
    if x is not None:
        p["fieldPositionX"], p["fieldPositionY"] = x, y
    return p


def entry(pl, seconds, twice=True):
    one = {"sequence": 0, "time": {"value": float(seconds), "displayValue": "x"}, "text": pl["text"], "play": pl}
    return [one, dict(one)] if twice else [one]


def summary(entries, state="post", completed=True, comp="900001"):
    return {"header": {"competitions": [{"id": comp, "status": {"type": {"name": "STATUS_FULL_TIME" if state == "post" else "STATUS_SECOND_HALF",
                                                                         "state": state, "completed": completed}}}]},
            "commentary": [{"sequence": 0, "time": {"value": 0.0}, "text": "Lineups are announced."}] + [e for pair in entries for e in pair]}


def kinds(events):
    return [(e["kind"], e["tier"]) for e in events]


# ---- classification -------------------------------------------------------------------------------------
def test_tier_mapping_and_dedupe_of_double_entries():
    s = summary([
        entry(play("1", "70", "Goal", "Goal! A 1, B 0.", "2026-10-13T17:00:10Z"), 600),
        entry(play("2", "93", "Red Card", "X (B) is shown the red card.", "2026-10-13T17:05:00Z"), 900),
        entry(play("3", "106", "Shot On Target", "Attempt saved. X shot from outside the box is saved.", "2026-10-13T17:06:00Z", 75.0, 47.0), 960),
        entry(play("4", "117", "Shot Off Target", "Attempt missed. shot from the centre of the box.", "2026-10-13T17:07:00Z", 88.0, 50.0), 1020),
        entry(play("5", "117", "Shot Off Target", "Attempt missed. shot from outside the box.", "2026-10-13T17:08:00Z", 70.0, 50.0), 1080),
        entry(play("6", "135", "Shot Blocked", "Attempt blocked.", "2026-10-13T17:09:00Z", 90.0, 90.0), 1140),
        entry(play("7", "136", "Hit Woodwork", "Hits the bar.", "2026-10-13T17:10:00Z", 90.0, 50.0), 1200),
        entry(play("8", "95", "Corner Awarded", "Corner, A.", "2026-10-13T17:11:00Z"), 1260),
        entry(play("9", "66", "Foul", "Foul by X (B).", "2026-10-13T17:12:00Z"), 1320),
        entry(play("10", "68", "Offside", "Offside, A.", "2026-10-13T17:13:00Z"), 1380),
        entry(play("11", "94", "Yellow Card", "X is shown the yellow card.", "2026-10-13T17:14:00Z"), 1440),
        entry(play("12", "76", "Substitution", "Substitution, A.", "2026-10-13T17:15:00Z"), 1500),
        entry(play("13", "118", "Pass", "A pass.", "2026-10-13T17:16:00Z"), 1560),
    ])
    ev = eh.parse_commentary(s, LEAGUE, GAME)
    assert kinds(ev) == [("goal", 1), ("red card", 1), ("save", 2), ("shot off target", 2), ("shot off target", 3), ("shot blocked", 3),
                         ("hit woodwork", 2), ("corner", 2), ("foul", 3), ("offside", 3), ("yellow card", 3), ("substitution", 3)]
    assert ev[0]["family"] == "goal" and ev[0]["game_seconds"] == 600.0 and ev[0]["game_id"] == GAME
    assert ev[0]["wallclock_epoch"] == eh.parse_wallclock("2026-10-13T17:00:10Z")
    assert ev[3]["in_box"] is True and ev[4]["in_box"] is False and ev[5]["in_box"] is False   # y 90 is outside the width of the area


def test_shot_without_position_uses_the_wording():
    assert eh.classify("117", "Shot Off Target", "shot from the centre of the box misses", None, None) == ("shot off target", 2)
    assert eh.classify("117", "Shot Off Target", "shot from outside the box misses", None, None) == ("shot off target", 3)
    assert eh.classify("135", "Shot Blocked", "header from very close range is blocked", None, None) == ("shot blocked", 2)
    assert eh.classify("135", "Shot Blocked", "blocked", None, None) == ("shot blocked", 3)


def test_goal_variants_and_penalty_goal_are_one_goal_each():
    s = summary([entry(play("1", "70", "Goal", "Goal!", "2026-10-13T17:00:10Z"), 600),
                 entry(play("2", "137", "Goal - Header", "Goal! header", "2026-10-13T17:00:11Z"), 600),
                 entry(play("3", "98", "Penalty - Scored", "Penalty scored", "2026-10-13T17:30:00Z", team="Team B"), 3000)])
    ev = eh.parse_commentary(s, LEAGUE, GAME)
    assert kinds(ev) == [("goal", 1), ("penalty goal", 1)]


def test_penalty_awarded_has_no_type_two_foul_entries_make_one_event():
    s = summary([entry(play("1", "66", "Foul", "Penalty Team A. X draws a foul in the penalty area.", "2026-10-13T17:20:00Z", team="Team A"), 1200),
                 entry(play("2", "66", "Foul", "Penalty conceded by Y (Team B) after a foul in the penalty area.", "2026-10-13T17:20:00Z", team="Team B"), 1200),
                 entry(play("3", "66", "Foul", "Foul by Z (B).", "2026-10-13T17:21:00Z"), 1260)])
    ev = eh.parse_commentary(s, LEAGUE, GAME)
    assert kinds(ev) == [("penalty awarded", 1), ("foul", 3)]
    assert ev[0]["family"] == "penalty" and ev[0]["team"] == "Team A" and not ev[0]["text"].lower().startswith("penalty conceded")


def test_var_texts_and_goal_overturned():
    s = summary([entry(play("1", "172", "VAR - Referee decision cancelled", "VAR Decision: Goal A 1-0 B.", "2026-10-13T17:00:00Z"), 600),
                 entry(play("2", "172", "VAR - Referee decision cancelled", "VAR Decision: No Goal A 0-0 B.", "2026-10-13T17:10:00Z"), 1200),
                 entry(play("3", "175", "Deleted After Review", "GOAL OVERTURNED BY VAR: X scores but ruled out.", "2026-10-13T17:10:00Z"), 1200)])
    assert kinds(eh.parse_commentary(s, LEAGUE, GAME)) == [("var goal", 2), ("var no goal", 2), ("goal overturned", 2)]


def test_handball_is_tier2_only_in_the_offenders_own_box():
    assert eh.classify("122", "Handball", "Handball by X", 8.0, 50.0) == ("handball", 2)
    assert eh.classify("122", "Handball", "Handball by X", 86.8, 58.4) == ("handball", 3)
    assert eh.classify("122", "Handball", "Handball by X", None, None) == ("handball", 3)


def test_missing_wallclock_is_estimated_from_the_nearest_entry():
    s = summary([entry(play("1", "95", "Corner Awarded", "Corner", "2026-10-13T17:00:00Z"), 600),
                 entry(play("2", "95", "Corner Awarded", "Corner", None), 640),
                 entry(play("3", "95", "Corner Awarded", "Corner", "2026-10-13T17:05:00Z"), 900)])
    ev = eh.parse_commentary(s, LEAGUE, GAME)
    assert ev[1]["wallclock_est"] is True
    assert ev[1]["wallclock_epoch"] == eh.parse_wallclock("2026-10-13T17:00:00Z") + 40.0


def test_plays_add_free_kicks_and_details_without_duplicates(tmp_path):
    com = summary([entry(play("1", "106", "Shot On Target", "Attempt saved.", "2026-10-13T17:00:00Z", 90.0, 50.0), 600)])
    pages = [{"pageCount": 1, "items": [
        {"id": "1", "type": {"id": "106", "text": "Shot On Target"}, "shotInfo": {"text": "Regular Play"}, "targetZone": {"text": "High Left"}, "wallclock": "2026-10-13T17:00:00Z"},
        {"id": "50", "type": {"id": "8", "text": "Free Kick"}, "text": "X Free Kick at 20'", "wallclock": "2026-10-13T17:02:00Z", "clock": {"value": 720.0},
         "period": {"number": 1}, "fieldPositionX": 80.0, "fieldPositionY": 40.0, "team": {"$ref": "http://x/teams/494?lang=en"}, "shootout": False},
        {"id": "51", "type": {"id": "8", "text": "Free Kick"}, "text": "Y Free Kick at 21'", "wallclock": "2026-10-13T17:03:00Z", "clock": {"value": 780.0},
         "fieldPositionX": 30.0, "fieldPositionY": 40.0, "shootout": False},
        {"id": "52", "type": {"id": "118", "text": "Pass"}, "wallclock": "2026-10-13T17:03:10Z", "shootout": False}]}]
    d = tmp_path / LEAGUE / GAME
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps(com), encoding="utf-8")
    (d / "plays-001.json").write_text(json.dumps(pages[0]), encoding="utf-8")
    ev = eh.game_events(tmp_path, LEAGUE, GAME, tiers=(1, 2, 3))
    assert kinds(ev) == [("save", 2), ("free kick", 2), ("free kick", 3)]
    assert ev[0]["shot_info"] == "Regular Play" and ev[0]["target_zone"] == "High Left" and ev[1]["src"] == "plays" and ev[1]["team"] == "494"
    assert len(eh.game_events(tmp_path, LEAGUE, GAME)) == 2                                         # default: tiers 1 and 2
    assert eh.game_events(tmp_path, LEAGUE, "nope") == []


# ---- fetching ------------------------------------------------------------------------------------------
class FakeNet:
    def __init__(self, state="post", pages=2):
        self.urls, self.state, self.pages = [], state, pages

    def __call__(self, url):
        self.urls.append(url)
        if "summary" in url:
            return 200, summary([entry(play("1", "95", "Corner Awarded", "Corner"), 600)], state=self.state, comp="777")
        page = int(url.rsplit("page=", 1)[1])
        return 200, {"pageCount": self.pages, "items": [{"id": f"p{page}", "type": {"id": "118", "text": "Pass"}}]}


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 0.0, []

    def clock(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def test_fetch_game_caches_paces_and_never_refetches(tmp_path):
    net, fc = FakeNet(), FakeClock()
    pacer = eh.Pacer(2.0, fc.clock, fc.sleep)
    rep = eh.fetch_game(LEAGUE, GAME, tmp_path, fetch=net, pacer=pacer)
    assert rep["action"] == "fetched" and rep["requests"] == 3 and rep["pages"] == 2
    assert all("page=" in u for u in net.urls[1:]) and "/competitions/777/plays" in net.urls[1]            # the competition id comes from the summary
    assert len(fc.sleeps) == 2 and all(s >= 1.99 for s in fc.sleeps)                                       # first request free, each later one 2 s after
    assert eh.is_complete(tmp_path, LEAGUE, GAME)
    again = eh.fetch_game(LEAGUE, GAME, tmp_path, fetch=net, pacer=pacer)
    assert again["action"] == "cached" and again["requests"] == 0 and len(net.urls) == 3
    summ, items = eh.load_game(tmp_path, LEAGUE, GAME)
    assert summ is not None and [i["id"] for i in items] == ["p1", "p2"]


def test_unfinished_game_is_skipped_and_stores_nothing(tmp_path):
    net = FakeNet(state="in")
    rep = eh.fetch_game(LEAGUE, GAME, tmp_path, fetch=net, pacer=eh.Pacer(0, lambda: 0.0, lambda s: None))
    assert rep["action"] == "not-finished" and len(net.urls) == 1
    assert not eh.game_dir(tmp_path, LEAGUE, GAME).exists()


def test_failed_page_keeps_what_was_stored_and_a_rerun_resumes(tmp_path):
    state = {"fail": True}
    base = FakeNet(pages=3)

    def flaky(url):
        if "page=2" in url and state["fail"]:
            return 503, None
        return base(url)
    quick = eh.Pacer(0, lambda: 0.0, lambda s: None)
    rep = eh.fetch_game(LEAGUE, GAME, tmp_path, fetch=flaky, pacer=quick)
    assert rep["action"] == "error" and not eh.is_complete(tmp_path, LEAGUE, GAME)
    state["fail"] = False
    base.urls.clear()
    rep = eh.fetch_game(LEAGUE, GAME, tmp_path, fetch=flaky, pacer=quick)
    assert rep["action"] == "fetched" and not any("summary" in u or "page=1" in u for u in base.urls)       # page 1 and the summary were kept
    assert eh.is_complete(tmp_path, LEAGUE, GAME) and len(eh.load_game(tmp_path, LEAGUE, GAME)[1]) == 3


def test_summary_error_is_reported(tmp_path):
    rep = eh.fetch_game(LEAGUE, GAME, tmp_path, fetch=lambda url: (None, None), pacer=eh.Pacer(0, lambda: 0.0, lambda s: None))
    assert rep["action"] == "error" and not eh.game_dir(tmp_path, LEAGUE, GAME).exists()
