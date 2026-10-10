"""WebSocket server: receives audio levels from the Edge extension, sends back swap commands.

Messages are JSON, one object per message.

Extension -> daemon:
  {"type": "register",   "tab_id": 12, "weight": 1.0, "is_main": false, "sport": "Football", "status": "auto|live|break|over", "role": "LEFT", "allowed": ["MAIN", "LEFT"]|null,
                "teams": "Paris SG vs Lyon", "favourites": [{"name": "PSG", "mult": 2}]}
  {"type": "unregister", "tab_id": 12}
  {"type": "audio",      "tab_id": 12, "rms": 0.031}   (simple loudness-only form)
  {"type": "set_weight", "tab_id": 12, "weight": 1.5}
  {"type": "freeze",     "value": true}
  {"type": "video_rect", "tab_id": 12, "rects": [{"left":0,"top":0,"width":1920,"height":1080}], "visible": true, "fullscreen": false}
  {"type": "focus_tile", "tab_id": 12, "index": 2}   (daemon -> extension: expand that video tile)
  {"type": "arrange", "moves": [{"tab_id": 12, "role": "MAIN"}, ...]}  (daemon -> extension: put each tab in that slot's window)
  binary frame: 4-byte little-endian tab id + 32 kHz mono int16 samples (100 ms per frame)

Daemon -> extension:
  {"type": "swap", "promote": 12, "demote": 7}
"""
import asyncio
import json
import logging
import struct
import time

import numpy as np
from websockets.asyncio.server import serve

from daemon.arrange import plan_swap
from daemon.favourites import match_multiplier
from daemon.game_state import GameTracker, combine
from daemon.multiview import TileFocuser
from daemon.score_watch import ScoreWatcher
from daemon.sport_detect import SportDetector
from daemon.screen_ocr import find_clocks, grab
from daemon.scoring import Scorer

HOST, PORT = "127.0.0.1", 8765
DECIDE_INTERVAL_S = 0.25
SCORE_LOG_INTERVAL_S = 2.0
CLASSIFY_INTERVAL_S = 1.0
OCR_INTERVAL_S = 2.0
FAVOURITE_MEMORY_S = 60.0  # a favourite read on screen keeps counting for this long
OCR_MIN_WIDTH = 900  # smaller areas (multiview tiles) are enlarged before reading
SAMPLE_RATE = 32000
CLASSIFY_WINDOW = 3 * SAMPLE_RATE  # classify the last 3 seconds of each tab
CROWD_CLASSES = ["Cheering", "Crowd", "Applause"]

log = logging.getLogger("daemon")


class Server:
    def __init__(self, scorer: Scorer | None = None, classifier=None, screen_reader=None, focuser: TileFocuser | None = None):
        self.scorer = scorer or Scorer()
        self.classifier = classifier
        self.screen_reader = screen_reader
        self.ocr: dict[tuple[int, int], dict] = {}  # (tab id, video index) -> latest OCR reading
        self.sports: dict[int, str | None] = {}     # tab id -> sport label chosen in the popup
        self.favourites: list[dict] = []            # user's favourite teams [{name, mult}]
        self.team_text: dict[int, str] = {}         # tab id -> teams typed in the popup
        self.fav_seen: dict[tuple[int, int], tuple[float, float]] = {}  # (tab, video) -> (multiplier, when seen on screen)
        self.roles: dict[int, str] = {}             # tab id -> slot its window currently has
        self.allowed: dict[int, set[str] | None] = {}  # tab id -> slots it may use (None = any)
        self.trackers: dict[tuple[int, int], GameTracker] = {}  # (tab id, video index) -> game state
        self.watchers: dict[tuple[int, int], ScoreWatcher] = {}  # (tab id, video index) -> score-change detector
        self.event_at: dict[tuple[int, int], float] = {}        # (tab id, video index) -> last score change
        self.sport_detectors: dict[tuple[int, int], SportDetector] = {}  # (tab id, video index) -> sport guess
        self.sport_seen: dict[tuple[int, int], str | None] = {}          # last guess, to log changes only
        self.focuser = focuser or TileFocuser()
        self.clients = set()
        self.buffers: dict[int, np.ndarray] = {}  # tab id -> last 3 s of audio
        self.rects: dict[int, dict] = {}          # tab id -> where its playing videos are on screen
        self.tags: dict[int, np.ndarray] = {}     # tab id -> latest classifier probabilities

    def handle_message(self, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "register":
            self.scorer.register(msg["tab_id"], msg.get("weight", 1.0), msg.get("is_main", False))
            tab_id = msg["tab_id"]
            if msg.get("role"):
                self.roles[tab_id] = msg["role"]
            else:
                self.roles.pop(tab_id, None)
            allowed = msg.get("allowed")
            self.allowed[tab_id] = set(allowed) if allowed else None
            self.scorer.set_can_main(tab_id, self.allowed[tab_id] is None or "MAIN" in self.allowed[tab_id])
            self.sports[tab_id] = msg.get("sport") or None
            if isinstance(msg.get("favourites"), list):
                self.favourites = msg["favourites"]
            self.team_text[tab_id] = msg.get("teams") or ""
            self._update_favourite(tab_id, time.monotonic())
            status = msg.get("status", "auto")
            self.scorer.set_status(tab_id, None if status == "auto" else status)
            for (t, _), tracker in self.trackers.items():
                if t == tab_id:
                    tracker.set_sport(self.sports[tab_id])
            log.info("registered tab %s (sport=%s, status=%s)", tab_id, self.sports[tab_id], status)
        elif kind == "unregister":
            self.scorer.remove(msg["tab_id"])
            self.buffers.pop(msg["tab_id"], None)
            self.rects.pop(msg["tab_id"], None)
            for store in (self.ocr, self.trackers, self.watchers, self.event_at, self.sport_detectors, self.sport_seen):
                for key in [k for k in store if k[0] == msg["tab_id"]]:
                    del store[key]
            self.sports.pop(msg["tab_id"], None)
            self.team_text.pop(msg["tab_id"], None)
            for key in [k for k in self.fav_seen if k[0] == msg["tab_id"]]:
                del self.fav_seen[key]
            self.roles.pop(msg["tab_id"], None)
            self.allowed.pop(msg["tab_id"], None)
            self.tags.pop(msg["tab_id"], None)
            log.info("unregistered tab %s", msg["tab_id"])
        elif kind == "audio":
            self.scorer.add_audio(msg["tab_id"], float(msg["rms"]), time.monotonic())
        elif kind == "video_rect":
            tab_id = msg["tab_id"]
            info = {"rects": msg.get("rects") or [], "visible": bool(msg.get("visible")), "fullscreen": bool(msg.get("fullscreen"))}
            if self.rects.get(tab_id) != info:
                log.info("video area tab %s: %s", tab_id, info)
            self.rects[tab_id] = info
        elif kind == "set_weight":
            self.scorer.set_weight(msg["tab_id"], float(msg["weight"]))
        elif kind == "freeze":
            self.scorer.frozen = bool(msg["value"])
            log.info("freeze %s", "ON: no swaps or tile changes" if self.scorer.frozen else "OFF")
        else:
            log.warning("unknown message type: %r", kind)

    def handle_audio(self, raw: bytes) -> None:
        """Binary frame: 4-byte little-endian tab id, then 16-bit little-endian mono samples."""
        tab_id = struct.unpack_from("<I", raw)[0]
        samples = np.frombuffer(raw, dtype="<i2", offset=4).astype(np.float32) / 32768.0
        if samples.size == 0:
            return
        rms = float(np.sqrt(np.mean(samples * samples)))
        self.scorer.add_audio(tab_id, rms, time.monotonic())
        if self.classifier is not None:
            old = self.buffers.get(tab_id)
            buf = samples if old is None else np.concatenate([old, samples])
            self.buffers[tab_id] = buf[-CLASSIFY_WINDOW:]

    async def _client(self, ws) -> None:
        self.clients.add(ws)
        log.info("extension connected (%d client(s))", len(self.clients))
        try:
            async for raw in ws:
                try:
                    if isinstance(raw, bytes):
                        self.handle_audio(raw)
                    else:
                        self.handle_message(json.loads(raw))
                except (ValueError, KeyError, TypeError, struct.error) as e:
                    log.warning("bad message (%d bytes): %s", len(raw), e)
        finally:
            self.clients.discard(ws)
            log.info("extension disconnected")

    def _log_scores(self, now: float) -> None:
        for tab_id in self.scorer.streams:
            tag = "MAIN " if tab_id == self.scorer.main else ""
            c = self.scorer.components(tab_id, now)
            if c is None:
                log.info("  %s%s: warming up or no audio", tag, tab_id)
                continue
            text = (f"{tag}{tab_id}: {c['status']} fav={c['fav']:.1f} score={c['score']:.2f} surge={c['surge']:.2f} crowd={c['crowd']:.2f} "
                    f"speech={c['speech']:.2f} music={c['music']:.2f}")
            probs = self.tags.get(tab_id)
            if probs is not None:
                text += " top=" + ",".join(f"{n}:{p:.2f}" for n, p in self.classifier.top(probs, 3))
            log.info("  %s", text)

    async def _classify_loop(self) -> None:
        while True:
            await asyncio.sleep(CLASSIFY_INTERVAL_S)
            ready = [t for t, b in self.buffers.items() if len(b) >= CLASSIFY_WINDOW]
            if not ready:
                continue
            batch = np.stack([self.buffers[t] for t in ready])
            probs = await asyncio.to_thread(self.classifier.predict, batch)
            idx = self.classifier.index
            now = time.monotonic()
            for tab_id, p in zip(ready, probs):
                self.tags[tab_id] = p
                crowd = float(max(p[idx[name]] for name in CROWD_CLASSES))
                self.scorer.set_tags(tab_id, crowd, float(p[idx["Speech"]]), float(p[idx["Music"]]), now)

    def _ocr_pass(self, jobs: list[tuple[int, int, dict]]) -> dict:
        """Runs in a worker thread: screenshot each video area and read its text."""
        from PIL import Image

        results = {}
        for tab_id, index, rect in jobs:
            image = grab(rect)
            if image.shape[1] < OCR_MIN_WIDTH:
                factor = OCR_MIN_WIDTH / image.shape[1]
                size = (OCR_MIN_WIDTH, int(image.shape[0] * factor))
                image = np.asarray(Image.fromarray(image).resize(size, Image.LANCZOS))
            texts = [text for text, _ in self.screen_reader.read(image)]
            results[(tab_id, index)] = {"texts": texts, "clocks": find_clocks(texts)}
        return results

    def _update_favourite(self, tab_id: int, now: float) -> None:
        """A favourite counts if its name is in the tab's teams field or was read on screen in the last minute."""
        mult = match_multiplier([self.team_text.get(tab_id, "")], self.favourites)
        for (t, _), (m, seen_at) in self.fav_seen.items():
            if t == tab_id and now - seen_at <= FAVOURITE_MEMORY_S:
                mult = max(mult, m)
        self.scorer.set_favourite(tab_id, mult)

    def _update_game_states(self, results: dict, now: float) -> None:
        per_tab: dict[int, list[str]] = {}
        for (tab_id, index), r in results.items():
            tracker = self.trackers.setdefault((tab_id, index), GameTracker(self.sports.get(tab_id)))
            detected = self.sport_detectors.setdefault((tab_id, index), SportDetector()).update(r["texts"], r["clocks"])
            tracker.set_sport(self.sports.get(tab_id) or detected)  # a sport chosen in the popup wins
            if detected != self.sport_seen.get((tab_id, index)):
                log.info("sport detected tab %s tile %s: %s", tab_id, index, detected or "unknown")
                self.sport_seen[(tab_id, index)] = detected
            per_tab.setdefault(tab_id, []).append(tracker.update(now, r["texts"], r["clocks"]))
            seen = match_multiplier(r["texts"], self.favourites)
            if seen > 1.0:
                self.fav_seen[(tab_id, index)] = (seen, now)
            if self.watchers.setdefault((tab_id, index), ScoreWatcher()).update(r["texts"], r["clocks"], now):
                self.event_at[(tab_id, index)] = now
                self.scorer.set_event(tab_id, now)
                log.info("score change tab %s tile %s: %s", tab_id, index, r["texts"][:8])
        for tab_id, states in per_tab.items():
            self._update_favourite(tab_id, now)
            state = combine(states)
            stream = self.scorer.streams.get(tab_id)
            if stream is not None and stream.auto_state != state:
                log.info("game state tab %s: %s -> %s", tab_id, stream.auto_state, state)
            self.scorer.set_auto_state(tab_id, state)

    def _focus_commands(self, now: float) -> list[dict]:
        """For multiview tabs: which tile should have the sound?"""
        commands = []
        for tab_id, info in self.rects.items():
            if not info["visible"] or len(info["rects"]) < 2:
                continue
            if self.scorer.frozen and tab_id == self.scorer.main:
                continue  # MAIN is locked: leave which tile it shows alone
            tiles = []
            for pos, rect in enumerate(info["rects"][:4]):
                index = rect.get("index", pos)
                tracker = self.trackers.get((tab_id, index))
                age = now - self.event_at.get((tab_id, index), float("-inf"))
                tile = {"index": index, "audible": bool(rect.get("audible")),
                        "state": tracker.state if tracker else "live",
                        "event": max(0.0, 1.0 - age / self.scorer.config.event_s)}
                if tile["audible"]:
                    c = self.scorer.components(tab_id, now)
                    tile["crowd"] = c["crowd"] if c else 0.0
                tiles.append(tile)
            target = self.focuser.decide(tab_id, now, tiles)
            if target is not None:
                log.info("focus tile: tab %s -> tile %s | %s", tab_id, target, tiles)
                commands.append({"type": "focus_tile", "tab_id": tab_id, "index": target})
                self._expand_swap(tab_id, info["rects"], target)
        return commands

    def _expand_swap(self, tab_id: int, rects: list[dict], target: int) -> None:
        """DAZN's expand moves the clicked stream into the big main slot and the old main stream into the
        clicked slot, so the per-tile records must change places too (main + sidebar layout only)."""
        areas = sorted(((r["width"] * r["height"], r.get("index", pos)) for pos, r in enumerate(rects)), reverse=True)
        if len(areas) < 2 or areas[0][0] < 1.8 * areas[1][0]:
            return  # equal-size grid: nothing moves
        main = areas[0][1]
        if target == main:
            return
        for store in (self.trackers, self.watchers, self.event_at, self.ocr, self.sport_detectors, self.sport_seen):
            a, b = (tab_id, target), (tab_id, main)
            moved_a, moved_b = store.pop(a, None), store.pop(b, None)
            if moved_b is not None:
                store[a] = moved_b
            if moved_a is not None:
                store[b] = moved_a

    async def _broadcast(self, obj: dict) -> None:
        text = json.dumps(obj)
        for ws in list(self.clients):
            await ws.send(text)

    async def _ocr_loop(self) -> None:
        while True:
            await asyncio.sleep(OCR_INTERVAL_S)
            jobs = [
                (tab_id, i, rect)
                for tab_id, info in list(self.rects.items())
                if info["visible"]
                for i, rect in ((r.get("index", pos), r) for pos, r in enumerate(info["rects"][:4]))
            ]
            if not jobs:
                continue
            try:
                results = await asyncio.to_thread(self._ocr_pass, jobs)
            except Exception as e:  # e.g. window moved off-screen mid-capture
                log.warning("ocr pass failed: %s", e)
                continue
            self.ocr.update(results)
            self._update_game_states(results, time.monotonic())
            for command in self._focus_commands(time.monotonic()):
                await self._broadcast(command)
            for (tab_id, i), r in results.items():
                log.info("  ocr tab %s[%d]: clocks=%s texts=%s", tab_id, i, r["clocks"], r["texts"][:10])

    def _next_swap(self, now: float):
        """Normal swap into MAIN; while MAIN is frozen, a swap into the LEFT window instead."""
        if not self.scorer.frozen:
            return self.scorer.decide(now)
        left = next((t for t, role in self.roles.items() if role == "LEFT" and t != self.scorer.main), None)
        return self.scorer.decide(now, target=left) if left is not None else None

    async def _decide_loop(self) -> None:
        last_log = 0.0
        while True:
            await asyncio.sleep(DECIDE_INTERVAL_S)
            now = time.monotonic()
            if now - last_log >= SCORE_LOG_INTERVAL_S:
                self._log_scores(now)
                last_log = now
            swap = self._next_swap(now)
            if swap:
                log.info("swap: promote tab %d, demote tab %d | %s", swap.promote, swap.demote, swap.reason)
                moves = plan_swap(swap.promote, swap.demote, self.roles, self.allowed)
                if moves is None:
                    log.info("swap refused (allowed slots or unknown slots): promote %s, demote %s", swap.promote, swap.demote)
                    if not self.scorer.frozen:
                        self.scorer.main = swap.demote  # nothing moved, so the old MAIN stays MAIN
                    continue
                log.info("arrange: %s", moves)
                await self._broadcast({"type": "arrange", "moves": [{"tab_id": t, "role": r} for t, r in moves]})

    async def run(self, host: str = HOST, port: int = PORT) -> None:
        async with serve(self._client, host, port):
            log.info("listening on ws://%s:%d", host, port)
            loops = [self._decide_loop()]
            if self.classifier is not None:
                loops.append(self._classify_loop())
            if self.screen_reader is not None:
                loops.append(self._ocr_loop())
            await asyncio.gather(*loops)


if __name__ == "__main__":
    from daemon.logsetup import setup_logging

    setup_logging()
    from daemon.audio_classifier import AudioClassifier

    from daemon.screen_ocr import ScreenReader

    from daemon.settings import load_settings

    settings = load_settings()
    classifier = AudioClassifier()
    log.info("audio classifier ready on %s", classifier.device)
    screen_reader = ScreenReader()
    log.info("screen reader ready")
    try:
        asyncio.run(Server(Scorer(settings.scoring), classifier=classifier, screen_reader=screen_reader,
                           focuser=TileFocuser(settings.focus)).run())
    except KeyboardInterrupt:
        pass
