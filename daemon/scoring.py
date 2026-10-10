"""Decides when a secondary stream should be promoted to the main slot.

Pure logic: no network, no clock of its own (callers pass `now` in seconds), so it is easy to test.
Streams are keyed by tab id, so a stream's weight follows it when tabs move between screen slots.

Score of a stream = priority * max(0, sum of weighted signals):
  surge   how far recent loudness is above this stream's own long-term average (0 = normal)
  crowd   0..1 probability of cheering / crowd / applause
  speech  0..1 probability of speech (commentary)
  music   0..1 probability of music (ads, jingles)
  event   0..1 freshness of a score change read from the picture (1 = just now)
"""
import math
from collections import deque
from dataclasses import dataclass, field

MIN_BASELINE = 0.002  # floor so near-silence cannot make the surge explode
STATUS_MULT = {"live": 1.0, "break": 0.25, "over": 0.0}  # how much a stream counts in each game state


@dataclass
class Weights:
    surge: float = 0.3
    crowd: float = 1.0
    speech: float = -0.2
    music: float = -0.5
    event: float = 0.8    # a fresh score change (goal, touchdown) read from the picture


@dataclass
class Config:
    weights: Weights = field(default_factory=Weights)
    threshold: float = 0.40   # challenger must beat main by this fraction...
    min_gap: float = 0.15     # ...and by at least this much in absolute score
    min_score: float = 0.20   # a challenger scoring below this never wins
    window_s: float = 3.0     # loudness is averaged over this many seconds
    baseline_s: float = 60.0  # time constant of each stream's long-term loudness average
    warmup_s: float = 20.0    # a stream cannot win until its baseline has had this long to settle
    surge_cap: float = 3.0    # limit on the surge signal
    event_s: float = 45.0     # a score change counts for this long, fading linearly
    min_dwell_s: float = 20.0 # after a swap, wait this long before allowing another one
    stale_s: float = 5.0      # a stream with no audio for this long is ignored


@dataclass
class Swap:
    promote: int  # tab id moving to the main slot
    demote: int   # tab id leaving the main slot
    reason: str = ""


@dataclass
class _Stream:
    weight: float = 1.0
    samples: deque = field(default_factory=deque)  # (time, level)
    baseline: float | None = None
    first_seen: float | None = None
    last_sample: float | None = None
    tags: tuple[float, float, float] = (0.0, 0.0, 0.0)  # crowd, speech, music
    tags_at: float = float("-inf")
    event_at: float = float("-inf")  # when a score change was last seen
    override: str | None = None  # status chosen by the user (live/break/over), None = automatic
    auto_state: str = "live"     # status detected from the picture


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

    def add_audio(self, tab_id: int, level: float, now: float) -> None:
        stream = self.streams.get(tab_id)
        if stream is None:
            return
        stream.samples.append((now, level))
        self._trim(stream, now)
        if stream.baseline is None:
            stream.baseline, stream.first_seen = level, now
        else:
            alpha = 1 - math.exp(-(now - stream.last_sample) / self.config.baseline_s)
            stream.baseline += alpha * (level - stream.baseline)
        stream.last_sample = now

    def set_tags(self, tab_id: int, crowd: float, speech: float, music: float, now: float) -> None:
        stream = self.streams.get(tab_id)
        if stream is not None:
            stream.tags, stream.tags_at = (crowd, speech, music), now

    def set_event(self, tab_id: int, now: float) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].event_at = now

    def set_status(self, tab_id: int, override: str | None) -> None:
        if tab_id in self.streams:
            self.streams[tab_id].override = override if override in STATUS_MULT else None

    def set_auto_state(self, tab_id: int, state: str) -> None:
        if tab_id in self.streams and state in STATUS_MULT:
            self.streams[tab_id].auto_state = state

    def components(self, tab_id: int, now: float) -> dict | None:
        """The signals and final score of a stream, or None if it has no recent audio or is still warming up."""
        cfg = self.config
        stream = self.streams.get(tab_id)
        if stream is None:
            return None
        self._trim(stream, now)
        if not stream.samples or now - stream.last_sample > cfg.stale_s:
            return None
        if now - stream.first_seen < cfg.warmup_s:
            return None

        level = sum(v for _, v in stream.samples) / len(stream.samples)
        surge = min(max(level / max(stream.baseline, MIN_BASELINE) - 1.0, 0.0), cfg.surge_cap)
        fresh = now - stream.tags_at <= cfg.stale_s
        crowd, speech, music = stream.tags if fresh else (0.0, 0.0, 0.0)

        event = max(0.0, 1.0 - (now - stream.event_at) / cfg.event_s)
        w = cfg.weights
        raw = w.surge * surge + w.crowd * crowd + w.speech * speech + w.music * music + w.event * event
        status = stream.override or stream.auto_state
        return {"score": max(raw, 0.0) * stream.weight * STATUS_MULT[status], "surge": surge, "crowd": crowd,
                "speech": speech, "music": music, "event": event, "level": level, "status": status}

    def score(self, tab_id: int, now: float) -> float | None:
        c = self.components(tab_id, now)
        return None if c is None else c["score"]

    def decide(self, now: float) -> Swap | None:
        cfg = self.config
        if self.frozen or self.main not in self.streams:
            return None
        if now - self._last_swap < cfg.min_dwell_s:
            return None

        main_c = self.components(self.main, now)
        main_score = main_c["score"] if main_c else 0.0
        best_id, best_c = None, None
        for tab_id in self.streams:
            if tab_id == self.main:
                continue
            c = self.components(tab_id, now)
            if c is not None and (best_c is None or c["score"] > best_c["score"]):
                best_id, best_c = tab_id, c

        if best_c is None or best_c["score"] < cfg.min_score:
            return None
        if best_c["score"] < max(main_score * (1 + cfg.threshold), main_score + cfg.min_gap):
            return None

        fmt = lambda c: " ".join(f"{k}={c[k]:.2f}" for k in ("score", "surge", "crowd", "speech", "music", "event"))
        reason = f"challenger [{fmt(best_c)}] vs main [{fmt(main_c) if main_c else 'no data'}]"
        swap = Swap(promote=best_id, demote=self.main, reason=reason)
        self.main = best_id
        self._last_swap = now
        return swap

    def _trim(self, stream: _Stream, now: float) -> None:
        cutoff = now - self.config.window_s
        while stream.samples and stream.samples[0][0] < cutoff:
            stream.samples.popleft()
