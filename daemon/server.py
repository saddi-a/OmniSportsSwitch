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
from daemon.feed_map import FeedMapper, parse_clock_s
from daemon.logsetup import LOG_DIR, archive_rotated, prune_archive
from daemon.recorder import DatasetRecorder, snapshot_rows
from daemon.sport_choice import choose_sport, sport_from_feed
from daemon.sport_detect import sport_from_title
from daemon.sport_memory import SportMemory
from daemon.leverage import tab_leverage
from daemon.game_state import GameTracker, combine
from daemon.multiview import TileFocuser
from daemon.score_watch import ScoreWatcher, find_score_geo
from daemon.sport_detect import SportDetector
from daemon.rapid_reader import RapidReader, needs_rescue
from daemon.screen_ocr import find_clocks, grab, merge_zoom
from daemon.semantic import level as semantic_level
from daemon.buildup import BuildUp, is_soccer, moves_main
from daemon.cues import GoalPhraseWatch, SpeechMemory, SurgeRiseWatch, surge_rise_weight, cue_weight, end_zone_factor, is_strong_cue, speech_cue_weight, tracker_note_weight
from daemon.speech import find_speech_cues
from daemon.scoring import SHOUT_CLASSES, Scorer, Swap

HOST, PORT = "127.0.0.1", 8765
DECIDE_INTERVAL_S = 0.25
SCORE_LOG_INTERVAL_S = 1.0   # the scoring tick: cue checks, build-up, surge rise. Was 2.0 until 2026-10-17: the rules that count readings (surge_rise_votes, CROWD_NOTE_READINGS) went 2 -> 3 to keep their 2 s hold
SNAPSHOT_INTERVAL_S = 1.0    # the score lines of daemon.log and the dataset rows (logging only, changes no decision)
MAINTENANCE_INTERVAL_S = 3600.0   # the log archive is completed and pruned this often (and once at the start)
CLASSIFY_INTERVAL_S = 1.0
OCR_INTERVAL_S = 2.0
GAP_RETRY_S = 1.5          # the gap pass (strip between two team codes) looks again this often on a tile where it found no score
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
                 speech=None, semantic=None, sport_memory_path=None, recorder: DatasetRecorder | None = None, rapid_reader=None):
        self.scorer = scorer or Scorer()
        self.scorer.sport_of = self._tab_sport   # per-sport settings overrides: the scorer asks which sport a stream is
        self.scorer.tile_sport_of = self.scoring_sport   # ...and, in a multiview, which sport each tile is
        self.scorer.audible_of = self._audible_index     # ...and which tile has the sound (the audio terms belong to it)
        self.speech = speech                    # SpeechListener or None: the commentary listener (measuring only)
        self.semantic = semantic                # SemanticMatcher or None: how close the commentary is to big-moment phrases (measuring only)
        self.speech_buffers: dict[int, np.ndarray] = {}   # tab id -> last speech_window_s seconds of audio
        self.speech_cue_at: dict[tuple[int, str], float] = {}  # (tab, cue) -> when it was last logged
        self.settings = settings  # daemon.settings.Settings; the popup edits it live
        self.recorder = recorder  # DatasetRecorder or None: rows and events for later learning (scoring.dataset_mode; tests pass None)
        self.feed: FeedSession | None = None   # outside game data (measuring only); started by run() when feed_mode > 0
        self.feed_map = FeedMapper()   # which feed game each tab/tile shows (measuring only)
        self.feed_gate = FeedGate()   # shadow clock gate: 'would fire' lines (measuring only)
        self.ui_clients = set()   # sockets of the popup: they get settings replies, not arrange commands
        self.classifier = classifier
        self.screen_reader = screen_reader
        self.rapid_reader = rapid_reader   # RapidReader or None: the second OCR reader (tests pass None)
        self.rapid_try_at: dict[tuple[int, int], float] = {}   # (tab, video) -> when the second reader last looked
        self.rapid_found: set[tuple[int, int]] = set()          # tiles where the second reader currently completes the reading
        self.ocr: dict[tuple[int, int], dict] = {}  # (tab id, video index) -> latest OCR reading
        self.sports: dict[int, str | None] = {}     # tab id -> sport label chosen in the popup
        self.favourites: list[dict] = []            # user's favourite teams [{name, mult}]
        self.team_text: dict[int, str] = {}         # tab id -> teams typed in the popup
        self.page_text: dict[int, str] = {}         # tab id -> what the page says about the game (media session, player header text)
        self.fav_seen: dict[tuple[int, int], tuple[float, float]] = {}  # (tab, video) -> (multiplier, when seen on screen)
        self.roles: dict[int, str] = {}             # tab id -> slot its window currently has
        self.allowed: dict[int, set[str] | None] = {}  # tab id -> slots it may use (None = any), as ticked on the tab
        self.limits: dict[int, set[str] | None] = {}   # tab id -> slots it may really use: tab choice + team rules
        self.digit_try_at: dict[tuple[int, int], float] = {}  # (tab, video) -> when the digit fallback last looked
        self.zoom_try_at: dict[tuple[int, int], float] = {}   # (tab, video) -> when the zoom pass last looked
        self.gap_try_at: dict[tuple[int, int], float] = {}    # (tab, video) -> when the gap pass last looked
        self.gap_found: set[tuple[int, int]] = set()            # tiles where the gap pass currently finds the score
        self.zoom_found: set[tuple[int, int]] = set()           # tiles where the zoom pass currently finds the score
        self.digit_found: set[tuple[int, int]] = set()          # tiles where the digit fallback currently finds the score
        self.rule_seen: dict[tuple[int, int], tuple[set[str], float]] = {}  # (tab, video) -> (slots of a team rule, when seen)
        self.trackers: dict[tuple[int, int], GameTracker] = {}  # (tab id, video index) -> game state
        self.watchers: dict[tuple[int, int], ScoreWatcher] = {}  # (tab id, video index) -> score-change detector
        self.event_at: dict[tuple[int, int], float] = {}        # (tab id, video index) -> last score change
        self.sport_detectors: dict[tuple[int, int], SportDetector] = {}  # (tab id, video index) -> sport guess
        self.sport_seen: dict[tuple[int, int], str | None] = {}          # last guess, to log changes only
        self.cue_speech = SpeechMemory(self.scorer.config.cue_speech_window_s)   # review subjects the commentary named lately, per tab
        self.tab_titles: dict[int, str] = {}   # tab id -> page title sent by the extension (league words are weak evidence of the sport)
        self.buildup = BuildUp()   # the cues of a soccer stream added up over a minute (measuring only: logs "buildup would take MAIN")
        self.surge_rise = SurgeRiseWatch()   # soccer: the crowd noise rising fast, a medium cue heard before the commentator's words
        self.goal_watch = GoalPhraseWatch()   # a goal phrase heard near a loud surge (measuring only)
        self.fed: dict[tuple[int, int], float] = {}   # (tab, tile) -> when a feed goal was counted there
        self.replay_signal: dict[tuple[int, int, str], bool] = {}   # (tab, tile, 'bug' / 'clock') -> scorebug hidden / clock shows a replay (for the post-goal hold)
        self.held_change: dict[tuple[int, int], tuple[float, float]] = {}   # (tab, tile) -> (strength, when): a score change read while the game looked like a break
        self.unconfirmed: dict[tuple[int, int], tuple[float, float]] = {}   # (tab, tile) -> (full strength, when): OCR score changes the feed has not confirmed
        self.sport_choice_seen: dict[tuple[int, int], tuple] = {}   # last (sport, source) per tile, to log changes only
        self.sport_memory = SportMemory(sport_memory_path if sport_memory_path is not None else LOG_DIR / "sports.json")         # last sport seen per tab: a start guess after a restart
        self.focuser = focuser or TileFocuser()
        self.pending_expand: dict[int, tuple[int, float]] = {}  # tab id -> (tile asked for, deadline): waiting to see the result
        self.clients = set()
        self.buffers: dict[int, np.ndarray] = {}  # tab id -> last 3 s of audio
        self.rects: dict[int, dict] = {}          # tab id -> where its playing videos are on screen
        self.tags: dict[int, np.ndarray] = {}     # tab id -> latest classifier probabilities
        self.audible_seen: dict[int, tuple[int, str | None]] = {}   # multiview tab id -> (tile that has the sound, its sport) at the last report

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
            self.tab_titles[tab_id] = msg.get("title") or ""
            self.feed_map.set_text(tab_id, self._feed_text(tab_id))
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
            self.buildup.remove(msg["tab_id"])
            self.cue_speech.remove(msg["tab_id"])
            self.surge_rise.remove(msg["tab_id"])
            self.buffers.pop(msg["tab_id"], None)
            self.speech_buffers.pop(msg["tab_id"], None)
            self.rects.pop(msg["tab_id"], None)
            self.audible_seen.pop(msg["tab_id"], None)
            for store in (self.ocr, self.trackers, self.watchers, self.event_at, self.replay_signal, self.sport_detectors, self.sport_seen,
                          self.sport_choice_seen, self.held_change, self.fed):
                for key in [k for k in store if k[0] == msg["tab_id"]]:
                    del store[key]
            self.sports.pop(msg["tab_id"], None)
            self.team_text.pop(msg["tab_id"], None)
            self.page_text.pop(msg["tab_id"], None)
            self.feed_map.forget(msg["tab_id"])
            for key in [k for k in self.unconfirmed if k[0] == msg["tab_id"]]:
                del self.unconfirmed[key]
            self.feed_gate.forget(msg["tab_id"])
            self.digit_found = {k for k in self.digit_found if k[0] != msg["tab_id"]}
            self.zoom_found = {k for k in self.zoom_found if k[0] != msg["tab_id"]}
            self.gap_found = {k for k in self.gap_found if k[0] != msg["tab_id"]}
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
            if isinstance(msg.get("info"), dict):
                self._page_info(tab_id, msg["info"])
            self._check_expand(tab_id, info)
            self._note_audible(tab_id, info)
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
                    self._rec_swap(swap, "user", now)
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

    def _rec_event(self, kind: str, tab_id: int | None = None, tile: int | None = None, **fields) -> None:
        """One line in the dataset's event file (swap, score change, cue, ...). Never raises, never waits."""
        if self.recorder is None or not self.recorder.enabled():
            return
        try:
            sport = None
            if tab_id is not None:   # a tile of a multiview has its own sport; the only video of a tab has the tab's
                sport = self.scoring_sport(tab_id, tile) if tile is not None and self._is_multi(tab_id) else self._tab_sport(tab_id)
            event = {"v": 1, "t": round(time.time(), 3), "mono": round(time.monotonic(), 3), "kind": kind, "tab": tab_id, "tile": tile,
                     "sport": sport, "main": self.scorer.main}
            event.update(fields)
            self.recorder.add_event(event)
        except Exception as e:
            log.warning("dataset event %s failed: %s: %s", kind, type(e).__name__, e)

    def _rec_swap(self, swap: Swap, source: str, now: float) -> None:
        """The swap just decided: who, why, and the score of both sides at that moment."""
        if self.recorder is None or not self.recorder.enabled():
            return
        try:
            def side(tab_id):
                c = self.scorer.components(tab_id, now, warmup=False)
                return None if c is None else {k: round(c[k], 3) for k in ("score", "surge", "crowd", "speech", "music", "shout", "event", "cue")}
            self._rec_event("swap", swap.promote, source=source, promote=swap.promote, demote=swap.demote, reason=swap.reason,
                            promote_c=side(swap.promote), demote_c=side(swap.demote))
        except Exception as e:
            log.warning("dataset swap record failed: %s: %s", type(e).__name__, e)

    def _record_rows(self, now: float) -> None:
        if self.recorder is None or not self.recorder.enabled():
            return
        try:
            for row in snapshot_rows(self, now):
                self.recorder.add_row(row)
        except Exception as e:   # the recorder must never hurt the daemon
            log.warning("dataset rows failed: %s: %s", type(e).__name__, e)

    def _log_scores(self, now: float) -> Swap | None:
        """Log the scores; returns the swap the build-up tracker made on the way (football), if any."""
        taken = None
        for tab_id, what, secs in self.scorer.goal_hold_changes(now):
            if what == "start":
                log.info("post-goal replay hold tab %s until +%.0f s", tab_id, secs)
            else:
                log.info("post-goal replay over tab %s after %.0f s", tab_id, secs)
        for tab_id in self.scorer.streams:
            c = self.scorer.components(tab_id, now)
            if c is None:
                continue
            cfg = self.scorer.config
            self.goal_watch.surge_min, self.goal_watch.window_s = cfg.goal_phrase_surge, cfg.goal_phrase_window_s
            if self.goal_watch.surge(tab_id, c["surge"], now):
                log.info("goal phrase + surge would fire tab %s (measuring only)", tab_id)
            self._check_surge_rise(tab_id, c, now)
            taken = taken or self._check_buildup(tab_id, c, now)
        return taken

    def _log_snapshot(self, now: float) -> None:
        """The score line of every stream (daemon.log) and the dataset rows, every `SNAPSHOT_INTERVAL_S`. Logging only: no decision uses them."""
        for tab_id in self.scorer.streams:
            tag = "MAIN " if tab_id == self.scorer.main else ""
            c = self.scorer.components(tab_id, now)
            if c is None:
                log.info("  %s%s: warming up or no audio", tag, tab_id)
                continue
            text = (f"{tag}{tab_id}: {'AD' if c['ad'] else c['status']} fav={c['fav']:.1f} score={c['score']:.2f} surge={c['surge']:.2f} crowd={c['crowd']:.2f} "
                    f"speech={c['speech']:.2f} music={c['music']:.2f} shout={c['shout']:.2f} lev={c['lev']:.1f}")
            if c["tile"] is not None:   # a multiview: the tab scores as its best tile; say which tile and sport
                text += f" tile={c['tile']} sport={c['sport'] or 'unknown'} tiles=" + ",".join(f"{i}:{s:.2f}" for i, s in sorted(c["tile_scores"].items()))
            probs = self.tags.get(tab_id)
            if probs is not None:
                text += " top=" + ",".join(f"{n}:{p:.2f}" for n, p in self.classifier.top(probs, 3))
            log.info("  %s", text)
        self._record_rows(now)

    def _check_surge_rise(self, tab_id: int, c: dict, now: float) -> bool:
        """Soccer: the loudness of a live stream rises fast over a few seconds (voted over consecutive readings, with a cooldown): a medium
        cue (`cue_surge_rise`) that arrives before the commentator's words. It feeds the score like a banner cue, the build-up tracker (kind
        'surge_rise') and the dataset (event 'surge_rise'). Not on an ad, a break or a replay."""
        tile, sport = self._audible_target(tab_id)   # the sound belongs to the audible tile: its cue, its sport
        cfg = self.scorer.tile_cfg(tab_id, tile)
        weight = surge_rise_weight(sport, cfg.weights)
        if weight is None or c["ad"] or c["status"] != "live" or self.scorer.replay_of(tab_id, tile):
            self.surge_rise.reading(tab_id, now, c["level"], 0.0, cfg)   # keeps the history and resets the vote
            return False
        if not self.surge_rise.reading(tab_id, now, c["level"], c["surge"], cfg):
            return False
        where = "" if tile is None else f" tile {tile}"
        log.info("anticipation surge rise tab %s%s (sound) weight %.2f: surge %.2f level %.4f", tab_id, where, weight, c["surge"], c["level"])
        self.scorer.set_anticipation(tab_id, now, weight, tile=tile)
        self.buildup.note(self._bkey(tab_id, tile), now, weight, "surge_rise")
        self._rec_event("surge_rise", tab_id, tile, weight=round(weight, 3), surge=round(c["surge"], 3), crowd=round(c["crowd"], 3), level=round(c["level"], 5))
        return True

    def _check_buildup(self, tab_id: int, c: dict, now: float) -> Swap | None:
        """Measuring only, soccer only (each sport has its own clues and needs its own calibration): log when the cues of a stream added
        together, plus its loudness and crowd, would have been enough to take MAIN. Compare these lines with the goals: scripts/backtest.py."""
        if not self._is_multi(tab_id):
            return self._check_buildup_tile(tab_id, None, c, now)
        taken = None
        for index in self._tab_indices(tab_id):   # every video of a multiview has its own build-up, with its own sport
            taken = taken or self._check_buildup_tile(tab_id, index, c, now)
        return taken

    def main_buildup_level(self, main_c: dict | None, now: float) -> float:
        """MAIN's own build-up level (its cues + surge/crowd with ITS sport's weights): the bar a challenger must beat. In a multiview the
        highest of its tiles; only the tile that has the sound gets the surge / crowd part."""
        main = self.scorer.main
        if not main_c or main is None:
            return 0.0
        best = 0.0
        multi = self._is_multi(main)
        for index in (self._tab_indices(main) if multi else [None]):
            mw = self.scorer.tile_cfg(main, index).weights
            support = (mw.surge * main_c["surge"] + mw.crowd * main_c["crowd"] + mw.shout * main_c["shout"]) if self._hears(main, index) else 0.0
            best = max(best, self.buildup.level(self._bkey(main, index), now, support))
        return best

    def _main_hold(self, main: int | None, key, now: float, level: float, main_level: float, cfg) -> str | None:
        """`BuildUp.main_hold` against every stream of the MAIN tab (its tiles in a multiview)."""
        if main is None:
            return None
        for main_key in self._bkeys(main):
            blocked = self.buildup.main_hold(main_key, key, now, level, main_level, cfg)
            if blocked is not None:
                return blocked
        return None

    def _check_buildup_tile(self, tab_id: int, tile: int | None, c: dict, now: float) -> Swap | None:
        """The build-up of ONE video (the tab's only video, or one tile of a multiview) with that video's sport. The loudness and crowd
        (`c`, the tab's audio) support only the tile that has the sound; the others build up on their cues alone."""
        cfg = self.scorer.tile_cfg(tab_id, tile)   # the build-up values of THIS stream's sport (shared unless overridden in settings.toml)
        sport = self.scoring_sport(tab_id, tile)
        if cfg.buildup_mode <= 0 or tab_id == self.scorer.main or not is_soccer(sport):
            return None
        key, hears = self._bkey(tab_id, tile), self._hears(tab_id, tile)
        main = self.scorer.components(self.scorer.main, now) if self.scorer.main is not None else None
        main_score = main["score"] if main else 0.0
        w = cfg.weights
        self.buildup.window_s, self.buildup.cooldown_s = cfg.buildup_window_s, cfg.buildup_cooldown_s
        main_level = self.main_buildup_level(main, now)
        support = (w.surge * c["surge"] + w.crowd * c["crowd"] + w.shout * c["shout"]) if hears else 0.0
        if hears and self.buildup.surge_note_due(key, now, c["surge"], cfg.buildup_surge_min):
            self.buildup.note(key, now, cfg.buildup_surge_note, "surge_note")   # measuring only: a sign for the build-up, never set_anticipation
        if hears and self.buildup.crowd_note_due(key, now, c["crowd"], cfg.buildup_crowd_min):
            self.buildup.note(key, now, cfg.buildup_crowd_note, "crowd_note")   # a crowd that keeps building: a sign, never a score
        # the cooldown starts only when the swap really happens (or in log-only mode): a swap a guard stopped may be tried again
        level = self.buildup.would_take(key, now, support, main_score, cfg.buildup_min, cfg.buildup_gap, start_cooldown=False, main_level=main_level)
        if level is not None:
            cues = ", ".join(f"{v:.2f}@{age:.0f}s" for age, v in self.buildup.cues(key, now))
            outcome, swap, stopped = "log only", None, False
            if cfg.buildup_live > 0:
                if not moves_main(sport):
                    outcome, stopped = "not moved: sport not detected as football", True
                else:
                    # the swap guards, then MAIN keeping its seat after its own cue / review (log-only mode applies neither)
                    blocked = (self.scorer.take_main_block(tab_id, now, level=level, cue_age=self.buildup.spoken_age(key, now), main_level=main_level, tile=tile)
                               or self._main_hold(self.scorer.main, key, now, level, main_level, cfg))
                    if blocked is not None:
                        outcome, stopped = f"not moved: {blocked}", True
                    else:
                        reason = f"build-up: level {level:.2f} (cues {cues}) vs main {main_score:.2f}"
                        swap = self.scorer.take_main(tab_id, now, reason, level=level, cue_age=self.buildup.spoken_age(key, now), main_level=main_level, tile=tile)
                        outcome = "moved MAIN"
            if stopped:
                if not self.buildup.blocked_log_due(key, now):
                    return None   # a stopped build-up may fire on every reading: say why only every 10 s
            else:
                self.buildup.mark_fired(key, now)
            where = f"{tab_id}" if tile is None else f"{tab_id} tile {tile} ({sport or 'unknown'})"   # a multiview line says which tile and sport
            log.info("buildup would take MAIN tab %s: level %.2f (cues %s; surge %.2f crowd %.2f) vs main %.2f (%s)",
                     where, level, cues, c["surge"] if hears else 0.0, c["crowd"] if hears else 0.0, main_score, outcome)
            self._rec_event("buildup", tab_id, tile, level=round(level, 3), main_score=round(main_score, 3), surge=round(c["surge"], 3) if hears else 0.0,
                            crowd=round(c["crowd"], 3) if hears else 0.0, outcome=outcome, moved=swap is not None)
            return swap
        return None

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
            self.feed_gate.fresh = self.feed.fresh_game_ids(time.monotonic())
            now = time.monotonic()
            for tab_id, tile in self.feed_gate.confirmations(now):   # the feed confirmed an OCR score change later: full strength
                full = self.unconfirmed.pop((tab_id, tile), None)
                if full is not None:
                    self.scorer.set_event(tab_id, now, full[0], goal=True, tile=self._tkey(tab_id, tile))
            weight = self.scorer.config.feed_event_weight   # 0 = the feed only logs; above 0 a released feed goal counts like a score change
            for f in self.feed_gate.drain_fired():
                if weight > 0:
                    self.scorer.set_event(f.tab, now, f.strength * weight, goal=True, tile=self._tkey(f.tab, f.tile))
                    self.fed[(f.tab, f.tile)] = now
                    self._rec_event("feed_goal", f.tab, f.tile, goal_kind=f.kind, strength=f.strength)
            for key in [k for k, (_, at) in self.unconfirmed.items() if now - at > self.scorer.config.feed_confirm_s]:
                del self.unconfirmed[key]
            for line in self.feed_gate.messages:
                log.info("%s", line)
            self.feed_gate.messages.clear()
            try:
                self.feed_map.set_games(self.feed.games())
                self.feed_gate.sport_of.update({s.game_id: s.sport for s in self.feed.games()})
                for game_id, situations in self.feed.situations().items():
                    self.feed_gate.set_situations(game_id, situations)
            except Exception as e:   # mapping is measuring only too
                log.warning("feed map update failed: %s: %s", type(e).__name__, e)
            for n in notes:
                if n.event is not None:
                    self.feed_gate.add(n.event, n.snap, now)
                elif n.kind == "score":
                    self.feed_gate.add_score(n.snap, n.before, now)
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
                tile, sport = self._audible_target(tab_id)   # the commentary is the audible tile's: its sport, its cues, its build-up
                tcfg = self.scorer.tile_cfg(tab_id, tile)   # cue weights and factor of this stream's sport
                bkey = self._bkey(tab_id, tile)
                cues = find_speech_cues(text, sport)
                self.cue_speech.window_s = cfg.cue_speech_window_s
                self.cue_speech.record(bkey, cues, text, sport, now)
                self.goal_watch.surge_min, self.goal_watch.window_s = cfg.goal_phrase_surge, cfg.goal_phrase_window_s
                if "goal" in cues and self.goal_watch.phrase(tab_id, now):
                    log.info("goal phrase + surge would fire tab %s (measuring only)", tab_id)
                if "replay" in cues:
                    self.scorer.note_replay(tab_id, now, tile)   # the commentator says replay: extends a post-goal hold
                where = "" if tile is None else f" tile {tile}"
                for cue in cues:
                    if now - self.speech_cue_at.get((tab_id, cue), float("-inf")) >= tcfg.speech_cue_cooldown_s:
                        self.speech_cue_at[(tab_id, cue)] = now
                        log.info("speech cue %s tab %s: %s", cue, tab_id, text)
                        self._rec_event("speech_cue", tab_id, tile, cue=cue, text=text, language=language)
                        weight = speech_cue_weight(cue, sport, tcfg.weights, tcfg.speech_cue_factor)
                        if weight is not None:   # a commentator calling a dangerous moment boosts the stream like the banner would, at a discount
                            log.info("anticipation %s tab %s%s (spoken) weight %.2f", cue, tab_id, where, weight)
                            self.scorer.set_anticipation(tab_id, now, weight, strong=is_strong_cue(weight, sport, tcfg.weights), tile=tile)
                            self.buildup.note(bkey, now, weight, "spoken:" + cue)
                        note = tracker_note_weight(cue, sport, tcfg.weights, tcfg.speech_cue_factor)
                        if note is not None:   # a set piece is a build-up sign for the tracker only: no score, no anticipation
                            log.info("buildup note %s tab %s%s weight %.2f", cue, tab_id, where, note)
                            self.buildup.note(bkey, now, note, "note:" + cue)

    def _feed_text(self, tab_id: int) -> str:
        """The text the feed mapper looks for team names in: what the user typed in the popup, else what the page says."""
        return self.team_text.get(tab_id) or self.page_text.get(tab_id, "")

    def _page_info(self, tab_id: int, info: dict) -> None:
        """The page's own words about the game (title, media session, player header). Weak evidence: it feeds the team mapping when the
        popup has no teams, and the sport by its league words. Every string is cut short."""
        media = info.get("media") if isinstance(info.get("media"), dict) else {}
        parts = [str(media.get(k) or "")[:120] for k in ("title", "artist", "album")]
        if not media.get("title"):   # the media session names the match (CANAL+: "New Orleans Saints / Minnesota Vikings"); the page texts are only a fallback (menus, mostly)
            parts += [str(t)[:120] for t in (info.get("texts") or [])[:12] if isinstance(t, str)]
        text = " | ".join(p for p in parts if p)
        if text != self.page_text.get(tab_id, ""):
            self.page_text[tab_id] = text
            log.info("page info tab %s: title=%r media=%r texts=%r", tab_id, str(info.get("title") or "")[:80], media, (info.get("texts") or [])[:12])
            self.feed_map.set_text(tab_id, self._feed_text(tab_id))

    def tile_sport(self, tab_id: int, index: int) -> str | None:
        """THE sport of one tile, lowercase: the popup choice of its tab, else the best one the daemon knows for this tile (the full
        `choose_sport` order, stored by `_update_game_states`), else the plain scorebug detection. None when nothing is known."""
        sport = self.sports.get(tab_id)
        if not sport:
            choice = self.sport_choice_seen.get((tab_id, index))
            sport = (choice[0] if choice else None) or self.sport_seen.get((tab_id, index))
        return sport.lower() if sport else None

    def _audible_index(self, tab_id: int) -> int | None:
        """The tile that has the sound: the only tile of a one-video tab, else the first one the extension reports as audible."""
        rects = (self.rects.get(tab_id) or {}).get("rects") or []
        if len(rects) == 1:
            return rects[0].get("index", 0)
        return next((r.get("index", pos) for pos, r in enumerate(rects) if r.get("audible")), None)

    def audible_sport(self, tab_id: int) -> str | None:
        """The sport of a tab as the audio and the scorer must read it, lowercase: the popup choice, else the sport of the tile that has the
        sound. In a multiview (2+ videos) an audible tile of unknown sport counts as football. With no audible tile known (or one video of
        unknown sport) the first sport known on any tile of the tab, as before."""
        sport = self.sports.get(tab_id)
        if sport:
            return sport.lower()
        index = self._audible_index(tab_id)
        if index is not None:
            sport = self.tile_sport(tab_id, index)
            if sport:
                return sport
            if len((self.rects.get(tab_id) or {}).get("rects") or []) >= 2:
                return "football"
        for store in (self.sport_choice_seen, self.sport_seen):
            for (t, _), value in store.items():
                found = value[0] if isinstance(value, tuple) else value
                if t == tab_id and found:
                    return found.lower()
        return None

    def _tab_sport(self, tab_id: int) -> str | None:
        """The sport of a tab, lowercase (what the Scorer, the speech loop and the build-up tracker use): that of the audible tile."""
        return self.audible_sport(tab_id)

    # ---- tiles of a multiview tab: each video is scored, built up and spoken to with its own sport (docs/per-tile-sport.md, Stage 2) ----

    def _tab_indices(self, tab_id: int, extra=()) -> list[int]:
        """The video indexes of a tab: those of its reported rects (else of its trackers), plus `extra` (the tiles just read)."""
        rects = (self.rects.get(tab_id) or {}).get("rects") or []
        if rects:
            return sorted({r.get("index", pos) for pos, r in enumerate(rects)} | set(extra))
        return sorted(set(extra) | {i for (t, i) in self.trackers if t == tab_id})

    def _is_multi(self, tab_id: int, extra=()) -> bool:
        """The tab shows two or more videos at once (a DAZN multiview). One video = the tab behaves exactly as before the per-tile work."""
        return len(self._tab_indices(tab_id, extra)) >= 2

    def _tkey(self, tab_id: int, index: int, extra=()) -> int | None:
        """The tile argument for the Scorer: the video index in a multiview, None for the only video of a tab."""
        return index if self._is_multi(tab_id, extra) else None

    @staticmethod
    def _bkey(tab_id: int, tile: int | None):
        """The key of a stream in `buildup` and `cue_speech`: the tab id, or `(tab id, video index)` for a tile of a multiview."""
        return tab_id if tile is None else (tab_id, tile)

    def scoring_sport(self, tab_id: int, tile: int | None) -> str | None:
        """The sport the Scorer reads a tile with (lowercase). A tile of a multiview whose sport is not known is football, like the audible
        sport of Stage 1; the only video of a tab keeps the tab sport (None when unknown)."""
        if tile is None:
            return self._tab_sport(tab_id)
        return self.tile_sport(tab_id, tile) or ("football" if self._is_multi(tab_id) else self._tab_sport(tab_id))

    def _audible_target(self, tab_id: int) -> tuple[int | None, str | None]:
        """(tile, sport) the tab's AUDIO belongs to (speech, rising-sound cue): the only video of a tab, or the tile that has the sound in a
        multiview. With the sound's tile not known the tile whose sport is the tab's audible sport, else the first one."""
        if not self._is_multi(tab_id):
            return None, self._tab_sport(tab_id)
        index = self._audible_index(tab_id)
        if index is None:
            indices = self._tab_indices(tab_id)
            sport = self._tab_sport(tab_id)
            index = next((i for i in indices if self.scoring_sport(tab_id, i) == sport), indices[0])
        return index, self.scoring_sport(tab_id, index)

    def _hears(self, tab_id: int, tile: int | None) -> bool:
        """The tab's sound belongs to this tile (every tile of a multiview hears it while the audible tile is not known)."""
        if tile is None:
            return True
        audible = self._audible_index(tab_id)
        return audible is None or audible == tile

    def _bkeys(self, tab_id: int) -> list:
        """The build-up keys of a tab: its id, or one `(tab, tile)` per video in a multiview."""
        return [self._bkey(tab_id, i) for i in self._tab_indices(tab_id)] if self._is_multi(tab_id) else [tab_id]

    def _note_audible(self, tab_id: int, info: dict) -> None:
        """Multiview: when the sound moves to another tile, drop the audio of the old one (it is another commentary, another crowd) and say so."""
        rects = info.get("rects") or []
        if len(rects) < 2:
            self.audible_seen.pop(tab_id, None)
            return
        index = self._audible_index(tab_id)
        if index is None:
            return
        sport = self.audible_sport(tab_id)
        old = self.audible_seen.get(tab_id)
        self.audible_seen[tab_id] = (index, sport)
        if old is None or old[0] == index:
            return
        log.info("audible tile tab %s: tile %s -> %s (sport %s -> %s)", tab_id, old[0], index, old[1] or "unknown", sport or "unknown")
        self.speech_buffers.pop(tab_id, None)
        self.buffers.pop(tab_id, None)
        self.tags.pop(tab_id, None)
        self.surge_rise.remove(tab_id)   # the loudness history is the old tile's
        stream = self.scorer.streams.get(tab_id)
        if stream is not None:
            stream.tags_at = float("-inf")   # the crowd / speech / music tags belong to the old tile: stale until the next classification

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
            if hasattr(self.screen_reader, "gap_pass") and find_score_geo(items, find_clocks(texts)) is None:
                # Team codes at both ends and the scores between them, with a digit missed (thin white digit on a pale panel, a
                # logo between the scores): read only the strip between the codes again, at twice the size, digits only.
                key, now_s = (tab_id, index), time.monotonic()
                if now_s - self.gap_try_at.get(key, float("-inf")) >= GAP_RETRY_S:
                    gap = self.screen_reader.gap_pass(image, items)
                    self.gap_try_at[key] = now_s
                    if gap is not None:
                        merged = merge_zoom(items, *gap)
                        if find_score_geo(merged, find_clocks([it["text"] for it in merged])) is not None:
                            if key not in self.gap_found:
                                log.info("gap pass tab %s tile %s: score found", tab_id, index)
                            self.gap_found.add(key)
                            items, texts = merged, [it["text"] for it in merged]
                        else:
                            self.gap_found.discard(key)
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
            if self.rapid_reader is not None and self.scorer.config.ocr_assist > 0 and self.rapid_reader.available and needs_rescue(items):
                # A scorebug is on screen but the score or the clock is missing: the second reader (PaddleOCR, CPU) adds its own text and the
                # two score digits read slot by slot. Not on every pass when nothing is found (it costs CPU time).
                key, now_s = (tab_id, index), time.monotonic()
                if now_s - self.rapid_try_at.get(key, float("-inf")) >= self.scorer.config.ocr_assist_retry_s or key in self.rapid_found:
                    self.rapid_try_at[key] = now_s
                    try:
                        merged = self.rapid_reader.rescue(image, items)
                    except Exception:   # the second reader must never break the OCR pass: keep the first reader's items
                        log.exception("second OCR reader failed on tab %s tile %s", tab_id, index)
                        merged = items
                    if find_score_geo(merged, find_clocks([it["text"] for it in merged])) is not None:
                        if key not in self.rapid_found:
                            log.info("second reader tab %s tile %s: score found", tab_id, index)
                        self.rapid_found.add(key)
                    else:
                        self.rapid_found.discard(key)
                    if len(find_clocks([it["text"] for it in merged])) > len(find_clocks(texts)) or key in self.rapid_found:
                        items, texts = merged, [it["text"] for it in merged]
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
        tile_states: dict[tuple[int, int], str] = {}
        read: dict[int, list[int]] = {}
        for tab_id, index in results:
            read.setdefault(tab_id, []).append(index)
        multi = {t for t, indices in read.items() if self._is_multi(t, indices)}   # tabs that show several videos: one scorer record per video
        for tab_id, indices in read.items():
            self.scorer.set_tiles(tab_id, self._tab_indices(tab_id, indices) if tab_id in multi else [])
        for (tab_id, index), r in results.items():
            tk = index if tab_id in multi else None   # the tile argument of every scorer call below
            tracker = self.trackers.setdefault((tab_id, index), GameTracker(self.sports.get(tab_id), self.scorer.config))
            detected = self.sport_detectors.setdefault((tab_id, index), SportDetector()).update(r["texts"], r["clocks"])
            det = self.sport_detectors[(tab_id, index)]
            now_wall = time.time()
            mapping = self.feed_map.mapping(tab_id, index)
            feed_sport = sport_from_feed(LEAGUES[mapping.league][0]) if mapping and mapping.league in LEAGUES else None
            # popup choice > hard cue > feed league > tab title (stage C) > league words on screen > remembered sport
            memory_key = tab_id if tk is None else f"{tab_id}:{index}"   # a multiview remembers the sport of each tile, not one for the tab
            sport, source = choose_sport(popup=self.sports.get(tab_id), hard=detected, feed=feed_sport, title=sport_from_title(self.tab_titles.get(tab_id)) or sport_from_title(self.page_text.get(tab_id)),
                                         weak=det.weak_sport, memory=self.sport_memory.get(memory_key, now_wall))
            tracker.set_sport(sport)
            if source in ("hard", "feed"):   # only reliable sources are remembered
                self.sport_memory.remember(memory_key, sport, now_wall)
            self.sport_memory.save()
            if (sport, source) != self.sport_choice_seen.get((tab_id, index)):
                self.sport_choice_seen[(tab_id, index)] = (sport, source)
                log.info("sport %s tab %s tile %s via %s", sport or "unknown", tab_id, index, source or "none")
            if detected != self.sport_seen.get((tab_id, index)):
                log.info("sport detected tab %s tile %s: %s", tab_id, index, detected or "unknown")
                self.sport_seen[(tab_id, index)] = detected
            tile_states[(tab_id, index)] = tracker.update(now, r["texts"], r["clocks"])
            per_tab.setdefault(tab_id, []).append(tile_states[(tab_id, index)])
            if tracker.scorebug_note:   # measuring only: DAZN hides the scorebug during replays
                kind, secs = tracker.scorebug_note
                log.info("scorebug %s tab %s tile %s: %.0f s", kind, tab_id, index, secs)
            if tracker.clock_replay_note:   # measuring only unless scoring.replay_by_clock_s > 0
                kind, secs = tracker.clock_replay_note
                log.info("replay by clock tab %s tile %s: %s", tab_id, index, f"-{secs:.0f} s" if kind == "start" else f"ended after {secs:.0f} s")
            if tracker.scorebug_note:
                self.replay_signal[(tab_id, index, "bug")] = tracker.scorebug_note[0] == "hidden"
            if tracker.clock_replay_note:
                self.replay_signal[(tab_id, index, "clock")] = tracker.clock_replay_note[0] == "start"
            if self.replay_signal.get((tab_id, index, "bug")) or self.replay_signal.get((tab_id, index, "clock")):
                self.scorer.note_replay(tab_id, now, tk)   # extends the post-goal hold of this tile
            tile_cfg = self.scorer.config.for_sport(tracker.sport)   # this tile's sport: shared values unless overridden in settings.toml
            for cue in tracker.anticipation:
                banner = tracker.anticipation_text.get(cue, cue)
                zone = end_zone_factor(tracker.end_zone_yards, tracker.down, tracker.goal_to_go, tile_cfg)   # None when the distance is unknown
                weight = cue_weight(cue, banner, self.cue_speech.tier(self._bkey(tab_id, tk), now), tracker.sport or None, tile_cfg.weights, zone=zone)
                log.info("anticipation %s tab %s tile %s weight %.2f", cue, tab_id, index, weight)
                self.scorer.set_anticipation(tab_id, now, weight, strong=is_strong_cue(weight, tracker.sport or None, tile_cfg.weights), tile=tk)
                self.buildup.note(self._bkey(tab_id, tk), now, weight, cue)
                self._rec_event("banner_cue", tab_id, index, cue=cue, banner=banner, weight=round(weight, 3))
            if tracker.big_play:
                strength = tile_cfg.big_play_strength
                self.event_at[(tab_id, index)] = max(self.event_at.get((tab_id, index), float("-inf")),
                                                     now - (1.0 - strength) * self.scorer.config.event_s)
                self.scorer.set_event(tab_id, now, strength, tile=tk)
                log.info("big play tab %s tile %s: %s", tab_id, index, tracker.big_play)
                self._rec_event("big_play", tab_id, index, text=str(tracker.big_play), strength=strength)
            if tracker.goal_banner:   # a GOAL banner (soccer): an early event, a real score change takes it to full strength
                strength = tile_cfg.goal_banner_strength
                if strength > 0:
                    self.event_at[(tab_id, index)] = max(self.event_at.get((tab_id, index), float("-inf")),
                                                         now - (1.0 - strength) * self.scorer.config.event_s)
                    self.scorer.set_event(tab_id, now, strength, goal=True, tile=tk)
                log.info("goal banner tab %s tile %s: %s", tab_id, index, tracker.goal_banner)
                self._rec_event("goal_banner", tab_id, index, text=str(tracker.goal_banner), strength=strength)
            seen = match_multiplier(r["texts"], self.favourites)
            if seen > 1.0:
                self.fav_seen[(tab_id, index)] = (seen, now)
            rule = match_slots(r["texts"], self.favourites)
            if rule:
                self.rule_seen[(tab_id, index)] = (rule, now)
            watcher = self.watchers.setdefault((tab_id, index), ScoreWatcher(self.scorer.config))
            changed = watcher.update(r["texts"], r["clocks"], now, r.get("items"), replay=tracker.replay,
                                     sport=self.tile_sport(tab_id, index))
            if watcher.candidate and per_tab[tab_id][-1] == "live":   # a higher score the vote has not confirmed yet: an early goal signal for the music rule
                self.scorer.set_goal_pending(tab_id, now, tile=tk)
            for line in self.feed_map.observe(tab_id, index, r["texts"], watcher.score, r["clocks"]):
                log.info("%s", line)
            mapped = self.feed_map.mapping(tab_id, index)
            self.feed_gate.lead_s = self.scorer.config.feed_lead_s
            self.feed_gate.confirm_s = self.scorer.config.feed_confirm_s
            self.feed_gate.field_goal_strength = self.scorer.config.field_goal_strength
            self.feed_gate.big_play_strength = self.scorer.config.big_play_strength
            clock_now = parse_clock_s(r["clocks"])
            if mapped is not None and clock_now is not None:   # measuring only: the OCR score against the feed score at the stream clock
                allowed = self.feed_map.allowed_scores(mapped.game_id, clock_now, tracker.period)
                for line in self.feed_gate.referee(tab_id, index, watcher.score, allowed, tracker.replay, clock_now):
                    log.info("%s", line)
            for line in self.feed_gate.observe(tab_id, index, mapped.game_id if mapped else None, r["clocks"], tracker.period, tracker.replay, now):
                log.info("%s", line)
            if per_tab[tab_id][-1] == "over":   # measuring only: a feed goal the screen never showed, at the end of the game
                for line in self.feed_gate.game_over(tab_id, index, now):
                    log.info("%s", line)
            held = self.held_change.get((tab_id, index))
            if held is not None and now - held[1] > self.scorer.config.held_change_s:
                del self.held_change[(tab_id, index)]
                held = None
            if changed and per_tab[tab_id][-1] != "live":
                log.info("score change ignored on tab %s tile %s: the game is %s", tab_id, index, per_tab[tab_id][-1])   # results tickers, countdowns
                if self.scorer.config.held_change_s > 0 and per_tab[tab_id][-1] == "break":   # kept (not for "over": a finished game): a wrong break reading right after a touchdown must not lose it
                    self.held_change[(tab_id, index)] = (watcher.strength, now)
            elif held is not None and not changed and per_tab[tab_id][-1] == "live":   # the game is live again: the held change counts now
                del self.held_change[(tab_id, index)]
                self.event_at[(tab_id, index)] = now - (1.0 - held[0]) * self.scorer.config.event_s
                self.scorer.set_event(tab_id, now, held[0], goal=True, tile=tk)
                log.info("score change tab %s tile %s counted late: it was read %.0f s ago while the game looked like a break", tab_id, index, now - held[1])
                self._rec_event("score_change", tab_id, index, home=(watcher.score or (None, None))[0], away=(watcher.score or (None, None))[1], strength=held[0], note="counted late")
            elif changed:
                self.held_change.pop((tab_id, index), None)
                change = self.feed_gate.real_change(tab_id, index, now)
                for line in change.lines:
                    log.info("%s", line)
                strength = watcher.strength
                factor = self.scorer.config.feed_unconfirmed_factor
                if change.verdict == "ocr-only" and factor < 1.0:   # mapped game, no feed event first: counts less until the feed confirms
                    self.unconfirmed[(tab_id, index)] = (strength, now)
                    strength *= factor
                if change.counted and self.scorer.config.feed_event_weight > 0:   # the feed already counted this goal
                    log.info("score change tab %s tile %s counted by the feed already: %s", tab_id, index, r["texts"][:8])
                    self._rec_event("score_change", tab_id, index, home=(watcher.score or (None, None))[0], away=(watcher.score or (None, None))[1], strength=strength, note="counted by the feed already")
                else:
                    self.event_at[(tab_id, index)] = now - (1.0 - strength) * self.scorer.config.event_s
                    self.scorer.set_event(tab_id, now, strength, goal=True, tile=tk)
                    log.info("score change tab %s tile %s: %s", tab_id, index, r["texts"][:8])
                    self._rec_event("score_change", tab_id, index, home=(watcher.score or (None, None))[0], away=(watcher.score or (None, None))[1], strength=strength, verdict=change.verdict)
        for tab_id, states in per_tab.items():
            self._update_favourite(tab_id, now)
            before = self.scorer.tab_auto_state(tab_id)
            for index in read[tab_id]:   # the picture signals of each video go to its own record (a single video: the stream's own)
                tk = index if tab_id in multi else None
                self.scorer.set_on_ad(tab_id, self.trackers[(tab_id, index)].on_ad, now, tile=tk)   # a tab is on an ad when all its videos are
                self.scorer.set_replay(tab_id, self.trackers[(tab_id, index)].replay, now, tile=tk)
                self.scorer.set_auto_state(tab_id, tile_states[(tab_id, index)], tile=tk)
            state = combine(states)
            stream = self.scorer.streams.get(tab_id)
            if stream is not None and before != state:
                log.info("game state tab %s: %s -> %s", tab_id, before, state)
                self._rec_event("game_state", tab_id, old=before, new=state)
            tiles = {}   # closeness and time left of each game in this tab
            for (t, index), watcher in self.watchers.items():
                if t != tab_id:
                    continue
                tracker = self.trackers.get((t, index))
                tiles[index] = (self.tile_sport(t, index), watcher.score, tracker.period if tracker else None, tracker.clock_minute if tracker else None)
            if tab_id in multi:   # each tile has its own leverage, read with its own sport
                for index in read[tab_id]:
                    if index in tiles:
                        self.scorer.set_leverage(tab_id, tab_leverage([tiles[index]], self.scorer.config), tile=index)
            else:   # a single video: the closeness and time left of the best game of the tab, as before
                self.scorer.set_leverage(tab_id, tab_leverage(list(tiles.values()), self.scorer.config))

    def _focus_commands(self, now: float) -> list[dict]:
        """For multiview tabs: which tile should have the sound?"""
        commands = []
        for tab_id, info in self.rects.items():
            if not info["visible"] or len(info["rects"]) < 2:
                continue
            if self.scorer.frozen and tab_id == self.scorer.main:
                continue  # MAIN is locked: leave which tile it shows alone
            pending = self.pending_expand.get(tab_id)
            if pending is not None and now < pending[1]:
                continue   # a tile was asked to expand a moment ago: wait to see where the sound went (`_check_expand`) before asking again
            scores = self.scorer.tile_scores(tab_id, now)   # the wanted tile: the one the scorer rates highest (None while the tab has no score)
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
                if scores is not None and index in scores:
                    tile["score"] = round(scores[index], 3)
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
        for store in (self.trackers, self.watchers, self.event_at, self.ocr, self.sport_detectors, self.sport_seen, self.sport_choice_seen,
                      self.held_change, self.unconfirmed, self.fed, self.fav_seen, self.rule_seen):
            a, b = (tab_id, target), (tab_id, main)
            moved_a, moved_b = store.pop(a, None), store.pop(b, None)
            if moved_b is not None:
                store[a] = moved_b
            if moved_a is not None:
                store[b] = moved_a
        for kind in ("bug", "clock"):   # replay_signal keys carry a third part
            a, b = (tab_id, target, kind), (tab_id, main, kind)
            moved_a, moved_b = self.replay_signal.pop(a, None), self.replay_signal.pop(b, None)
            if moved_b is not None:
                self.replay_signal[a] = moved_b
            if moved_a is not None:
                self.replay_signal[b] = moved_a
        self.scorer.swap_tiles(tab_id, target, main)   # the scorer's picture records, the build-ups and what was heard follow their videos too
        self.buildup.swap((tab_id, target), (tab_id, main))
        self.cue_speech.swap((tab_id, target), (tab_id, main))
        seen = self.audible_seen.get(tab_id)
        if seen is not None and seen[0] in (target, main):   # the stream that had the sound moved with its record
            self.audible_seen[tab_id] = (main if seen[0] == target else target, seen[1])

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
        last_log = last_fix = last_snap = 0.0
        while True:
            await asyncio.sleep(DECIDE_INTERVAL_S)
            now = time.monotonic()
            taken = None
            if now - last_log >= SCORE_LOG_INTERVAL_S:
                taken = self._log_scores(now)
                last_log = now
            if now - last_snap >= SNAPSHOT_INTERVAL_S:
                self._log_snapshot(now)
                last_snap = now
            self._ensure_main()
            swap = taken or self.scorer.evict_main(now) or self._next_swap(now)
            if not swap and now - last_fix >= FIX_INTERVAL_S and (moves := plan_fix(self.roles, self.limits)):
                last_fix = now
                log.info("arrange (slot rules): %s", moves)
                await self._broadcast({"type": "arrange", "moves": [{"tab_id": t, "role": r} for t, r in moves]})
            if swap:
                log.info("swap: promote tab %d, demote tab %d | %s", swap.promote, swap.demote, swap.reason)
                self._rec_swap(swap, "buildup" if swap is taken else "decide", now)
                await self._perform_swap(swap, restore_main=not self.scorer.frozen)

    async def _maintenance_loop(self) -> None:
        """Completes and prunes the log archive (see logsetup) in a worker thread; once at the start, then every MAINTENANCE_INTERVAL_S."""
        while True:
            try:
                cfg = self.scorer.config
                await asyncio.to_thread(archive_rotated, LOG_DIR)
                await asyncio.to_thread(prune_archive, LOG_DIR, cfg.log_archive_days, cfg.log_archive_max_mb)
            except Exception as e:   # housekeeping must never hurt the daemon
                log.warning("log maintenance failed: %s: %s", type(e).__name__, e)
            await asyncio.sleep(MAINTENANCE_INTERVAL_S)

    async def run(self, host: str = HOST, port: int = PORT) -> None:
        async with serve(self._client, host, port):
            log.info("listening on ws://%s:%d", host, port)
            loops = [self._decide_loop(), self._maintenance_loop()]
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
    rapid_reader = RapidReader() if settings.scoring.ocr_assist > 0 else None   # the second OCR reader: fail-safe, None engine when the package is missing
    if rapid_reader is not None:
        log.info("second OCR reader %s", "ready (cpu)" if rapid_reader.available else "not available (rapidocr-onnxruntime missing?); continuing without it")
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
    recorder = DatasetRecorder(LOG_DIR / "dataset", mode=lambda: settings.scoring.dataset_mode)
    import atexit
    atexit.register(recorder.close)
    try:
        asyncio.run(Server(Scorer(settings.scoring), classifier=classifier, screen_reader=screen_reader,
                           focuser=TileFocuser(settings.focus), settings=settings, speech=speech, semantic=semantic,
                           recorder=recorder, rapid_reader=rapid_reader).run())
    except KeyboardInterrupt:
        pass
