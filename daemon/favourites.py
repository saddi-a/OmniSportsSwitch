"""Matches the user's favourite teams against text (scoreboard OCR or the tab's teams field).

A favourite is {"name": "PSG", "mult": 2}. The name should be the short form shown on the scoreboard.
"""


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
        for text in texts:
            t = text.strip().casefold()
            # very short names must be a whole text item ("AS" must not match "BASKET"); longer names may be inside
            if t == name or (len(name) >= 3 and name in t):
                best = max(best, mult)
                break
    return best
