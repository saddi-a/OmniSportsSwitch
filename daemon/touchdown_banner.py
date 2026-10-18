"""The TOUCHDOWN banner of NFL RedZone as a score event (user option B, 2026-10-18).

RedZone flashes a huge stylised chrome 'TOUCHDOWN' across the lower third for about 4 s. EasyOCR rarely reads it (2 of 4, confidence 0.24 to
0.49) and the second reader's 2x bottom band breaks the huge letters (1 of 4); the second reader on the lower rows at 0.5x read it 4 of 4.
A LOOSE match (TOUCH / DOWN) fires on ordinary frames (3RD DOWN, TOTAL TOUCHDOWNS, YARDS/TOUCH, DOWNS), so only a STRICT whole-item match
counts: `TOUCHDOWN` with at most one junk character in front (ATOUChDOWN, KTOUCHDOWN) and a tolerated missing end (TOUCHDO), never a plural.
Measured on 460 frames of one RedZone stream: 0 false hits. Pure logic: `Server` runs the pass and feeds `TouchdownBannerWatch`.
"""
import re

BANNER_FROM = 0.60    # the banner pass reads the rows from here to the bottom (fraction of the video area height: the area comes in 3 sizes)
BANNER_SCALE = 0.5    # ...at this magnification (the huge letters read best small)

STRICT_RE = re.compile(r"^[A-Z0-9|!./]?T[O0]UCHD(?:[O0]WN|[O0])$")
HEAD_RE = re.compile(r"^[A-Z0-9|!./]?T[O0]UCH[DOI0]{0,2}$")   # the first half of a split banner
TAIL_RE = re.compile(r"^[DO0]{0,2}WN$")                      # ...and the second half
DOUBT_RE = re.compile(r"REVIEW|CHALLENGE|POSSIBLE|NO\s*TOUCHDOWN|CALLED\s*BACK|NULLIFIED|OVERTURN", re.IGNORECASE)


def is_banner_text(text: str) -> bool:
    """The whole text item is the banner word (strict)."""
    return bool(STRICT_RE.fullmatch(re.sub(r"\s+", "", text).upper()))


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def has_banner(texts: list[str]) -> bool:
    """The banner word as one item, or broken in two NEIGHBOURING items (the huge letters often split: `TOUCHDO` + `OWN`, `TOUCHO` + `OWN`,
    `ETOUCHI` + `WN`): a head `TOUCH...` straight followed by a tail `...WN`."""
    squashed = [_squash(t) for t in texts]
    if any(STRICT_RE.fullmatch(t) for t in squashed):
        return True
    return any(HEAD_RE.fullmatch(a) and TAIL_RE.fullmatch(b) for a, b in zip(squashed, squashed[1:]))


def has_doubt(texts: list[str]) -> bool:
    """Review / challenge wording on screen: a touchdown that may not count."""
    return any(DOUBT_RE.search(t) for t in texts)


class TouchdownBannerWatch:
    """Votes of one tile: `votes` banner readings within `window_s` accept it; then `cooldown_s` pass before the next. `seen=None` = the banner
    pass did not look this time (nothing changes), False = it looked and found none (the votes start over)."""

    def __init__(self):
        self.hits: list[float] = []
        self.accepted_at = float("-inf")

    def update(self, now: float, seen: bool | None, votes: int = 2, window_s: float = 6.0, cooldown_s: float = 30.0) -> bool:
        if seen is None:
            return False
        if not seen:
            self.hits = []
            return False
        if now - self.accepted_at < cooldown_s:
            self.hits = []
            return False
        self.hits = [t for t in self.hits if now - t <= window_s] + [now]
        if len(self.hits) >= max(1, int(votes)):
            self.hits = []
            self.accepted_at = now
            return True
        return False
