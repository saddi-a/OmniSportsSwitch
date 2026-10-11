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
import datetime
import json
import logging
import struct
import time

import numpy as np
from websockets.asyncio.server import serve

from daemon.arrange import plan_fix, plan_swap
from daemon.favourites import match_multiplier, match_slots
from daemon.feed import LEAGUES, FeedRecorder, FeedSession
from daemon.feed_gate import FeedGate
from daemon.feed_map import FeedMapper
from daemon.logsetup import LOG_DIR
from daemon.sport_choice import choose_sport, sport_from_feed
from daemon.sport_memory import SportMemory
from daemon.leverage import tab_leverage
from daemon.game_state import GameTracker, combine
from daemon.multiview import TileFocuser
from daemon.score_watch import ScoreWatcher, find_score, find_score_geo
from daemon.sport_detect import SportDetector
from daemon.screen_ocr import find_clocks, grab, merge_zoom
from daemon.semantic import level as semantic_level
from daemon.cues import SpeechMemory, cue_weight
from daemon.speech import find_speech_cues
from daemon.scoring import SHOUT_CLASSES, Scorer

HOST, PORT = "127.0.0.1", 8765
DECIDE_INTERVAL_S = 0.25
SCORE_LOG_INTERVAL_S = 2.0
CLASSIFY_INTERVAL_S = 1.0
OCR_INTERVAL_S = 2.0
ZOOM_RETRY_S = 6.0         # the zoom pass looks again this often on a tile where it found no score
DIGIT_RETRY_S = 6.0       # the digit fallback looks again this often on a tile where it found nothing
FIX_INTERVAL_S = 10.0     # how often tabs sitting in a slot they may not use are corrected
EXPAND_CHECK_S = 8.0   # how long to wait for the next video report to confirm an expand
FAVOURITE_MEMORY_S = 60.0
RULE_MEMORY_S = 600.0     # slot rules are remembered longer: a team stays on its tab through ads and breaks  # a favourite read on screen keeps counting for this long
OCR_MIN_WIDTH = 900  # smaller areas (multiview tiles) are enlarged before reading
SAMPLE_RATE = 32000
CLASSIFY_WINDOW = 3 * SAMPLE_RATE  # classify the last 3 seconds of each tab
CROWD_CLASSES = ["Cheering", "Crowd", "Applause"]

log = logging.getLogger("daemon")
boxes_log = logging.getLogger("daemon.boxes")


def leveling_gain(baseline: float | None, target: float, max_gain: float) -> float:
    """Factor that brings a stream's normal loudness to `target` before the sound classifier hears it, so a quietly
    mixed broadcast is judged like a loud one. It follows the slow baseline, not the last seconds, so a roar still
    sounds louder than normal. 1.0 when off (target 0) or while nothing is known."""
    if target <= 0 or baseline is None or baseline <= 0:
        return 1.0
    return min(max(target / baseline, 1.0 / max_gain), max_gain)


class Server:
    def __init__(self, scorer: Scorer | None = None, classifier=None, screen_reader=None, focuser: TileFocuser | None = None, settings=None,
                 speech=None, semantic=None, sport_memory_path=None):
        self.scorer = scorer or Scorer()
        self.speech = speech                    # SpeechListener or None: the commentary listener (measuring only)
        self.semantic = semantic                # SemanticMatcher or None: how close the commentary is to big-moment phrases (measuring only)
        self.speech_buffers: dict[int, np.ndarray] = {}   # tab id -> last speech_window_s seconds of audio
        self.speech_cue_at: dict[tuple[int, str], float] = {}  # (tab, cue) -> when it was last logged
        self.settings = settings  # daemon.settings.Settings; the popup edits it live
        self.feed: FeedSession | None = None   # outside game data (measuring only); started by run() when feed_mode > 0
        self.feed_map = FeedMapper()   # which feed game each tab/tile shows (measuring only)
        self.feed_gate = FeedGate()   # shadow clock gate: 'would fire' lines (measuring only)
        self.ui_clients = set()   # sockets of the popup: they get settings replies, not arrange commands
        self.classifier = classifier
        self.screen_reader = screen_reader
        self.ocr: dict[tuple[int, int], dict] = {}  # (tab id, video index) -> latest OCR reading
        self.sports: dict[int, str | None] = {}     # tab id -> sport label chosen in the popup
        self.favourites: list[dict] = []            # user's favourite teams [{name, mult}]
        self.team_text: dict[int, str] = {}         # tab id -> teams typed in the popup
        self.fav_seen: dict[tuple[int, int], tuple[float, float]] = {}  # (tab, video) -> (multiplier, when seen on screen)
        self.roles: dict[int, str] = {}             # tab id -> slot its window currently has
        self.allowed: dict[int, set[str] | None] = {}  # tab id -> slots it may use (None = any), as ticked on the tab
        self.limits: dict[int, set[str] | None] = {}   # tab id -> slots it may really use: tab choice + team rules
        self.digit_try_at: dict[tuple[int, int], float] = {}  # (tab, video) -> when the digit fallback last looked
        self.zoom_try_at: dict[tuple[int, int], float] = {}   # (tab, video) -> when the zoom pass last looked
        self.zoom_found: set[tuple[int, int]] = set()           # tiles where the zoom pass currently finds the score
        self.digit_found: set[tuple[int, int]] = set()          # tiles where the digit fallback currently finds the score
        self.rule_seen: dict[tuple[int, int], tuple[set[str], float]] = {}  # (tab, video) -> (slots of a team rule, when seen)
        self.trackers: dict[tuple[int, int], GameTracker] = {}  # (tab id, video index) -> game state
        self.watchers: dict[tuple[int, int], ScoreWatcher] = {}  # (tab id, video index) -> score-change detector
        self.event_at: dict[tuple[int, int], float] = {}        # (tab id, video index) -> last score change
        self.sport_detectors: dict[tuple[int, int], SportDetector] = {}  # (tab id, video index) -> sport guess
        self.sport_seen: dict[tuple[int, int], str | None] = {}          # last guess, to log changes only
        self.cue_speech = SpeechMemory(self.scorer.config.cue_speech_window_s)   # review subjects the commentary named lately, per tab
        self.sport_choice_seen: dict[tuple[int, int], tuple] = {}   # last (sport, source) per tile, to log changes only
        self.sport_memory = SportMemory(sport_memory_path if sport_memory_path is not None else LOG_DIR / "sports.json")         # last sport seen per tab: a start guess after a restart
        self.focuser = focuser or TileFocuser()
        self.pending_expand: dict[int, tuple[int, float]] = {}  # tab id -> (tile asked for, deadline): waiting to see the result
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
            self.sports[tab_id] = msg.get("sport") or None
            if isinstance(msg.get("favourites"), list):
                self.favourites = msg["favourites"]
            self.team_text[tab_id] = msg.get("teams") or ""
            self.feed_map.set_text(tab_id, self.team_text[tab_id])
            self._update_favourite(tab_id, time.monotonic())
            status = msg.get("status", "auto")
            self.scorer.set_status(tab_id, None if status == "auto" else status)
            for (t, _), tracker in self.trackers.items():
                if t == tab_id:
                    tracker.set_sport(self.sports[tab_id])
            log.info("registered tab %s (sport=%s, status=%s, %d team(s), limits=%s)", tab_id, self.sports[tab_id], status,
                     len(self.favourites), self.limits.get(tab_id))
        elif kind == "unregister":
            self.scorer.remove(msg["tab_id"])
            self.buffers.pop(msg["tab_id"], None)
            self.speech_buffers.pop(msg["tab_id"], None)
            self.rects.pop(msg["tab_id"], None)
            for store in (self.ocr, self.trackers, self.watchers, self.event_at, self.sport_detectors, self.sport_seen):
                for key in [k for k in store if k[0] == msg["tab_id"]]:
                    del store[key]
            self.sports.pop(msg["tab_id"], None)
            self.team_text.pop(msg["tab_id"], None)
            self.feed_map.forget(msg["tab_id"])
            self.feed_gate.forget(msg["tab_id"])
            self.digit_found = {k for k in self.digit_found if k[0] != msg["tab_id"]}
            self.zoom_found = {k for k in self.zoom_found if k[0] != msg["tab_id"]}
            for seen in (self.fav_seen, self.rule_seen, self.digit_try_at):
                for key in [k for k in seen if k[0] == msg["tab_id"]]:
                    del seen[key]
            self.roles.pop(msg["tab_id"], None)
            self.allowed.pop(msg["tab_id"], None)
            self.limits.pop(msg["tab_id"], None)
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
            self._check_expand(tab_id, info)
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
        if self.speech is not None:
            old = self.speech_buffers.get(tab_id)
            buf = samples if old is None else np.concatenate([old, samples])
            self.speech_buffers[tab_id] = buf[-int(self.scorer.config.speech_window_s * SAMPLE_RATE):]

    def handle_settings_message(self, msg: dict) -> dict | None:
        """The popup's settings requests. Returns the reply, or None if the message is not about settings."""
        from daemon import settings as settings_module

        kind = msg.get("type")
        if kind not in ("get_settings", "set_settings", "reset_settings"):
            return None
        if self.settings is None:
            return {"type": "settings", "error": "settings are not available in this daemon"}
        errors = {}
        if kind == "set_settings" and isinstance(msg.get("changes"), dict):
            errors = settings_module.apply_changes(self.settings, msg["changes"])
            log.info("settings changed from the popup: %s%s", msg["changes"], f" (refused: {errors})" if errors else "")
        elif kind == "reset_settings":
            settings_module.reset_all(self.settings)
            log.info("settings reset to the defaults from the popup")
        return {"type": "settings", "groups": settings_module.describe(self.settings), "errors": errors}

    async def handle_control_message(self, msg: dict) -> dict | None:
        """The popup's manual control: put a stream on MAIN and hold it there, release the hold, ask for its state."""
        kind = msg.get("type")
        if kind not in ("force_main", "release_hold", "get_hold"):
            return None
        now, error = time.monotonic(), None
        if kind == "force_main":
            try:
                tab_id = int(msg["tab_id"])
                swap = self.scorer.force_main(tab_id, now)
                if swap is not None:
                    log.info("swap: promote tab %d, demote tab %d | %s", swap.promote, swap.demote, swap.reason)
                    if not await self._perform_swap(swap, restore_main=True):
                        self.scorer.release_hold()
                        error = "this stream cannot go to Main (slot rules or windows not set)"
                if error is None:
                    log.info("MAIN forced to tab %s by the user, held for %.0f s", tab_id, self.scorer.hold_left(now))
            except (ValueError, KeyError, TypeError) as e:
                error = str(e) or "bad request"
        elif kind == "release_hold":
            self.scorer.release_hold()
            log.info("hold on MAIN released by the user")
        return {"type": "hold", "tab_id": self.scorer.hold_tab, "left_s": round(self.scorer.hold_left(now)), "error": error}

    async def _perform_swap(self, swap, restore_main: bool) -> bool:
        """Arrange the windows for a swap the scorer decided on. False if the slot rules refuse it."""
        moves = plan_swap(swap.promote, swap.demote, self.roles, self.limits)
        if moves is None:
            log.info("swap refused (allowed slots or unknown slots): promote %s, demote %s", swap.promote, swap.demote)
            if restore_main:
                self.scorer.main = swap.demote  # nothing moved, so the old MAIN stays MAIN
            return False
        log.info("arrange: %s", moves)
        await self._broadcast({"type": "arrange", "moves": [{"tab_id": t, "role": r} for t, r in moves]})
        return True

    async def _client(self, ws) -> None:
        self.clients.add(ws)
        log.info("extension connected (%d client(s))", len(self.clients))
        try:
            async for raw in ws:
                try:
                    if isinstance(raw, bytes):
                        self.handle_audio(raw)
                    else:
                        msg = json.loads(raw)
                        reply = self.handle_settings_message(msg) or await self.handle_control_message(msg)
                        if reply is not None:
                            self.ui_clients.add(ws)
                            await ws.send(json.dumps(reply))
                        else:
                            self.handle_message(msg)
                except (ValueError, KeyError, TypeError, struct.error) as e:
                    log.warning("bad message (%d bytes): %s", len(raw), e)
        finally:
            self.clients.discard(ws)
            self.ui_clients.discard(ws)
            log.info("extension disconnected")

    def _log_scores(self, now: float) -> None:
        for tab_id in self.scorer.streams:
            tag = "MAIN " if tab_id == self.scorer.main else ""
            c = self.scorer.components(tab_id, now)
            if c is None:
                log.info("  %s%s: warming up or no audio", tag, tab_id)
                continue
            text = (f"{tag}{tab_id}: {'AD' if c['ad'] else c['status']} fav={c['fav']:.1f} score={c['score']:.2f} surge={c['surge']:.2f} crowd={c['crowd']:.2f} "
                    f"speech={c['speech']:.2f} music={c['music']:.2f} shout={c['shout']:.2f} lev={c['lev']:.1f}")
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
            cfg = self.scorer.config
            batch = np.stack([self.buffers[t] * leveling_gain(self.scorer.baseline_of(t), cfg.classifier_target_rms,
                                                              cfg.classifier_max_gain) for t in ready])
            np.clip(batch, -1.0, 1.0, out=batch)
            probs = await asyncio.to_thread(self.classifier.predict, batch)
            idx = self.classifier.index
            now = time.monotonic()
            for tab_id, p in zip(ready, probs):
                self.tags[tab_id] = p
                crowd = float(max(p[idx[name]] for name in CROWD_CLASSES))
                shout = float(max(p[idx[name]] for name in SHOUT_CLASSES))
                self.scorer.set_tags(tab_id, crowd, float(p[idx["Speech"]]), float(p[idx["Music"]]), now, shout=shout)

    async def _feed_loop(self) -> None:
        """Measuring only: record ESPN's public scoreboards (and football plays) to logs/feed and log what changes."""
        if self.feed is None:
            self.feed = FeedSession(self.scorer.config, FeedRecorder(LOG_DIR / "feed"))
        while True:
            await asyncio.sleep(1.0)
            try:
                notes = await asyncio.to_thread(self.feed.step, time.monotonic(), datetime.datetime.now().astimezone())
            except Exception as e:   # the feed must never hurt the daemon
                log.warning("feed step failed: %s: %s", type(e).__name__, e)
                continue
            for message in self.feed.messages:
                log.warning("%s", message)
            self.feed.messages.clear()
            try:
                self.feed_map.set_games(self.feed.games())
            except Exception as e:   # mapping is measuring only too
                log.warning("feed map update failed: %s: %s", type(e).__name__, e)
            for n in notes:
                if n.event is not None:
                    self.feed_gate.add(n.event, n.snap)
                log.info("feed %s %s %s [%s] %s", n.kind, n.snap.league, n.snap.label, n.snap.display or n.snap.status,
                         f"{n.event.kind} {n.event.display} {n.event.team}: {n.event.text}" if n.event else n.text)

    async def _speech_loop(self) -> None:
        """Measuring only: write down what the commentators say, and which cues (penalty, corner, red zone...) it holds."""
        while True:
            cfg = self.scorer.config
            await asyncio.sleep(max(cfg.speech_interval_s, 1.0))
            if cfg.speech_interval_s <= 0:
                continue
            need = int(0.6 * cfg.speech_window_s * SAMPLE_RATE)
            for tab_id, buf in list(self.speech_buffers.items()):
                if len(buf) < need:
                    continue
                text, language = await asyncio.to_thread(self.speech.transcribe, buf.copy())
                if not text:
                    continue
                log.info("speech heard tab %s [%s]: %s", tab_id, language, text)
                now = time.monotonic()
                if self.semantic is not None:
                    match = await asyncio.to_thread(self.semantic.match, text)
                    tier = semantic_level(match, cfg.semantic_min_sim, cfg.semantic_margin, cfg.semantic_mid_sim)
                    log.info("semantic heard tab %s: high=%.2f mid=%.2f low=%.2f best='%s'", tab_id, match["high"], match["mid"],
                             match["low"], match["phrase"])
                    if tier and now - self.speech_cue_at.get((tab_id, "semantic " + tier), float("-inf")) >= cfg.speech_cue_cooldown_s:
                        self.speech_cue_at[(tab_id, "semantic " + tier)] = now
                        log.info("semantic %s tab %s: high=%.2f mid=%.2f low=%.2f best='%s' piece='%s'", tier, tab_id, match["high"],
                                 match["mid"], match["low"], match["phrase"], match["piece"])
                cues = find_speech_cues(text)
                self.cue_speech.window_s = cfg.cue_speech_window_s
                self.cue_speech.record(tab_id, cues, text, self._tab_sport(tab_id), now)
                for cue in cues:
                    if now - self.speech_cue_at.get((tab_id, cue), float("-inf")) >= cfg.speech_cue_cooldown_s:
                        self.speech_cue_at[(tab_id, cue)] = now
                        log.info("speech cue %s tab %s: %s", cue, tab_id, text)

    def _tab_sport(self, tab_id: int) -> str | None:
        """The sport of a tab, lowercase: the popup choice, else the first one detected on any of its tiles."""
        sport = self.sports.get(tab_id) or next((s for (t, _), s in self.sport_seen.items() if t == tab_id and s), None)
        return sport.lower() if sport else None

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
            items = self.screen_reader.read_items(image)
            texts = [item["text"] for item in items]
            if hasattr(self.screen_reader, "zoom_pass") and find_score_geo(items, find_clocks(texts)) is None:
                # Small digits next to three-letter team codes are missed at normal size: read the strip around the clock
                # again at twice the size (not on every pass when nothing is there; it costs GPU time).
                key, now_s = (tab_id, index), time.monotonic()
                if now_s - self.zoom_try_at.get(key, float("-inf")) >= ZOOM_RETRY_S or key in self.zoom_found:
                    zoom = self.screen_reader.zoom_pass(image, items)
                    self.zoom_try_at[key] = now_s
                    if zoom is not None:
                        merged = merge_zoom(items, *zoom)
                        if find_score_geo(merged, find_clocks([it["text"] for it in merged])) is not None:
                            if key not in self.zoom_found:
                                log.info("zoom pass tab %s tile %s: score found", tab_id, index)
                            self.zoom_found.add(key)
                            items, texts = merged, [it["text"] for it in merged]
                        else:
                            self.zoom_found.discard(key)
            if hasattr(self.screen_reader, "read_digits_beside_names") and find_score_geo(items, find_clocks(texts)) is None:
                # The big score digits of some scoreboards are not read at all. Look right beside the team names, but
                # not on every pass when nothing is there (it costs GPU time).
                key, now_s = (tab_id, index), time.monotonic()
                if now_s - self.digit_try_at.get(key, float("-inf")) >= DIGIT_RETRY_S or key in self.digit_found:
                    extra = self.screen_reader.read_digits_beside_names(image, items)
                    self.digit_try_at[key] = now_s
                    if extra:
                        if key not in self.digit_found:
                            log.info("digit fallback tab %s tile %s: %s", tab_id, index, [it["text"] for it in extra])
                        self.digit_found.add(key)
                        items, texts = items + extra, texts + [it["text"] for it in extra]
                    else:
                        self.digit_found.discard(key)
            results[(tab_id, index)] = {"texts": texts, "clocks": find_clocks(texts), "items": items}
        return results

    def _update_favourite(self, tab_id: int, now: float) -> None:
        """A favourite counts if its name is in the tab's teams field or was read on screen in the last minute.
        The same goes for team rules that limit the slots a tab may use."""
        mult = match_multiplier([self.team_text.get(tab_id, "")], self.favourites)
        for (t, _), (m, seen_at) in self.fav_seen.items():
            if t == tab_id and now - seen_at <= FAVOURITE_MEMORY_S:
                mult = max(mult, m)
        self.scorer.set_favourite(tab_id, mult)
        rule = match_slots([self.team_text.get(tab_id, "")], self.favourites)
        for (t, _), (slots, seen_at) in self.rule_seen.items():
            if t == tab_id and now - seen_at <= RULE_MEMORY_S:
                rule = slots if rule is None else ((rule & slots) or rule)
        tab = self.allowed.get(tab_id)
        limit = tab if rule is None else (rule if tab is None else ((tab & rule) or tab))  # a clash keeps the tab's own choice
        self.limits[tab_id] = limit
        self.scorer.set_can_main(tab_id, limit is None or "MAIN" in limit)

    def _update_game_states(self, results: dict, now: float) -> None:
        per_tab: dict[int, list[str]] = {}
        ads: dict[int, list[bool]] = {}
        for (tab_id, index), r in results.items():
            tracker = self.trackers.setdefault((tab_id, index), GameTracker(self.sports.get(tab_id), self.scorer.config))
            detected = self.sport_detectors.setdefault((tab_id, index), SportDetector()).update(r["texts"], r["clocks"])
            det = self.sport_detectors[(tab_id, index)]
            now_wall = time.time()
            mapping = self.feed_map.mapping(tab_id, index)
            feed_sport = sport_from_feed(LEAGUES[mapping.league][0]) if mapping and mapping.league in LEAGUES else None
            # popup choice > hard cue > feed league > tab title (stage C) > league words on screen > remembered sport
            sport, source = choose_sport(popup=self.sports.get(tab_id), hard=detected, feed=feed_sport, title=None,
                                         weak=det.weak_sport, memory=self.sport_memory.get(tab_id, now_wall))
            tracker.set_sport(sport)
            if source in ("hard", "feed"):   # only reliable sources are remembered
                self.sport_memory.remember(tab_id, sport, now_wall)
            self.sport_memory.save()
            if (sport, source) != self.sport_choice_seen.get((tab_id, index)):
                self.sport_choice_seen[(tab_id, index)] = (sport, source)
                log.info("sport %s tab %s tile %s via %s", sport or "unknown", tab_id, index, source or "none")
            if detected != self.sport_seen.get((tab_id, index)):
                log.info("sport detected tab %s tile %s: %s", tab_id, index, detected or "unknown")
                self.sport_seen[(tab_id, index)] = detected
            per_tab.setdefault(tab_id, []).append(tracker.update(now, r["texts"], r["clocks"]))
            if tracker.scorebug_note:   # measuring only: DAZN hides the scorebug during replays
                kind, secs = tracker.scorebug_note
                log.info("scorebug %s tab %s tile %s: %.0f s", kind, tab_id, index, secs)
            if tracker.clock_replay_note:   # measuring only unless scoring.replay_by_clock_s > 0
                kind, secs = tracker.clock_replay_note
                log.info("replay by clock tab %s tile %s: %s", tab_id, index, f"-{secs:.0f} s" if kind == "start" else f"ended after {secs:.0f} s")
            for cue in tracker.anticipation:
                banner = tracker.anticipation_text.get(cue, cue)
                weight = cue_weight(cue, banner, self.cue_speech.tier(tab_id, now), tracker.sport or None, self.scorer.config.weights)
                log.info("anticipation %s tab %s tile %s weight %.2f", cue, tab_id, index, weight)
                self.scorer.set_anticipation(tab_id, now, weight)
            ads.setdefault(tab_id, []).append(tracker.on_ad)
            if tracker.big_play:
                strength = self.scorer.config.big_play_strength
                self.event_at[(tab_id, index)] = max(self.event_at.get((tab_id, index), float("-inf")),
                                                     now - (1.0 - strength) * self.scorer.config.event_s)
                self.scorer.set_event(tab_id, now, strength)
                log.info("big play tab %s tile %s: %s", tab_id, index, tracker.big_play)
            seen = match_multiplier(r["texts"], self.favourites)
            if seen > 1.0:
                self.fav_seen[(tab_id, index)] = (seen, now)
            rule = match_slots(r["texts"], self.favourites)
            if rule:
                self.rule_seen[(tab_id, index)] = (rule, now)
            watcher = self.watchers.setdefault((tab_id, index), ScoreWatcher(self.scorer.config))
            changed = watcher.update(r["texts"], r["clocks"], now, r.get("items"), replay=tracker.replay,
                                     sport=self.sports.get(tab_id) or self.sport_seen.get((tab_id, index)))
            for line in self.feed_map.observe(tab_id, index, r["texts"], watcher.score, r["clocks"]):
                log.info("%s", line)
            mapped = self.feed_map.mapping(tab_id, index)
            self.feed_gate.lead_s = self.scorer.config.feed_lead_s
            for line in self.feed_gate.observe(tab_id, index, mapped.game_id if mapped else None, r["clocks"], tracker.period, tracker.replay, now):
                log.info("%s", line)
            if changed and per_tab[tab_id][-1] != "live":
                log.info("score change ignored on tab %s tile %s: the game is %s", tab_id, index, per_tab[tab_id][-1])   # results tickers, countdowns
            elif changed:
                for line in self.feed_gate.real_change(tab_id, index, now):
                    log.info("%s", line)
                strength = watcher.strength
                self.event_at[(tab_id, index)] = now - (1.0 - strength) * self.scorer.config.event_s
                self.scorer.set_event(tab_id, now, strength)
                log.info("score change tab %s tile %s: %s", tab_id, index, r["texts"][:8])
        for tab_id, states in per_tab.items():
            self._update_favourite(tab_id, now)
            self.scorer.set_on_ad(tab_id, all(ads.get(tab_id, [False])), now)   # the whole tab is on an ad
            self.scorer.set_replay(tab_id, any(self.trackers[key].replay for key in results if key[0] == tab_id))
            state = combine(states)
            stream = self.scorer.streams.get(tab_id)
            if stream is not None and stream.auto_state != state:
                log.info("game state tab %s: %s -> %s", tab_id, stream.auto_state, state)
            self.scorer.set_auto_state(tab_id, state)
            tiles = []   # closeness and time left of the best game in this tab
            for (t, index), watcher in self.watchers.items():
                if t != tab_id:
                    continue
                tracker = self.trackers.get((t, index))
                sport = self.sports.get(t) or self.sport_seen.get((t, index))
                tiles.append((sport, watcher.score, tracker.period if tracker else None,
                              tracker.clock_minute if tracker else None))
            self.scorer.set_leverage(tab_id, tab_leverage(tiles, self.scorer.config))

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
                        "ad": bool(tracker and tracker.on_ad),
                        "event": max(0.0, 1.0 - age / self.scorer.config.event_s)}
                if tile["audible"]:
                    c = self.scorer.components(tab_id, now)
                    tile["crowd"] = c["crowd"] if c else 0.0
                tiles.append(tile)
            target = self.focuser.decide(tab_id, now, tiles)
            if target is not None:
                log.info("focus tile: tab %s -> tile %s | %s", tab_id, target, tiles)
                commands.append({"type": "focus_tile", "tab_id": tab_id, "index": target})
                self.pending_expand[tab_id] = (target, now + EXPAND_CHECK_S)
        return commands

    def _check_expand(self, tab_id: int, info: dict) -> None:
        """After a tile was asked to expand, look at where the sound went. Slots keep their size and the streams
        move between them, so an expand puts the sound on the big (main) slot, while a click that only selected the
        tile leaves the sound on the tile that was clicked. Records are swapped only for a confirmed expand."""
        pending = self.pending_expand.get(tab_id)
        rects = info.get("rects") or []
        if pending is None or len(rects) < 2:
            return
        target, deadline = pending
        areas = sorted(((r["width"] * r["height"], r.get("index", pos)) for pos, r in enumerate(rects)), reverse=True)
        main = areas[0][1]
        audible = next((r.get("index", pos) for pos, r in enumerate(rects) if r.get("audible")), None)
        if audible == target and target != main:
            log.warning("expand of tab %s tile %s did not happen: the tile was only selected", tab_id, target)
            del self.pending_expand[tab_id]
        elif audible == main or time.monotonic() > deadline:
            del self.pending_expand[tab_id]
            if audible == main:
                self._expand_swap(tab_id, rects, target)

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
        for ws in list(self.clients - self.ui_clients):
            try:
                await ws.send(text)
            except Exception as e:  # a socket closing must not stop the decision loop
                log.warning("could not send to a client: %s", e)

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
                log.info("  ocr tab %s[%d]: clocks=%s texts=%s", tab_id, i, r["clocks"], r["texts"][:40])
                boxes_log.info("tab %s[%d] %s", tab_id, i,
                               [(it["text"], it["y1"] - it["y0"], it["x0"], it["y0"]) for it in r["items"][:40]])

    def _next_swap(self, now: float):
        """Normal swap into MAIN; while MAIN is frozen, a swap into the LEFT window instead."""
        if not self.scorer.frozen:
            return self.scorer.decide(now)
        left = next((t for t, role in self.roles.items() if role == "LEFT" and t != self.scorer.main), None)
        return self.scorer.decide(now, target=left) if left is not None else None

    def _ensure_main(self) -> None:
        """Safety net: with no MAIN stream the scorer decides nothing at all. If the windows say which tab sits in the
        MAIN window, adopt it."""
        if self.scorer.main in self.scorer.streams:
            return
        tab = next((t for t, role in self.roles.items() if role == "MAIN" and t in self.scorer.streams), None)
        if tab is not None:
            log.warning("no MAIN stream: adopting tab %s, the one in the MAIN window", tab)
            self.scorer.main = tab

    async def _decide_loop(self) -> None:
        last_log = last_fix = 0.0
        while True:
            await asyncio.sleep(DECIDE_INTERVAL_S)
            now = time.monotonic()
            if now - last_log >= SCORE_LOG_INTERVAL_S:
                self._log_scores(now)
                last_log = now
            self._ensure_main()
            swap = self.scorer.evict_main(now) or self._next_swap(now)
            if not swap and now - last_fix >= FIX_INTERVAL_S and (moves := plan_fix(self.roles, self.limits)):
                last_fix = now
                log.info("arrange (slot rules): %s", moves)
                await self._broadcast({"type": "arrange", "moves": [{"tab_id": t, "role": r} for t, r in moves]})
            if swap:
                log.info("swap: promote tab %d, demote tab %d | %s", swap.promote, swap.demote, swap.reason)
                await self._perform_swap(swap, restore_main=not self.scorer.frozen)

    async def run(self, host: str = HOST, port: int = PORT) -> None:
        async with serve(self._client, host, port):
            log.info("listening on ws://%s:%d", host, port)
            loops = [self._decide_loop()]
            if self.classifier is not None:
                loops.append(self._classify_loop())
            if self.screen_reader is not None:
                loops.append(self._ocr_loop())
            if self.speech is not None:
                loops.append(self._speech_loop())
            if self.scorer.config.feed_mode > 0:   # needs a restart; mode 0 starts nothing
                loops.append(self._feed_loop())
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
    speech = None
    if settings.scoring.speech_interval_s > 0:   # the commentary listener is optional (measuring only)
        try:
            from daemon.speech import SpeechListener
            speech = SpeechListener()
            log.info("commentary listener ready on %s", speech.device)
        except Exception as error:   # package or model missing: the daemon runs as before
            log.warning("commentary listener not available (%s: %s); continuing without it", type(error).__name__, error)
    semantic = None
    if speech is not None:   # the semantic reading works on the listener's transcripts (measuring only)
        try:
            from daemon.semantic import SemanticMatcher
            semantic = SemanticMatcher()
            log.info("semantic matcher ready (cpu)")
        except Exception as error:
            log.warning("semantic matcher not available (%s: %s); continuing without it", type(error).__name__, error)
    try:
        asyncio.run(Server(Scorer(settings.scoring), classifier=classifier, screen_reader=screen_reader,
                           focuser=TileFocuser(settings.focus), settings=settings, speech=speech, semantic=semantic).run())
    except KeyboardInterrupt:
        pass
