"""The offline score agrees with the live scorer on strong cues: the touchdown pair rule (scoring-bugs-98) and the goal jingle (daemon/jingle.py)."""
from daemon.scoring import Config
from director_training import features as F

T = 1_000_000.0


def row(dt, surge=0.0, cue_age=None, kind=None, strong=False, value=0.35, tab=1, sport="american football", fade=1.0):
    r = {"t": T + dt, "tab": tab, "sport": sport, "surge": surge, "crowd": 0.0, "speech": 0.0, "music": 0.0, "shout": 0.0, "event": 0.0, "fav": 1.0,
         "lev": 0.0, "state": "live", "weight": 1.0, "bu": {"cues": []}}
    if cue_age is not None:
        r["cue"] = {"value": value, "age": cue_age, "strong": strong}
        r["cue_fade"] = fade
        r["bu"]["cues"] = [[cue_age, value, kind]]
    return r


def call_rows(call_at, surge_at, n=20, strong_after=None, **kw):
    """1 s rows around a spoken touchdown at call_at; a surge of 0.5 at surge_at (None = none)."""
    return [row(i, surge=0.5 if i == surge_at else 0.0, cue_age=(i - call_at) if i >= call_at else None, kind="spoken:touchdown", **kw) for i in range(n)]


def strong_of(rows):
    return [bool((r.get("cue") or {}).get("strong")) for r in rows]


def test_a_surge_after_the_call_makes_it_strong_from_that_moment():
    rows = F.apply_cue_rules(call_rows(5, 8), Config())
    assert strong_of(rows)[5:8] == [False] * 3 and all(strong_of(rows)[8:])


def test_a_surge_before_the_call_makes_it_strong_at_once():
    rows = F.apply_cue_rules(call_rows(8, 5), Config())
    assert not any(strong_of(rows)[:8]) and all(strong_of(rows)[8:])


def test_no_surge_or_one_too_far_away_stays_capped():
    assert not any(strong_of(F.apply_cue_rules(call_rows(5, None), Config())))
    assert not any(strong_of(F.apply_cue_rules(call_rows(2, 12, n=20), Config())))     # 10 s after the call: outside the 6 s window
    assert not any(strong_of(F.apply_cue_rules(call_rows(14, 5, n=20), Config())))     # 9 s before the call


def test_a_small_surge_does_not_pair():
    rows = call_rows(5, 8)
    rows[8]["surge"] = 0.2
    assert not any(strong_of(F.apply_cue_rules(rows, Config())))


def test_the_rule_follows_the_settings_and_can_be_switched_off():
    cfg = Config()
    cfg.touchdown_pair_window_s = 0.0
    recorded_strong = call_rows(5, 8, strong=True)
    assert not any(strong_of(F.apply_cue_rules(recorded_strong, cfg)))          # off: a touchdown cue is the capped cue, whatever the recording says
    cfg2 = Config()
    cfg2.touchdown_pair_surge = 0.8
    assert not any(strong_of(F.apply_cue_rules(call_rows(5, 8), cfg2)))


def test_other_cues_other_tabs_and_other_sports_are_untouched():
    rows = [row(i, surge=0.5 if i == 8 else 0.0, cue_age=i, kind="spoken:red zone") for i in range(12)]
    assert not any(strong_of(F.apply_cue_rules(rows, Config())))
    rows = F.apply_cue_rules(call_rows(5, 8, sport="football"), Config())
    assert not any(strong_of(rows))                                              # a soccer tab has no touchdown rule
    mixed = call_rows(5, None) + [dict(row(8, surge=0.5), tab=2)]
    assert not any(strong_of(F.apply_cue_rules(mixed, Config())))               # the surge was on another tab


def test_a_recorded_strong_cue_that_is_not_a_touchdown_stays_strong_and_the_input_is_not_changed():
    jingle = [row(i, cue_age=i, kind="jingle", strong=True, value=0.5) for i in range(5)]
    out = F.apply_cue_rules(jingle, Config())
    assert all(strong_of(out))
    rows = call_rows(5, 8)
    F.apply_cue_rules(rows, Config())
    assert not any(strong_of(rows))


def test_score_from_row_counts_a_strong_cue_in_full_and_a_capped_one_below_the_threshold():
    cfg = Config()
    capped = row(6, cue_age=1.0, kind="spoken:touchdown", value=0.5)
    strong = row(6, cue_age=1.0, kind="spoken:touchdown", value=0.5, strong=True)
    assert F.score_from_row(capped, cfg) <= cfg.cue_alone_cap + 1e-9 and F.score_from_row(strong, cfg) >= 0.5 - 1e-9 > cfg.cue_alone_cap


def test_the_goal_jingle_is_a_strong_cue_in_the_score():
    cfg = Config()
    jingle = row(3, cue_age=2.0, kind="jingle", strong=True, value=cfg.weights.jingle_cue_weight, sport="football", fade=0.9)
    assert F.score_from_row(jingle, cfg) > cfg.min_score        # 0.5 * 0.9 = 0.45 alone, in full (not capped to 0.30)
