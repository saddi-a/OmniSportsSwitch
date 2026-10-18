"""Plans which tabs move to which slots when a stream is promoted to MAIN, honouring "allowed slots".

Pure logic. `roles` maps tab id -> the slot it sits in now; `allowed` maps tab id -> the set of slots it
may use, or None for no restriction.

Default plan: the winner and the old MAIN stream trade places. If the old MAIN stream may not go to the
winner's slot, it goes to another slot it is allowed in, and whichever stream was there moves into the
winner's old slot (a three-way rotation). If no valid arrangement exists, the swap is refused.

`fixed` is a set of slots that must not move at all (Window 2 = LEFT while the user holds or freezes it): no swap, rotation or
fix touches a tab that sits in one, and a rotation never uses one.
"""

MAIN = "MAIN"
SLOT_PREFERENCE = ["LEFT", "RIGHT_BOT", "RIGHT_MID", "RIGHT_TOP"]


def _ok(allowed: dict[int, set[str] | None], tab_id: int, role: str) -> bool:
    permitted = allowed.get(tab_id)
    return permitted is None or role in permitted


def plan_fix(roles: dict[int, str], allowed: dict[int, set[str] | None], fixed=()) -> list[tuple[int, str]] | None:
    """A tab sits in a slot it may not use (a rule changed): trade places with a tab that fits both ways.
    MAIN is left to the scorer, which knows who holds it. A slot in `fixed` is left alone (neither side moves)."""
    for tab, slot in roles.items():
        if slot == MAIN or slot in fixed or _ok(allowed, tab, slot):
            continue
        for other, other_slot in roles.items():
            if (other != tab and other_slot != MAIN and other_slot not in fixed
                    and _ok(allowed, tab, other_slot) and _ok(allowed, other, slot)):
                return [(tab, other_slot), (other, slot)]
    return None


def plan_swap(
    promote: int, demote: int, roles: dict[int, str], allowed: dict[int, set[str] | None], fixed=()
) -> list[tuple[int, str]] | None:
    """List of (tab id, slot it should end up in), or None if the swap is not allowed (or would move a slot in `fixed`)."""
    winner_slot, main_slot = roles.get(promote), roles.get(demote)
    if winner_slot is None or main_slot is None or winner_slot in fixed or main_slot in fixed:
        return None
    if not _ok(allowed, promote, main_slot):
        return None
    if _ok(allowed, demote, winner_slot):
        return [(promote, main_slot), (demote, winner_slot)]

    for slot in SLOT_PREFERENCE:
        if slot in (winner_slot, main_slot) or slot in fixed or not _ok(allowed, demote, slot):
            continue
        occupant = next((t for t, r in roles.items() if r == slot and t not in (promote, demote)), None)
        if occupant is None:
            return [(promote, main_slot), (demote, slot)]
        if _ok(allowed, occupant, winner_slot):
            return [(promote, main_slot), (demote, slot), (occupant, winner_slot)]
    return None


def plan_move(tab: int, slot: str, roles: dict[int, str], allowed: dict[int, set[str] | None]) -> list[tuple[int, str]] | None:
    """Put `tab` into `slot` (the user's "Make Window 2"): the tab that sits there trades places with it. [] when it already is
    there, None when either of them may not use the other's slot or the tab has no known slot."""
    here = roles.get(tab)
    if here is None or not _ok(allowed, tab, slot):
        return None
    if here == slot:
        return []
    occupant = next((t for t, r in roles.items() if r == slot and t != tab), None)
    if occupant is None:
        return [(tab, slot)]
    if not _ok(allowed, occupant, here):
        return None
    return [(tab, slot), (occupant, here)]
