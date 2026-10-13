"""Remembers the build-up of a stream: the cues of the last seconds ADDED together, not only the strongest one.

The scorer keeps one cue per stream (the strongest, fading over `anticipation_s`), so a free kick, then a chance, then a shot on the
post count as one cue. A real build-up is a SEQUENCE: each sign alone is small, together they say a goal may come. `BuildUp` keeps every
cue of a stream for `window_s` seconds and reports their faded sum plus the stream's current excitement (loudness surge, crowd) as a
"build-up level", on the scale of a stream's score so it can be compared with MAIN's score.

LIVE for football since 2026-10-12 (`scoring.buildup_live`, 0 = log only; `Scorer.take_main` applies the swap guards): the server logs "buildup would take MAIN" when a stream that is not MAIN reaches the level and the gap, so the
log can be compared with the real goals (scripts/backtest.py). It changes no score; it moves MAIN only through `Scorer.take_main`. Pure logic, callers pass `now`.
"""
import dataclasses


def is_soccer(sport: str | None) -> bool:
    """Soccer is "football" (lowercase, as the tabs name it); an unknown sport is treated like soccer, as for the spoken cues
    (`cues.speech_cue_weight`)."""
    return (sport or "").strip().lower() in ("football", "")


def moves_main(sport: str | None) -> bool:
    """Only a tab DETECTED (or chosen) as football may be moved to MAIN by the tracker; an unknown sport never is."""
    return (sport or "").strip().lower() == "football"


@dataclasses.dataclass
class _Cue:
    at: float
    value: float
    kind: str = "cue"   # what made it (for the learning data): a banner cue name, "spoken:<cue>", "note:<cue>", "surge_note", "crowd_note"


class BuildUp:
    def __init__(self, window_s: float = 45.0, cooldown_s: float = 30.0):
        self.window_s, self.cooldown_s = window_s, cooldown_s
        self._cues: dict[int, list[_Cue]] = {}
        self._fired_at: dict[int, float] = {}
        self._surge_noted_at: dict[int, float] = {}
        self._crowd_run: dict[int, int] = {}          # consecutive readings with the crowd at or above the minimum
        self._crowd_noted_at: dict[int, float] = {}
        self._blocked_logged_at: dict[int, float] = {}

    def note(self, tab_id: int, now: float, value: float, kind: str = "cue") -> None:
        """A cue of this weight (before leverage) was seen on the stream. `kind` only labels it in the learning data."""
        if value > 0:
            self._cues.setdefault(tab_id, []).append(_Cue(now, value, kind))

    def _stores(self):
        return (self._cues, self._fired_at, self._surge_noted_at, self._crowd_run, self._crowd_noted_at, self._blocked_logged_at)

    def remove(self, tab_id: int) -> None:
        """Forget a tab: its own key and, for a multiview, the key of every tile `(tab_id, index)` (a stream is keyed by the tab id, or by
        `(tab id, video index)` when the tab has several videos: each tile has its own cues, cooldowns and notes)."""
        for store in self._stores():
            for key in [k for k in store if k == tab_id or (isinstance(k, tuple) and k[0] == tab_id)]:
                del store[key]

    def swap(self, a, b) -> None:
        """Two streams change places (DAZN's expand moves the clicked video into the big slot): their cues and cooldowns follow."""
        for store in self._stores():
            moved_a, moved_b = store.pop(a, None), store.pop(b, None)
            if moved_b is not None:
                store[a] = moved_b
            if moved_a is not None:
                store[b] = moved_a

    def cues(self, tab_id: int, now: float) -> list[tuple[float, float]]:
        """(age in seconds, faded value) of the cues still in the window, oldest first. A cue fades linearly to 0 over the window."""
        kept = [c for c in self._cues.get(tab_id, []) if 0 <= now - c.at < self.window_s]
        self._cues[tab_id] = kept
        return [(now - c.at, c.value * (1.0 - (now - c.at) / self.window_s)) for c in kept]

    def cue_details(self, tab_id: int, now: float) -> list[tuple[float, float, str]]:
        """Like `cues`, with the kind of each cue: (age, faded value, kind)."""
        kept = [c for c in self._cues.get(tab_id, []) if 0 <= now - c.at < self.window_s]
        self._cues[tab_id] = kept
        return [(now - c.at, c.value * (1.0 - (now - c.at) / self.window_s), c.kind) for c in kept]

    def state(self, tab_id: int, now: float) -> dict:
        """Guard state for the learning data (read only): seconds of cooldown left after a build-up swap / surge note / crowd note, and the
        consecutive loud-crowd readings so far."""
        left = lambda at: round(max(0.0, self.cooldown_s - (now - at)), 1) if at != float("-inf") else 0.0
        return {"cooldown_left": left(self._fired_at.get(tab_id, float("-inf"))), "surge_note_left": left(self._surge_noted_at.get(tab_id, float("-inf"))),
                "crowd_note_left": left(self._crowd_noted_at.get(tab_id, float("-inf"))), "crowd_run": self._crowd_run.get(tab_id, 0)}

    def level(self, tab_id: int, now: float, support: float) -> float:
        """The faded sum of the cues of the window plus `support` (the stream's surge / crowd part of its score). Support alone is no
        build-up: it counts only while at least one cue is in the window, like the scorer's rule that a cue needs a sign of excitement."""
        faded = [v for _, v in self.cues(tab_id, now)]
        return sum(faded) + max(support, 0.0) if faded else 0.0

    def surge_note_due(self, tab_id: int, now: float, surge: float, min_surge: float) -> bool:
        """True (and the cooldown starts) when `surge` reaches `min_surge` and no surge note was made for this stream within `cooldown_s`.
        The caller then notes a small pseudo-cue; a loud crowd moment before a goal is a build-up sign, but not a swap reason by itself."""
        if surge < min_surge or now - self._surge_noted_at.get(tab_id, float("-inf")) < self.cooldown_s:
            return False
        self._surge_noted_at[tab_id] = now
        return True

    def crowd_note_due(self, tab_id: int, now: float, crowd: float, min_crowd: float, readings: int = 2) -> bool:
        """True (and the cooldown starts) when the crowd probability was at least `min_crowd` on `readings` CONSECUTIVE calls (one call per
        score reading; a lower reading resets the count) and no crowd note was made for this stream within `cooldown_s`. The caller then
        notes a small pseudo-cue: a crowd that keeps building is a sign, a single roar is not."""
        if crowd < min_crowd:
            self._crowd_run[tab_id] = 0
            return False
        run = self._crowd_run[tab_id] = self._crowd_run.get(tab_id, 0) + 1
        if run < readings or now - self._crowd_noted_at.get(tab_id, float("-inf")) < self.cooldown_s:
            return False
        self._crowd_noted_at[tab_id] = now
        return True

    def would_take(self, tab_id: int, now: float, support: float, main_score: float, min_level: float, min_gap: float,
                   start_cooldown: bool = True, main_level: float = 0.0) -> float | None:
        """The level when the stream reaches `min_level` and is `min_gap` above MAIN's score or MAIN's own build-up level `main_level`,
        whichever is higher (and was not reported within `cooldown_s`), else None. Reporting it starts the cooldown, unless `start_cooldown` is False: then the caller calls `mark_fired` itself once the
        swap really happened, so a swap that a guard stopped (dwell, hold, ad...) can be tried again on the next reading."""
        level = self.level(tab_id, now, support)
        if level < min_level or level - max(main_score, main_level) < min_gap:
            return None
        if now - self._fired_at.get(tab_id, float("-inf")) < self.cooldown_s:
            return None
        if start_cooldown:
            self._fired_at[tab_id] = now
        return level

    def spoken_age(self, tab_id: int, now: float) -> float | None:
        """Age in seconds of the youngest SPOKEN cue (kind 'spoken:...') of the stream in the window, else None."""
        ages = [now - c.at for c in self._cues.get(tab_id, []) if c.kind.startswith("spoken:") and 0 <= now - c.at < self.window_s]
        return min(ages) if ages else None
    def newest_cue(self, tab_id: int, now: float, min_value: float, max_age: float) -> tuple[float, float] | None:
        """(age, weight before fading) of the youngest cue of the stream that weighs at least `min_value` and is younger than `max_age`
        (and than the window), else None."""
        best = None
        for c in self._cues.get(tab_id, []):
            age = now - c.at
            if 0 <= age < min(max_age, self.window_s) and c.value >= min_value and (best is None or age < best[0]):
                best = (age, c.value)
        return best

    def main_hold(self, main_tab: int | None, tab_id: int, now: float, level: float, main_level: float, cfg) -> str | None:
        """Why MAIN keeps its seat against the challenger `tab_id` with build-up `level` (None = it does not). `main_level` is MAIN's own
        build-up level. (1) A review cue on MAIN (weight >= cfg.buildup_review_min, younger than cfg.buildup_review_hold_s): the challenger
        needs cfg.buildup_review_margin above main_level. (2) Any cue on MAIN of weight >= cfg.buildup_hold_cue_min younger than
        cfg.buildup_hold_s: it needs cfg.buildup_hold_margin above - unless the challenger has a review cue of its own that young (a VAR
        check on the challenger beats a chance on MAIN). The challenger's goal is not handled here (the normal swap rule takes it)."""
        if main_tab is None:
            return None
        if cfg.buildup_review_hold_s > 0:
            r = self.newest_cue(main_tab, now, cfg.buildup_review_min, cfg.buildup_review_hold_s)
            if r is not None and level < main_level + cfg.buildup_review_margin:
                return f"MAIN under review (cue {r[0]:.0f} s ago): level {level:.2f} < {main_level + cfg.buildup_review_margin:.2f}"
        if cfg.buildup_hold_s > 0:
            c = self.newest_cue(main_tab, now, cfg.buildup_hold_cue_min, cfg.buildup_hold_s)
            if c is not None and level < main_level + cfg.buildup_hold_margin:
                if self.newest_cue(tab_id, now, cfg.buildup_review_min, cfg.buildup_hold_s) is not None:
                    return None
                return f"MAIN's own cue {c[0]:.0f} s ago: level {level:.2f} < {main_level + cfg.buildup_hold_margin:.2f}"
        return None

    def mark_fired(self, tab_id: int, now: float) -> None:
        """Start the cooldown of this stream (see `would_take`)."""
        self._fired_at[tab_id] = now

    def blocked_log_due(self, tab_id: int, now: float, every_s: float = 10.0) -> bool:
        """True (and the timer starts) at most once per `every_s` per stream: for the 'not moved: why' line of a build-up that a guard
        stopped and that may fire again on every reading."""
        if now - self._blocked_logged_at.get(tab_id, float("-inf")) < every_s:
            return False
        self._blocked_logged_at[tab_id] = now
        return True
