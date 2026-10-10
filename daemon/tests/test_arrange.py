from daemon.arrange import plan_swap

# tab 1 = PSG stream, only allowed on MAIN or LEFT. Others unrestricted unless stated.
PSG_ONLY = {1: {"MAIN", "LEFT"}}


def test_unrestricted_streams_simply_trade_places():
    roles = {1: "MAIN", 2: "RIGHT_BOT", 3: "LEFT"}
    assert plan_swap(promote=2, demote=1, roles=roles, allowed={}) == [(2, "MAIN"), (1, "RIGHT_BOT")]


def test_restricted_main_stream_is_rotated_into_an_allowed_slot():
    roles = {1: "MAIN", 2: "RIGHT_BOT", 3: "LEFT"}
    plan = plan_swap(2, 1, roles, PSG_ONLY)
    assert plan == [(2, "MAIN"), (1, "LEFT"), (3, "RIGHT_BOT")]


def test_restricted_stream_may_take_the_winners_slot_if_allowed():
    roles = {1: "MAIN", 2: "LEFT", 3: "RIGHT_BOT"}
    assert plan_swap(2, 1, roles, PSG_ONLY) == [(2, "MAIN"), (1, "LEFT")]


def test_rotation_goes_to_an_empty_allowed_slot_without_moving_anything_else():
    roles = {1: "MAIN", 2: "RIGHT_BOT"}   # nobody in LEFT
    assert plan_swap(2, 1, roles, PSG_ONLY) == [(2, "MAIN"), (1, "LEFT")]


def test_rotation_refused_when_the_occupant_cannot_take_the_winners_slot():
    roles = {1: "MAIN", 2: "RIGHT_BOT", 3: "LEFT"}
    allowed = {1: {"MAIN", "LEFT"}, 3: {"LEFT", "MAIN"}}   # the LEFT stream may not go to RIGHT_BOT
    assert plan_swap(2, 1, roles, allowed) is None


def test_a_stream_not_allowed_on_main_can_never_be_promoted():
    roles = {1: "MAIN", 2: "RIGHT_BOT"}
    assert plan_swap(2, 1, roles, {2: {"LEFT", "RIGHT_BOT"}}) is None


def test_restricted_winner_may_be_promoted_when_main_is_allowed():
    roles = {1: "MAIN", 2: "LEFT"}
    assert plan_swap(1 + 1, 1, roles, {2: {"MAIN", "LEFT"}}) == [(2, "MAIN"), (1, "LEFT")]


def test_unknown_tabs_are_refused():
    assert plan_swap(5, 1, {1: "MAIN"}, {}) is None


def test_fix_moves_a_tab_out_of_a_slot_it_may_not_use():
    from daemon.arrange import plan_fix
    roles = {1: "RIGHT_BOT", 2: "LEFT", 3: "MAIN"}
    assert plan_fix(roles, {1: {"MAIN", "LEFT"}}) == [(1, "LEFT"), (2, "RIGHT_BOT")]


def test_fix_leaves_main_alone_and_does_nothing_when_all_fit():
    from daemon.arrange import plan_fix
    assert plan_fix({1: "MAIN", 2: "LEFT"}, {1: {"LEFT"}}) is None
    assert plan_fix({1: "LEFT", 2: "RIGHT_BOT"}, {1: {"LEFT"}}) is None
