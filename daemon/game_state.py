"""Works out from OCR readings whether a stream is live, on a break, or over.

Pure logic: callers pass `now` in seconds and what OCR read, so it is easy to test.

Rules (see PLAN.md):
  * text like FINAL / FULL TIME marks the game over, HALF TIME / MI-TEMPS marks a break, immediately
  * a game clock that has not changed for longer than the sport allows means a break
  * a scorebug with no readable clock for too long means a break (ad, studio segment, replay package)
"""
import re
from dataclasses import dataclass

# A caption only counts when a whole OCR text item is exactly the keyword. Words inside a sentence do not
# (an ad saying "ready for the half time?" must not mark the game as on a break).
OVER_RE = re.compile(r"(FINAL|FULL[\s-]?TIME|FT|FIN DU MATCH|TERMIN[EÉ])", re.IGNORECASE)
BREAK_RE = re.compile(r"(HALF[\s-]?TIME|HT|MI[\s-]?TEMPS|MITEMPS|INTERVALLE)", re.IGNORECASE)
CAPTION_CONFIRMATIONS = 2  # a caption must show on this many consecutive readings
KEYWORD_MEMORY_S = 120.0  # a FINAL / HALF TIME caption counts for this long after it was last seen


@dataclass(frozen=True)
class SportProfile:
    freeze_s: float    # clock unchanged for this long -> break
    no_clock_s: float  # no readable clock for this long -> break


PROFILES = {
    "football": SportProfile(freeze_s=20, no_clock_s=45),             # soccer: the clock only stops at half time / full time
    "american football": SportProfile(freeze_s=240, no_clock_s=90),   # clock stops after most plays and for TV timeouts
    "basketball": SportProfile(freeze_s=180, no_clock_s=60),          # clock stops on every foul and timeout
}
GENERIC = SportProfile(freeze_s=120, no_clock_s=60)


def profile_for(sport: str | None) -> SportProfile:
    return PROFILES.get((sport or "").strip().lower(), GENERIC)


class GameTracker:
    """Follows one video area (a whole stream, or one multiview tile) over time."""

    def __init__(self, sport: str | None = None):
        self.profile = profile_for(sport)
        self.state = "live"          # optimistic until proven otherwise
        self._clock: str | None = None
        self._clock_changed_at: float | None = None
        self._last_clock_seen: float | None = None
        self._first_seen: float | None = None
        self._over_seen = float("-inf")
        self._break_seen = float("-inf")
        self._over_hits = 0
        self._break_hits = 0

    def set_sport(self, sport: str | None) -> None:
        self.profile = profile_for(sport)

    def update(self, now: float, texts: list[str], clocks: list[str]) -> str:
        if self._first_seen is None:
            self._first_seen = now

        items = [re.sub(r"[^\w\s-]", "", t).strip() for t in texts]
        self._over_hits = self._over_hits + 1 if any(OVER_RE.fullmatch(t) for t in items) else 0
        self._break_hits = self._break_hits + 1 if any(BREAK_RE.fullmatch(t) for t in items) else 0
        if self._over_hits >= CAPTION_CONFIRMATIONS:
            self._over_seen = now
        if self._break_hits >= CAPTION_CONFIRMATIONS:
            self._break_seen = now

        if clocks:
            if clocks[0] != self._clock:
                self._clock, self._clock_changed_at = clocks[0], now
            self._last_clock_seen = now

        if now - self._over_seen <= KEYWORD_MEMORY_S:
            self.state = "over"
        elif now - self._break_seen <= KEYWORD_MEMORY_S:
            self.state = "break"
        elif clocks:
            frozen_for = now - self._clock_changed_at
            self.state = "break" if frozen_for > self.profile.freeze_s else "live"
        elif self._last_clock_seen is not None and now - self._last_clock_seen <= self.profile.no_clock_s:
            pass  # clock briefly unreadable (overlay, OCR miss): keep the previous state
        else:
            since = self._last_clock_seen if self._last_clock_seen is not None else self._first_seen
            self.state = "break" if now - since > self.profile.no_clock_s else "live"
        return self.state


def combine(states: list[str]) -> str:
    """State of a whole tab from the states of its video areas: live if any area is live."""
    if not states or "live" in states:
        return "live"
    return "break" if "break" in states else "over"
