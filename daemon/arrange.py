"""Plans which tabs move to which slots when a stream is promoted to MAIN, honouring "allowed slots".

Pure logic. `roles` maps tab id -> the slot it sits in now; `allowed` maps tab id -> the set of slots it
may use, or None for no restriction.

Default plan: the winner and the old MAIN stream trade places. If the old MAIN stream may not go to the
winner's slot, it goes to another slot it is allowed in, and whichever stream was there moves into the
winner's old slot (a three-way rotation). If no valid arrangement exists, the swap is refused.
"""

MAIN = "MAIN"
SLOT_PREFERENCE = ["LEFT", "RIGHT_BOT", "RIGHT_MID", "RIGHT_TOP"]


def _ok(allowed: dict[int, set[str] | None], tab_id: int, role: str) -> bool:
    permitted = allowed.get(tab_id)
    return permitted is None or role in permitted


def plan_swap(
    promote: int, demote: int, roles: dict[int, str], allowed: dict[int, set[str] | None]
) -> list[tuple[int, str]] | None:
    """List of (tab id, slot it should end up in), or None if the swap is not allowed."""
    winner_slot, main_slot = roles.get(promote), roles.get(demote)
    if winner_slot is None or main_slot is None:
        return None
    if not _ok(allowed, promote, main_slot):
        return None
    if _ok(allowed, demote, winner_slot):
        return [(promote, main_slot), (demote, winner_slot)]

    for slot in SLOT_PREFERENCE:
        if slot in (winner_slot, main_slot) or not _ok(allowed, demote, slot):
            continue
        occupant = next((t for t, r in roles.items() if r == slot and t not in (promote, demote)), None)
        if occupant is None:
            return [(promote, main_slot), (demote, slot)]
        if _ok(allowed, occupant, winner_slot):
            return [(promote, main_slot), (demote, slot), (occupant, winner_slot)]
    return None
