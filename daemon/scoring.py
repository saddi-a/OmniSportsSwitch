"""Decides when a secondary stream should be promoted to the main slot.

Pure logic: no network, no clock of its own (callers pass `now` in seconds), so it is easy to test.
Streams are keyed by tab id, so a stream's weight follows it when tabs move between screen slots.
"""
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Config:
    threshold: float = 0.40   # challenger must beat main by this fraction (0.40 = +40%)
    window_s: float = 3.0     # audio is averaged over this many seconds to ignore single blips
    min_dwell_s: float = 20.0 # after a swap, wait this long before allowing another one
    stale_s: float = 5.0      # a stream with no audio for this long is ignored
    min_score: float = 0.01   # a challenger quieter than this never wins (avoids swapping on silence)


@dataclass
class Swap:
    promote: int  # tab id moving to the main slot
    demote: int   # tab id leaving the main slot


@dataclass
class _Stream:
    weight: float = 1.0
    samples: deque = field(default_factory=deque)  # (time, rms)


class Scorer:
    def __init__(self, config: Config | None = None):
        self.config = config or Config()
        self.streams: dict[int, _Stream] = {}
        self.main: int | None = None
        self.frozen = False
        self._last_swap = float("-inf")

    def register(self, tab_id: int, weight: float = 1.0, is_main: bool = False) -> None:
        self.streams.setdefault(tab_id, _Stream()).weight = weight
        if is_main:
            self.main = tab_id

    def remove(self, tab_id: int) -> None:
        self.streams.pop(tab_id, None)
        if self.main == tab_id:
            self.main = None

    def set_weight(self, tab_id: int, weight: float) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].weight = weight

    def add_audio(self, tab_id: int, rms: float, now: float) -> None:
        stream = self.streams.get(tab_id)
        if stream is None:
            return
        stream.samples.append((now, rms))
        self._trim(stream, now)

    def score(self, tab_id: int, now: float) -> float | None:
        """Weighted average RMS over the window, or None if the stream has no recent audio."""
        stream = self.streams.get(tab_id)
        if stream is None:
            return None
        self._trim(stream, now)
        if not stream.samples or now - stream.samples[-1][0] > self.config.stale_s:
            return None
        mean = sum(r for _, r in stream.samples) / len(stream.samples)
        return mean * stream.weight

    def decide(self, now: float) -> Swap | None:
        cfg = self.config
        if self.frozen or self.main not in self.streams:
            return None
        if now - self._last_swap < cfg.min_dwell_s:
            return None

        main_score = self.score(self.main, now) or 0.0
        best_id, best_score = None, 0.0
        for tab_id in self.streams:
            if tab_id == self.main:
                continue
            s = self.score(tab_id, now)
            if s is not None and s > best_score:
                best_id, best_score = tab_id, s

        if best_id is None or best_score < cfg.min_score:
            return None
        if best_score <= main_score * (1 + cfg.threshold):
            return None

        swap = Swap(promote=best_id, demote=self.main)
        self.main = best_id
        self._last_swap = now
        return swap

    def _trim(self, stream: _Stream, now: float) -> None:
        cutoff = now - self.config.window_s
        while stream.samples and stream.samples[0][0] < cutoff:
            stream.samples.popleft()
