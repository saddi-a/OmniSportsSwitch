"""Learning-data recorder: what the daemon sees and does, written down for later (nothing here changes a score or a swap).

logs/dataset/<YYYY-MM-DD>.jsonl         one row per stream (tab) every ~1 s since 2026-10-17, ~2 s before (`snapshot_rows`, called from `Server._log_snapshot`)
logs/dataset/<YYYY-MM-DD>.events.jsonl  one line per event: swap, score change, goal banner, big play, banner cue, speech cue (with its text),
                                        game state change, build-up decision, feed goal (`Server._rec_event`)

One JSON object per line, compact, UTC-independent wall time `t` (epoch seconds) plus `mono` (time.monotonic, the clock the daemon decides with).
The files change by local day, are append-only, flushed after every batch, and are NOT part of the log archive, so `prune_archive` never touches
them. Format and units: docs/dataset-format.md. Local only (the speech text is in the events); logs/ is never committed.

Cost: the rows are built on the event loop (a few dict lookups per stream), then handed to a bounded queue; a writer thread does the JSON and the
disk work. A full queue DROPS the row (counted, reported once a minute) instead of waiting. `mode()` (scoring.dataset_mode) is read on every
call, so 0 stops all writing at once; the thread and the files are only created by the first row written.
"""
import datetime
import json
import logging
import pathlib
import queue
import threading
import time

from daemon.game_identity import key_text

log = logging.getLogger("daemon.recorder")

SCHEMA_VERSION = 1
QUEUE_SIZE = 4000          # rows + events waiting for the writer (about 15 minutes of 4 streams)
REPORT_EVERY_S = 60.0      # drops and write errors are reported at most this often
_STOP = object()


def _r(value, digits: int = 3):
    return None if value is None else round(float(value), digits)


def _wall_date(t: float) -> str:
    return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d")


class DatasetRecorder:
    def __init__(self, directory: pathlib.Path, mode=lambda: 1, queue_size: int = QUEUE_SIZE):
        self.directory = pathlib.Path(directory)
        self.mode = mode                     # callable -> scoring.dataset_mode (read on every call)
        self.queue: queue.Queue = queue.Queue(maxsize=queue_size)
        self.dropped = 0                     # since the last report
        self.dropped_total = 0
        self.written = 0
        self.errors = 0
        self._reported_at = time.monotonic()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._files: dict[str, tuple[str, object]] = {}   # "rows" / "events" -> (date, open file)

    # --- producers (called on the event loop; must never block) ---
    def enabled(self) -> bool:
        try:
            return float(self.mode()) > 0
        except Exception:
            return False

    def add_row(self, row: dict) -> bool:
        return self._put("rows", row)

    def add_event(self, event: dict) -> bool:
        return self._put("events", event)

    def _put(self, stream: str, obj: dict) -> bool:
        if not self.enabled():
            return False
        self._start()
        try:
            self.queue.put_nowait((stream, obj))
            return True
        except queue.Full:
            self.dropped += 1
            self.dropped_total += 1
            self._report(time.monotonic())
            return False

    def _start(self) -> None:
        if self._thread is None:
            with self._lock:
                if self._thread is None:
                    self._thread = threading.Thread(target=self._run, name="dataset-writer", daemon=True)
                    self._thread.start()

    def _report(self, now: float) -> None:
        if now - self._reported_at < REPORT_EVERY_S:
            return
        self._reported_at = now
        if self.dropped or self.errors:
            log.warning("dataset: %d row(s) dropped (writer too slow or queue full), %d write error(s) in the last minute", self.dropped, self.errors)
            self.dropped = self.errors = 0

    def close(self, timeout: float = 5.0) -> None:
        """Write what is queued and close the files (at exit, and in tests)."""
        thread = self._thread
        if thread is not None and thread.is_alive():
            try:
                self.queue.put(_STOP, timeout=timeout)
            except queue.Full:
                return
            thread.join(timeout)
        self._thread = None

    # --- the writer thread ---
    def _run(self) -> None:
        while True:
            item = self.queue.get()
            batch, stop = [], item is _STOP
            if not stop:
                batch.append(item)
                while len(batch) < 500:   # take what is waiting, then flush once
                    try:
                        more = self.queue.get_nowait()
                    except queue.Empty:
                        break
                    if more is _STOP:
                        stop = True
                        break
                    batch.append(more)
            try:
                self._write(batch)
            except Exception as e:   # a full disk or a locked file must not end the writer
                self.errors += 1
                if self.errors == 1:
                    log.warning("dataset: write failed (%s: %s)", type(e).__name__, e)
            self._report(time.monotonic())
            if stop:
                self._close_files()
                return

    def _write(self, batch: list) -> None:
        touched = set()
        for stream, obj in batch:
            date = _wall_date(obj.get("t", time.time()))
            held = self._files.get(stream)
            if held is None or held[0] != date:   # a new day: a new file
                if held is not None:
                    held[1].close()
                name = f"{date}.jsonl" if stream == "rows" else f"{date}.events.jsonl"
                self.directory.mkdir(parents=True, exist_ok=True)
                held = self._files[stream] = (date, open(self.directory / name, "a", encoding="utf-8", newline="\n"))
            held[1].write(json.dumps(obj, separators=(",", ":"), ensure_ascii=False, default=str) + "\n")
            touched.add(stream)
            self.written += 1
        for stream in touched:
            self._files[stream][1].flush()

    def _close_files(self) -> None:
        for _, handle in self._files.values():
            try:
                handle.close()
            except OSError:
                pass
        self._files.clear()


# --- building the rows ---
def _tile_rows(server, tab_id: int, now: float, c: dict | None = None) -> list[dict]:
    tiles = []
    rects = (server.rects.get(tab_id) or {}).get("rects") or []
    audible = {r.get("index", pos): bool(r.get("audible")) for pos, r in enumerate(rects)}
    indexes = sorted({i for (t, i) in server.trackers if t == tab_id} | {i for (t, i) in server.watchers if t == tab_id})
    for index in indexes:
        key = (tab_id, index)
        tracker, watcher = server.trackers.get(key), server.watchers.get(key)
        choice = server.sport_choice_seen.get(key)
        mapping = server.feed_map.mapping(tab_id, index)
        score = watcher.score if watcher is not None else None
        clocks = (server.ocr.get(key) or {}).get("clocks") or []
        tiles.append({
            "i": index,
            "sport": (choice[0] if choice else None) or server.tile_sport(tab_id, index),
            "sport_src": choice[1] if choice else None,
            "state": tracker.state if tracker else None,
            "ad": bool(tracker.on_ad) if tracker else None,
            "replay": bool(tracker.replay) if tracker else None,
            "period": tracker.period if tracker else None,
            "clock": clocks[0] if clocks else None,
            "clock_min": tracker.clock_minute if tracker else None,
            "home": score[0] if score else None,
            "away": score[1] if score else None,
            "audible": audible.get(index),
            "game_key": key_text(identity.key) if (identity := server.identities.get(key)) is not None else None,   # the two team codes of the scorebug, sorted (the game this video shows; a multiplex jumps between several)
            "game_id": mapping.game_id if mapping else None,
            "league": mapping.league if mapping else None,
            "mapped_ok": mapping.verified if mapping else None,
            "score": _r(c["tile_scores"].get(index)) if c and c["tile"] is not None else None,   # multiview: the score of THIS tile (the tab scores as its best)
        })
    return tiles


def _buildup_part(server, tab_id: int, c: dict | None, now: float) -> dict:
    tile = c["tile"] if c else None   # the build-up of the tab's best tile (the only video of a tab: its own)
    key = server._bkey(tab_id, tile)
    cfg = server.scorer.tile_cfg(tab_id, tile)
    bu = server.buildup
    bu.window_s, bu.cooldown_s = cfg.buildup_window_s, cfg.buildup_cooldown_s
    w = cfg.weights
    details = bu.cue_details(key, now)
    cues_sum = sum(v for _, v, _ in details)
    support = (w.surge * c["surge"] + w.crowd * c["crowd"] + w.shout * c["shout"]) if c and server._hears(tab_id, tile) else 0.0
    level = bu.level(key, now, support)
    main = server.scorer.main
    main_c = server.scorer.components(main, now) if main is not None and main != tab_id else None
    main_score = main_c["score"] if main_c else 0.0
    main_level = server.main_buildup_level(main_c, now)   # MAIN's own build-up level: the bar a challenger must beat together with MAIN's score (as Server._check_buildup)
    part = {
        "level": _r(level),
        "cues_sum": _r(cues_sum),
        "support": _r(support),
        "cues": [[_r(age, 1), _r(v), kind] for age, v, kind in details],
        "min": cfg.buildup_min, "gap": cfg.buildup_gap, "live": cfg.buildup_live,
        "main_score": _r(main_score), "main_level": _r(main_level),
        "hot_left": _r(server.scorer.hot_left(main, now), 1) if main is not None else 0.0,   # s MAIN stays hot (its goal / strong cue); `blocked` then includes the hot margin
        "qualifies": bool(level >= cfg.buildup_min and level - max(main_score, main_level) >= cfg.buildup_gap),
    }
    part.update(bu.state(key, now))
    if tile is not None:
        part["tile"] = tile
    if tab_id == main:
        part["blocked"] = "already MAIN"
    else:
        part["blocked"] = server.scorer.take_main_block(tab_id, now, level=level, cue_age=bu.spoken_age(key, now), main_level=main_level, tile=tile)
        if part["blocked"] is None and part["qualifies"] and cfg.buildup_live > 0:   # MAIN keeps its seat after its own cue / review
            part["blocked"] = server._main_hold(main, key, now, level, main_level, cfg)
    return part


def snapshot_rows(server, now: float, wall: float | None = None) -> list[dict]:
    """One row per registered stream (see docs/dataset-format.md). `now` is time.monotonic(), `wall` time.time()."""
    wall = time.time() if wall is None else wall
    scorer = server.scorer
    rows = []
    swap = scorer.swap_context(now)
    for tab_id, stream in list(scorer.streams.items()):
        c = scorer.components(tab_id, now)
        tiles = _tile_rows(server, tab_id, now, c)
        first = next((t for t in tiles if t["home"] is not None), None)
        mapped = next((t for t in tiles if t["game_id"]), None)
        probs = server.tags.get(tab_id)
        top = None
        if probs is not None and server.classifier is not None:
            try:
                top = [[n, _r(p, 2)] for n, p in server.classifier.top(probs, 3)]
            except Exception:
                top = None
        rec = scorer._rec_of(stream, c["tile"] if c else None)   # the picture signals of the tab's best tile (the stream itself for one video)
        cue_age = now - rec.anticipation_at if rec.anticipation_at != float("-inf") else None
        goal_age = now - rec.goal_at if rec.goal_at != float("-inf") else None
        row = {
            "v": SCHEMA_VERSION, "t": _r(wall), "mono": _r(now),
            "tab": tab_id, "role": server.roles.get(tab_id), "is_main": tab_id == scorer.main,
            "sport": server._tab_sport(tab_id),
            "warm": c is not None,
            "state": c["status"] if c else scorer.tab_state(tab_id),
            "auto_state": scorer.tab_auto_state(tab_id), "override": stream.override,
            "ad": bool(c["ad"]) if c else scorer.tab_on_ad(tab_id), "on_ad": scorer.tab_on_ad(tab_id),
            "tile": c["tile"] if c else None, "tile_sport": c["sport"] if c else None,   # which video scored the tab, and its sport (None = the only video)
            "can_main": stream.can_main, "weight": stream.weight,
            "score": _r(c["score"]) if c else None,
            "surge": _r(c["surge"]) if c else None, "crowd": _r(c["crowd"]) if c else None,
            "speech": _r(c["speech"]) if c else None, "music": _r(c["music"]) if c else None,
            "shout": _r(c["shout"]) if c else None, "event": _r(c["event"]) if c else None,
            "cue_fade": _r(c["anticipation"]) if c else None, "cue_part": _r(c["cue"]) if c else None,
            "fav": c["fav"] if c else stream.favourite, "lev": _r(c["lev"], 2) if c else _r(rec.leverage, 2),
            "loud": _r(c["level"], 5) if c else None, "baseline": _r(stream.baseline, 5),
            "top": top,
            "cue": {"value": _r(rec.anticipation_value), "age": _r(cue_age, 1), "strong": rec.anticipation_strong} if cue_age is not None else None,
            "bu": _buildup_part(server, tab_id, c, now),
            "home": first["home"] if first else None, "away": first["away"] if first else None,
            "clock": first["clock"] if first else (tiles[0]["clock"] if tiles else None),
            "period": first["period"] if first else (tiles[0]["period"] if tiles else None),
            "replay": scorer.replay_of(tab_id, c["tile"] if c else None), "goal_hold": _r(scorer.goal_hold_left(tab_id, now), 1), "goal_age": _r(goal_age, 1),
            "hold": _r(scorer.hold_left(now), 1) if tab_id == scorer.hold_tab else 0.0,
            "game_id": mapped["game_id"] if mapped else None, "league": mapped["league"] if mapped else None,
            "tiles": tiles,
            "main": scorer.main, "frozen": scorer.frozen, "last_swap_age": swap["last_swap_age"],
            "swap_pending": swap["candidate"],
        }
        rows.append(row)
    return rows
