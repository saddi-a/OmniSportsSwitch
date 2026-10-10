"""Guesses the sport of a video area from what its scoreboard shows.

Cues (OCR text is noisy, so evidence accumulates over many readings and fades slowly):
  * American football: "1ST & 10", "2nd & GOAL" (a short ordinal-like word, '&', a distance), "DOWN"
  * Soccer: a game clock that RUNS up beyond 20:00 (no other supported sport does) and stoppage time "45+2".
    A static time such as the kickoff text "Match started at 20:45" is not a clock, and "halftime" exists in
    every sport, so neither counts.
  * Basketball: "Q1".."Q4", "FOULS", "BONUS", "TIMEOUTS"
The names match the sport dropdown in the extension and the profiles in game_state.
"""
import re

FOOTBALL = "American football"
SOCCER = "Football"
BASKETBALL = "Basketball"

DOWN_DISTANCE_RE = re.compile(r"^\S{2,4}\s*&\s*(\d{1,2}|goal)$", re.IGNORECASE)   # "1ST & 10", "2no & 10"
DOWN_WORD_RE = re.compile(r"\bdown\b", re.IGNORECASE)
QUARTER_RE = re.compile(r"^(q[1-4]|[1-4]q)$", re.IGNORECASE)
BASKETBALL_WORDS_RE = re.compile(r"\b(fouls?|bonus|timeouts?)\b", re.IGNORECASE)
STOPPAGE_RE = re.compile(r"^\d{2}\s?\+\s?\d{1,2}$")                               # "45+2"
CLOCK_RE = re.compile(r"^(\d{1,3}):(\d{2})$")

DECAY = 0.995          # per reading (about 3 s): evidence halves in roughly four minutes
MIN_EVIDENCE = 6.0     # the winner needs at least this much...
MIN_RATIO = 1.5        # ...and this many times the runner-up
MAX_STEP_S = 15        # a running clock moves by at most this between two readings


class SportDetector:
    def __init__(self):
        self._evidence = {FOOTBALL: 0.0, SOCCER: 0.0, BASKETBALL: 0.0}
        self._previous_clocks: list[int] = []

    def update(self, texts: list[str], clocks: list[str]) -> str | None:
        for sport in self._evidence:
            self._evidence[sport] *= DECAY
        ev = self._evidence

        for text in (t.strip() for t in texts):
            if DOWN_DISTANCE_RE.match(text):
                ev[FOOTBALL] += 3
            elif DOWN_WORD_RE.search(text):
                ev[FOOTBALL] += 1
            if QUARTER_RE.match(text):
                ev[BASKETBALL] += 2
            if BASKETBALL_WORDS_RE.search(text):
                ev[BASKETBALL] += 1
            if STOPPAGE_RE.match(text):
                ev[SOCCER] += 4                                    # rare and decisive

        seconds = [int(m.group(1)) * 60 + int(m.group(2)) for c in clocks if (m := CLOCK_RE.match(c))]
        if seconds:
            # A clock only counts if it continues one from the previous reading (OCR may list a stray static
            # time first, so every clock is compared with every earlier one).
            steps = [(now, before) for now in seconds for before in self._previous_clocks
                     if 0 < abs(now - before) <= MAX_STEP_S]
            if steps:
                now, before = steps[0]
                if now > before:                                   # counting up
                    ev[SOCCER] += 3 if 20 * 60 < now < 130 * 60 else 0.5
                else:                                              # counting down: football or basketball
                    ev[FOOTBALL] += 0.25
                    ev[BASKETBALL] += 0.25
            self._previous_clocks = seconds
        return self.sport

    @property
    def sport(self) -> str | None:
        ranked = sorted(self._evidence.items(), key=lambda kv: kv[1], reverse=True)
        (best, best_score), (_, second_score) = ranked[0], ranked[1]
        if best_score >= MIN_EVIDENCE and best_score >= MIN_RATIO * second_score:
            return best
        return None
