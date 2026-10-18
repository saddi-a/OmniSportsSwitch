"""Window 2 (LEFT) held or frozen: swap planning treats it as fixed."""
from daemon.arrange import plan_fix, plan_move, plan_swap

FIXED = frozenset({"LEFT"})
PSG_ONLY = {1: {"MAIN", "LEFT"}}   # tab 1 may only sit in Main or Window 2


def test_a_plain_swap_between_main_and_another_window_ignores_the_fixed_slot():
    roles = {1: "MAIN", 2: "RIGHT_BOT", 3: "LEFT"}
    assert plan_swap(2, 1, roles, {}, FIXED) == [(2, "MAIN"), (1, "RIGHT_BOT")]


def test_the_rotation_does_not_use_the_fixed_slot_and_goes_to_another_window():
    roles = {1: "MAIN", 2: "RIGHT_BOT", 3: "LEFT", 4: "RIGHT_MID"}
    allowed = {1: {"MAIN", "LEFT", "RIGHT_MID"}, 4: {"RIGHT_MID", "RIGHT_BOT", "MAIN"}}   # the old Main stream may not go to Window 3
    assert plan_swap(2, 1, roles, allowed) == [(2, "MAIN"), (1, "LEFT"), (3, "RIGHT_BOT")]   # without the hold it would use Window 2
    assert plan_swap(2, 1, roles, allowed, FIXED) == [(2, "MAIN"), (1, "RIGHT_MID"), (4, "RIGHT_BOT")]


def test_the_swap_is_refused_when_only_the_fixed_slot_could_take_the_old_main_stream():
    roles = {1: "MAIN", 2: "RIGHT_BOT", 3: "LEFT"}
    assert plan_swap(2, 1, roles, PSG_ONLY) is not None
    assert plan_swap(2, 1, roles, PSG_ONLY, FIXED) is None


def test_a_stream_in_the_fixed_slot_cannot_be_promoted():
    roles = {1: "MAIN", 2: "LEFT"}
    assert plan_swap(2, 1, roles, {}) == [(2, "MAIN"), (1, "LEFT")]
    assert plan_swap(2, 1, roles, {}, FIXED) is None


def test_plan_fix_leaves_the_fixed_slot_alone():
    roles = {1: "RIGHT_BOT", 2: "LEFT", 3: "RIGHT_MID"}
    allowed = {1: {"LEFT", "RIGHT_MID"}}   # tab 1 may not sit in Window 3
    assert plan_fix(roles, allowed) == [(1, "LEFT"), (2, "RIGHT_BOT")]
    assert plan_fix(roles, allowed, FIXED) == [(1, "RIGHT_MID"), (3, "RIGHT_BOT")]
    assert plan_fix({1: "LEFT", 2: "RIGHT_BOT"}, {1: {"MAIN"}}, FIXED) is None   # the misplaced tab is the held one: untouched


def test_plan_move_trades_places_with_the_occupant_and_checks_the_rules():
    roles = {1: "MAIN", 2: "LEFT", 3: "RIGHT_BOT"}
    assert plan_move(3, "LEFT", roles, {}) == [(3, "LEFT"), (2, "RIGHT_BOT")]
    assert plan_move(1, "LEFT", roles, {}) == [(1, "LEFT"), (2, "MAIN")]
    assert plan_move(2, "LEFT", roles, {}) == []
    assert plan_move(3, "LEFT", roles, {3: {"RIGHT_BOT"}}) is None          # the tab may not use Window 2
    assert plan_move(3, "LEFT", roles, {2: {"LEFT"}}) is None               # the occupant may not leave for Window 3
    assert plan_move(9, "LEFT", roles, {}) is None                          # unknown tab
    assert plan_move(3, "LEFT", {1: "MAIN", 3: "RIGHT_BOT"}, {}) == [(3, "LEFT")]   # empty Window 2
