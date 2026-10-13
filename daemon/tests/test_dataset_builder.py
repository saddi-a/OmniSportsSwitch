import builtins
import pathlib
import time

from daemon import dataset_builder as db

T0 = 1_700_000_000.0
SPAN = 400


def clock_text(t):
    s = int(600 + (t - T0))
    return f"{s // 60}:{s % 60:02d}"


def make_rows(main_of, ads=(), breaks=(), clocks=True, games=None, span=SPAN):
    """Rows every 2 s of tabs 1 and 2 (games gA, gB). main_of(t) = the Main tab at t."""
    games = games or {1: "gA", 2: "gB"}
    rows = []
    for i in range(span // 2):
        t = T0 + 2 * i
        for tab, game in games.items():
            ad = any(a <= t - T0 < b for a, b in ads) and tab == 1       # the ad / break is on tab 1 (the old Main)
            br = any(a <= t - T0 < b for a, b in breaks) and tab == 1
            rows.append({"v": 1, "t": t, "tab": tab, "is_main": main_of(t) == tab, "sport": "football", "state": "break" if br else "live",
                         "ad": ad, "game_id": game, "clock": clock_text(t) if clocks else None, "replay": False, "date": "d"})
    return rows


def feed(goals=(), others=(), games=("gA", "gB")):
    """Feed rows: a snapshot per game every 30 s and events given as (game, pt offset, kind). The feed publishes 40 s after the picture."""
    out = []
    for g in games:
        for k in range(0, SPAN + 60, 30):
            out.append({"type": "snap", "league": "eng.1", "game": g, "home": "AAA", "away": "BBB", "t": T0 + k, "score": [0, 0]})
    for g, off, kind in list(goals) + list(others):
        out.append({"type": "event", "league": "eng.1", "game": g, "home": "AAA", "away": "BBB", "kind": kind, "team": "AAA", "text": kind,
                    "t": T0 + off + 40, "clock_s": 600.0 + off})
    return out


def swap(off, new, old, source="decide"):
    return {"kind": "swap", "t": T0 + off, "promote": new, "demote": old, "source": source, "sport": "football", "date": "d"}


def run(main_of, swaps, goals=(), others=(), **kw):
    marks = kw.pop("marks", None)
    return db.build(make_rows(main_of, **kw), list(swaps), feed(goals, others), marks=marks)


def always(tab):
    return lambda t: tab


# ---- clock and alignment ---------------------------------------------------------------------------------
def test_parse_clock():
    assert db.parse_clock("36:15") == 2175.0
    assert db.parse_clock("45+2") is None and db.parse_clock(None) is None and db.parse_clock("12:99") is None


def test_clock_alignment_places_the_event_on_the_picture_not_at_feed_time():
    res = run(always(1), [], goals=[("gA", 100, "goal")])
    ev = [e for e in res["feed_events"] if e["kind"] == "goal"][0]
    assert ev["align"] == "clock" and abs(ev["pt"] - (T0 + 100)) < 1.0      # the feed said T0+140
    info = {g["game"]: g for g in res["games"]}["gA"]
    assert abs(info["lag_s"] - (-40)) < 1.0 and info["lag_src"] == "clock"


def test_missing_clock_marks_rows_unaligned_instead_of_guessing():
    res = run(always(1), [], goals=[("gA", 100, "goal")], clocks=False)
    rows = [r for r in res["rows"] if r["tab"] == 1]
    near = [r for r in rows if T0 + 20 < r["t"] < T0 + 280]
    assert all(not r["aligned"] and r["y_goal_30"] is None and r["align_src"] == "unresolved-event" for r in near)
    other = [r for r in res["rows"] if r["tab"] == 2]
    assert all(r["aligned"] for r in other)                                  # the other game has no event: safe negatives
    assert all(r["y_goal_30"] == 0 for r in other)


def test_score_change_aligns_a_goal_when_there_is_no_clock():
    changes = [{"kind": "score_change", "tab": 1, "t": T0 + 100 + 12, "home": 1, "away": 0}]
    rows = make_rows(always(1), clocks=False)
    res = db.build(rows, changes, feed(goals=[("gA", 100, "goal")]))
    ev = [e for e in res["feed_events"] if e["kind"] == "goal"][0]
    assert ev["align"] == "score" and abs(ev["pt"] - (T0 + 100)) < 0.1


def test_weak_lag_fallback_marks_label_quality():
    # no rows after 120 s: a red card at 250 s has no clock rows near it, so the game's lag (from the goal) is used
    rows = [r for r in make_rows(always(1)) if not (r["tab"] == 1 and r["t"] > T0 + 120)]
    res = db.build(rows, [], feed(goals=[("gA", 100, "goal")], others=[("gA", 250, "red card")]))
    red = [e for e in res["feed_events"] if e["kind"] == "red card"][0]
    assert red["align"] == "lag" and abs(red["pt"] - (T0 + 250)) < 2.0


# ---- labels ------------------------------------------------------------------------------------------------
def test_labels_goal_30_and_t12_60():
    res = run(always(1), [], goals=[("gA", 200, "goal")], others=[("gA", 300, "red card"), ("gA", 120, "yellow card")])
    by = {round(r["t"] - T0): r for r in res["rows"] if r["tab"] == 1}
    assert by[172]["y_goal_30"] == 1 and by[160]["y_goal_30"] == 0 and by[202]["y_goal_30"] == 0
    assert by[150]["y_t12_60"] == 1 and by[130]["y_t12_60"] == 0       # 60 s before the goal
    assert by[250]["y_t1_60"] == 1 and by[250]["y_goal_30"] == 0       # the red card is tier 1, not a goal
    assert by[100]["y_t12_60"] == 0                                    # a yellow card is tier 3
    assert by[172]["next_event_kind"] == "goal" and abs(by[172]["secs_to_next_event"] - 28) < 1
    assert all(r["y_goal_30"] == 0 for r in res["rows"] if r["tab"] == 2)


def test_tiers():
    assert db.tier_of("football", "penalty goal") == 1 and db.tier_of("football", "yellow card") == 3
    assert db.tier_of("american football", "interception") == 2 and db.tier_of("american football", "sack") == 3


def test_split_by_game_never_divides_a_game():
    rows = make_rows(always(1))
    db.split_by_game(rows, holdout_games=["gB"])
    assert {r["split"] for r in rows if r["game_id"] == "gA"} == {"fit"}
    assert {r["split"] for r in rows if r["game_id"] == "gB"} == {"holdout"}


# ---- regret ------------------------------------------------------------------------------------------------
def only(res, off=None):
    sw = res["swaps"]
    return sw[0] if off is None else min(sw, key=lambda s: abs(s["t"] - T0 - off))


def test_clear_regret_goal_on_the_stream_left():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 110, "goal")])
    s = only(res)
    assert s["verdict"] == "regret" and s["regret_weight"] == 2 and s["tier_old"] == 1 and s["tier_new"] == 3
    assert res["summary"]["football"]["regrets"] == 1


def test_swap_to_the_stream_with_the_goal_is_ok():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gB", 110, "goal")])
    assert only(res)["verdict"] == "ok"


def test_event_outside_the_window_is_not_regret():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 140, "goal")])      # 40 s > W
    assert only(res)["verdict"] == "tie"


def test_quiet_tie_is_not_regret_but_is_listed_for_review():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)])
    s = only(res)
    assert s["verdict"] == "tie" and s["regret_weight"] == 0 and s["review"] is True


def test_tier_gap_weight_one_for_tier2_vs_none():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], others=[("gA", 110, "interception")])
    # football has no 'interception' tier: use a tier-2 american football kind instead through the tier table
    assert only(res)["verdict"] == "tie"
    assert db._verdict(2, 3) == ("regret", 1) and db._verdict(1, 3) == ("regret", 2) and db._verdict(1, 2) == ("regret", 1)


def test_flip_flop_without_context_is_a_candidate_not_regret():
    main = lambda t: 1 if t < T0 + 100 or t >= T0 + 120 else 2      # noqa: E731
    res = run(main, [swap(100, 2, 1), swap(120, 1, 2)])
    first, back = res["swaps"]
    assert first["verdict"] == "flip-flop-candidate" and first["flipflop"] and first["review"]
    assert back["flipflop_back"] and back["verdict"] == "tie"
    assert res["summary"]["football"]["regrets"] == 0


def test_flip_flop_with_context_is_regret():
    main = lambda t: 1 if t < T0 + 100 or t >= T0 + 120 else 2      # noqa: E731
    res = run(main, [swap(100, 2, 1), swap(120, 1, 2)], goals=[("gA", 115, "goal")])
    first = res["swaps"][0]
    assert first["verdict"] == "regret" and first["flipflop"]


def test_flip_flop_window_is_respected():
    main = lambda t: 1 if t < T0 + 100 or t >= T0 + 160 else 2      # noqa: E731
    res = run(main, [swap(100, 2, 1), swap(160, 1, 2)])
    assert not res["swaps"][0]["flipflop"] and res["swaps"][0]["verdict"] == "tie"


def test_user_swap_is_excluded():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1, source="user")], goals=[("gA", 110, "goal")])
    s = only(res)
    assert s["verdict"] == "excluded" and s["reason_excluded"] == "user" and res["summary"]["football"]["regrets"] == 0


def test_swap_while_old_main_is_on_an_ad_or_break_is_excluded():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 110, "goal")], breaks=[(90, 104)])
    assert only(res)["verdict"] == "excluded"
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 110, "goal")], ads=[(90, 104)])
    assert only(res)["verdict"] == "excluded"


def test_swap_with_unresolved_labels_is_judged_with_a_wide_margin_and_labelled():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 110, "goal")], clocks=False)
    s = only(res)
    assert s["verdict"] == "regret" and s["lag"] == "assumed" and s["lag_assumed_s"] == db.DEFAULT_LAG_S["football"]
    assert s["window_s"] == [-45.0, 75.0]
    sm = res["summary"]["football"]
    assert sm["judged_wide"] == 1 and sm["judged_exact"] == 0 and sm["regrets_wide"] == 1


def test_exact_swap_is_labelled_measured():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 110, "goal")])
    assert only(res)["lag"] == "measured" and res["summary"]["football"]["judged_exact"] == 1


def test_swap_without_a_game_is_still_unjudged():
    rows = make_rows(lambda t: 1 if t < T0 + 100 else 2, games={1: None, 2: "gB"})
    res = db.build(rows, [swap(100, 2, 1)], feed())
    assert only(res)["verdict"] == "unjudged" and res["summary"]["football"]["judged"] == 0


def cue(off, tab, name="goal"):
    return {"kind": "speech_cue", "t": T0 + off, "tab": tab, "cue": name, "sport": "football"}


def test_unconfirmed_cues_stay_out_of_the_verdict_but_in_their_own_column():
    res = db.build(make_rows(lambda t: 1 if t < T0 + 100 else 2), [swap(100, 2, 1), cue(110, 1)], feed())
    s = only(res)
    assert s["verdict"] == "tie" and s["verdict_with_cues"] == "regret"
    assert res["summary"]["football"]["regrets"] == 0 and res["summary"]["football"]["regrets_with_cues"] == 1


def test_cue_confirmed_by_an_event_mark_counts():
    res = db.build(make_rows(lambda t: 1 if t < T0 + 100 else 2), [swap(100, 2, 1), cue(110, 1)], feed(),
                   event_marks=[{"t": T0 + 112, "tab": 1, "verdict": "missed-chance"}])
    assert only(res)["verdict"] == "regret" and res["confirmed_cues"] == 1
    assert res["summary"]["football"]["regrets"] == 1


def test_event_mark_alone_is_a_tier2_event_and_a_miss_subject():
    res = db.build(make_rows(always(1)), [], feed(), event_marks=[{"t": T0 + 200, "tab": 2, "verdict": "missed-chance"}])
    e = [x for x in res["events"] if x["src"] == "user"][0]
    assert e["tab"] == 2 and e["tier"] == 2 and e["status"] == "miss"
    assert res["summary"]["football"]["user_event_marks"] == 1 and res["summary"]["football"]["user_event_marks_missed"] == 1


def test_cue_on_a_marked_swap_is_confirmed():
    key = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 + 100))
    res = db.build(make_rows(lambda t: 1 if t < T0 + 100 else 2), [swap(100, 2, 1), cue(110, 1)], feed(), marks={key: {"verdict": "late"}})
    assert res["confirmed_cues"] == 1 and only(res)["verdict"] == "regret"


def test_goal_on_the_stream_just_left_is_both_regret_and_miss():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 110, "goal")])
    e = event_status(res)
    assert only(res)["verdict"] == "regret" and e["status"] == "miss" and abs(e["left_main_s"] - 10) < 1
    sm = res["summary"]["football"]
    assert sm["regrets"] == 1 and sm["misses"] == 1 and sm["misses_left_just_before"] == 1 and sm["regrets_goal_left"] == 1


def test_lead_is_measured_from_the_last_swap_whatever_its_age():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gB", 200, "goal")])
    e = event_status(res)
    assert e["status"] == "main" and abs(e["lead_last_s"] - 100) < 1
    sm = res["summary"]["football"]
    assert len(sm["last_leads"]) == 1 and abs(sm["last_leads"][0] - 100) < 1 and sm["goals_ahead_12s"] == 0


def test_user_swap_is_not_a_lead_of_the_system():
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1, source="user")], goals=[("gB", 200, "goal")])
    assert res["summary"]["football"]["last_leads"] == []


def test_load_event_marks_reads_the_review_file(tmp_path):
    d = tmp_path / "review"
    d.mkdir()
    lines = ['{"id":"evt-21:28:43-77","kind":"event","verdict":"missed-chance","tab":77}', '{"id":"21:28:44","kind":"swap","verdict":"regret"}']
    (d / "2026-10-13.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out = db.load_event_marks(tmp_path, ["2026-10-13"])
    assert len(out) == 1 and out[0]["tab"] == 77 and time.strftime("%H:%M:%S", time.localtime(out[0]["t"])) == "21:28:43"
    assert list(db.load_marks(tmp_path, ["2026-10-13"])) == ["2026-10-13 21:28:44"]


def test_user_mark_overrides_the_automatic_verdict():
    key = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(T0 + 100))
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], marks={key: {"verdict": "regret"}})
    s = only(res)
    assert s["auto_verdict"] == "tie" and s["verdict"] == "regret" and s["regret_weight"] == 1 and s["user_mark"] == "regret"
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 110, "goal")], marks={key: {"verdict": "good"}})
    assert only(res)["verdict"] == "ok" and res["summary"]["football"]["user_marks"] == 1
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 110, "goal")], marks={key: {"verdict": "late"}})
    assert only(res)["verdict"] == "regret"          # 'late' does not change the automatic verdict


# ---- misses and leads -----------------------------------------------------------------------------------------
def event_status(res, kind="goal"):
    return [e for e in res["events"] if e["kind"] == kind][0]


def test_miss_goal_on_a_stream_that_was_never_main():
    res = run(always(1), [], goals=[("gB", 200, "goal")])
    e = event_status(res)
    assert e["tab"] == 2 and e["status"] == "miss"
    assert res["summary"]["football"]["misses"] == 1


def test_swap_ahead_is_a_lead_not_a_miss():
    res = run(lambda t: 1 if t < T0 + 180 else 2, [swap(180, 2, 1)], goals=[("gB", 200, "goal")])
    e = event_status(res)
    assert e["status"] == "ahead" and abs(e["lead_s"] - 20) < 1
    assert abs(res["summary"]["football"]["mean_lead_s"] - 20) < 1 and res["summary"]["football"]["misses"] == 0


def test_late_swap_is_a_miss():
    res = run(lambda t: 1 if t < T0 + 195 else 2, [swap(195, 2, 1)], goals=[("gB", 200, "goal")])
    assert event_status(res)["status"] == "miss-late"
    res = run(lambda t: 1 if t < T0 + 210 else 2, [swap(210, 2, 1)], goals=[("gB", 200, "goal")])
    assert event_status(res)["status"] == "miss-after"


def test_goal_on_main_needs_no_swap():
    res = run(always(1), [], goals=[("gA", 200, "goal")])
    assert event_status(res)["status"] == "main"


def test_main_with_an_equal_event_is_not_a_miss():
    res = run(always(1), [], goals=[("gB", 200, "goal"), ("gA", 205, "goal")])
    st = {e["tab"]: e["status"] for e in res["events"]}
    assert st[2] == "covered" and st[1] == "main"


# ---- read-only ---------------------------------------------------------------------------------------------------
def test_nothing_is_written_outside_the_output_directory(tmp_path, monkeypatch):
    writes = []
    real = builtins.open

    def spy(file, mode="r", *a, **k):
        if any(c in str(mode) for c in "wax+"):
            writes.append(pathlib.Path(file).resolve())
        return real(file, mode, *a, **k)

    monkeypatch.setattr(builtins, "open", spy)
    out = tmp_path / "out"
    res = run(lambda t: 1 if t < T0 + 100 else 2, [swap(100, 2, 1)], goals=[("gA", 110, "goal")])
    assert writes == []                                  # building writes nothing at all
    paths = db.write_outputs(out, res)
    assert {p.name for p in paths} == {"rows.jsonl", "swaps.jsonl", "events.jsonl", "games.jsonl"}
    assert writes and all(out.resolve() in p.parents for p in writes)


def test_reading_files_roundtrip(tmp_path):
    d = tmp_path / "dataset"
    d.mkdir()
    (d / "2026-10-13.jsonl").write_text('{"t": 1, "tab": 5}\nbroken\n', encoding="utf-8")
    (d / "2026-10-13.events.jsonl").write_text('{"t": 2, "kind": "swap"}\n', encoding="utf-8")
    rows, events = db.read_days(d, ["2026-10-13", "2026-10-14"])
    assert len(rows) == 1 and rows[0]["date"] == "2026-10-13" and len(events) == 1
    assert db.available_days(d) == ["2026-10-13"]
