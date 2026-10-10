"""Matches the user's favourite teams against text (scoreboard OCR or the tab's teams field).

A favourite is {"name": "PSG", "mult": 2}. The name should be the short form shown on the scoreboard.
It may also carry "slots": ["MAIN", "LEFT"], the only slots a tab showing that team may use.
"""


def _found(name: str, texts: list[str]) -> bool:
    for text in texts:
        t = text.strip().casefold()
        # very short names must be a whole text item ("AS" must not match "BASKET"); longer names may be inside
        if t == name or (len(name) >= 3 and name in t):
            return True
    return False


def match_multiplier(texts: list[str], favourites: list[dict]) -> float:
    """Biggest multiplier of any favourite found in the texts, or 1.0 if none."""
    best = 1.0
    for fav in favourites:
        name = str(fav.get("name") or "").strip().casefold()
        if len(name) < 2:
            continue
        try:
            mult = float(fav.get("mult", 1))
        except (TypeError, ValueError):
            continue
        if _found(name, texts):
            best = max(best, mult)
    return best


def match_slots(texts: list[str], teams: list[dict]) -> set[str] | None:
    """Slots allowed by the teams found in the texts (only slots every matching rule allows), or None."""
    allowed = None
    for team in teams:
        name = str(team.get("name") or "").strip().casefold()
        slots = team.get("slots")
        if len(name) < 2 or not isinstance(slots, list) or not slots or not _found(name, texts):
            continue
        allowed = set(slots) if allowed is None else (allowed & set(slots)) or allowed
    return allowed
