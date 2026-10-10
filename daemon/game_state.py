"""Works out from OCR readings whether a stream is live, on a break, or over.

Pure logic: callers pass `now` in seconds and what OCR read, so it is easy to test.

Rules (see PLAN.md):
  * text like FINAL / FULL TIME marks the game over, HALF TIME / MI-TEMPS marks a break, immediately
  * a game clock that has not changed for longer than the sport allows means a break
  * a scorebug with no readable clock for too long means a break (ad, studio segment, replay package)
  * the player's own placeholder during an ad ("COMMERCIAL BREAK IN PROGRESS", "PUBLICITARIA EN CURSO" ...) means
    a break at once, and the countdown timer it shows is not a game clock
"""
import re
from dataclasses import dataclass

# A caption only counts when a whole OCR text item is exactly the keyword. Words inside a sentence do not
# (an ad saying "ready for the half time?" must not mark the game as on a break).
OVER_RE = re.compile(r"(FINA[LU1I](?:\s?[/|IlU1]?\s?\d?OT\d?)?|SCORE FINAL|FULL[\s-]?TIME|FT|FIN DU MATCH|MATCH TERMIN[EÉ]|TERMIN[EÉ])",
                     re.IGNORECASE)   # also "FINAL/OT", "FINAL/2OT" as OCR mangles them (FINAUOT, FINALIOT)
# Overtime (American football, basketball) has no game clock on the scoreboard, so a missing clock is not a break there.
OVERTIME_RE = re.compile(r"(\d?OT|.*(OVERTIME|PROLONGATION|TEMPS SUPPL[EÉ]MENTAIRE).*)", re.IGNORECASE)
OVERTIME_MEMORY_S = 30.0
BREAK_RE = re.compile(r"(HALF[\s-]?TIME|HT|MI[\s-]?TEMPS|MITEMPS|INTERVALLE)", re.IGNORECASE)
CAPTION_CONFIRMATIONS = 2  # a caption must show on this many consecutive readings
KEYWORD_MEMORY_S = 120.0  # a FINAL / HALF TIME caption counts for this long after it was last seen

# Phrases a streaming player shows during an ad break (matched inside a longer text item, lower case).
# Seen live: "COMMERCIAL BREAK IN PROGRESS", "PUBLICITARIA EN CURSO", "INTERVALO COMERCIAL", "PUBLICITE".
AD_PHRASES = ["commercial break", "publicitaria", "intervalo comercial", "publicité", "publicite",
              "ad break", "advertisement", "right back", "watch now", "ondazn"]
# Banners the broadcast flashes for a big play. A text item counts only if it is (nearly) just that phrase, so
# sentences and stat labels ("TURNOVERS") do not. Seen live: "TAKEAWAY!" after an interception.
BIG_PLAY_WORDS = ["takeaway", "interception", "intercepted", "fumble", "sack", "red card", "carton rouge", "penalty kick"]
BIG_PLAY_SLACK = 10     # extra characters allowed around the phrase in the same text item
BIG_PLAY_COOLDOWN_S = 20.0   # the same banner lingering on screen counts once
AD_CONFIRMATIONS = 2   # the phrase must show on this many consecutive readings
AD_MEMORY_S = 8.0      # a break placeholder counts this long after it was last seen


@dataclass(frozen=True)
class SportProfile:
    freeze_s: float    # clock unchanged for this long -> break
    no_clock_s: float  # no readable clock for this long -> break


PROFILES = {
    "football": SportProfile(freeze_s=20, no_clock_s=45),             # soccer: the clock only stops at half time / full time
    "american football": SportProfile(freeze_s=240, no_clock_s=90),   # clock stops after most plays and for TV timeouts
    "basketball": SportProfile(freeze_s=180, no_clock_s=60),          # clock stops on every foul and timeout
    "generic": SportProfile(freeze_s=120, no_clock_s=60),             # any other or unknown sport
}


def profile_for(sport: str | None) -> SportProfile:
    return PROFILES.get((sport or "").strip().lower(), PROFILES["generic"])


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
        self._overtime_seen = float("-inf")
        self._over_hits = 0
        self._break_hits = 0
        self._ad_hits = 0
        self._ad_seen = float("-inf")
        self.on_ad = False   # True while the player's ad placeholder is showing
        self.big_play = ""   # set on the one reading where a new big-play banner appears (else "")
        self._big_play_at = float("-inf")

    def set_sport(self, sport: str | None) -> None:
        self.profile = profile_for(sport)

    def update(self, now: float, texts: list[str], clocks: list[str]) -> str:
        if self._first_seen is None:
            self._first_seen = now

        items = [re.sub(r"[^\w\s-]", "", t).strip() for t in texts]
        self._over_hits = self._over_hits + 1 if any(OVER_RE.fullmatch(t) for t in items) else 0
        self._break_hits = self._break_hits + 1 if any(BREAK_RE.fullmatch(t) for t in items) else 0
        if any(OVERTIME_RE.fullmatch(t) for t in items):
            self._overtime_seen = now
        if self._over_hits >= CAPTION_CONFIRMATIONS:
            self._over_seen = now
        if self._break_hits >= CAPTION_CONFIRMATIONS:
            self._break_seen = now

        lowered = [t.casefold() for t in texts]
        self._ad_hits = self._ad_hits + 1 if any(p in t for t in lowered for p in AD_PHRASES) else 0
        if self._ad_hits >= AD_CONFIRMATIONS:
            self._ad_seen = now
        on_ad = now - self._ad_seen <= AD_MEMORY_S
        self.on_ad = on_ad

        self.big_play = ""
        if not on_ad and now - self._big_play_at >= BIG_PLAY_COOLDOWN_S:
            for text in texts:
                words = re.sub(r"[^a-zà-ÿ ]", "", text.casefold()).strip()
                hit = ""
                for phrase in BIG_PLAY_WORDS:
                    whole_word = re.search("(?<![a-zà-ÿ])" + re.escape(phrase) + "(?![a-zà-ÿ])", words)
                    if whole_word and len(words) <= len(phrase) + BIG_PLAY_SLACK:
                        hit = phrase
                        break
                if hit:
                    self.big_play, self._big_play_at = hit, now
                    break

        if clocks and not on_ad:  # the countdown shown during an ad is not the game clock
            if clocks[0] != self._clock:
                self._clock, self._clock_changed_at = clocks[0], now
            self._last_clock_seen = now

        if now - self._over_seen <= KEYWORD_MEMORY_S:
            self.state = "over"
        elif on_ad:
            self.state = "break"
        elif now - self._break_seen <= KEYWORD_MEMORY_S:
            self.state = "break"
        elif clocks and self._clock_changed_at is not None:
            frozen_for = now - self._clock_changed_at
            self.state = "break" if frozen_for > self.profile.freeze_s else "live"
        elif now - self._overtime_seen <= OVERTIME_MEMORY_S:
            self.state = "live"   # overtime: no game clock to read
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
