"""Remembers the sport detected on each tab, so that a daemon restart does not start every tab as "unknown sport".

The lowest source of the sport (see `PLAN.md`): only a start guess, any evidence replaces it. Pure apart from the one JSON file whose
path the caller gives (`None` = memory only); callers pass wall-clock seconds (`time.time()`), because the memory outlives the process.
"""
import json
import logging
import os
import pathlib

from daemon.sport_detect import BASKETBALL, FOOTBALL, SOCCER

log = logging.getLogger("sport_memory")

KNOWN = {FOOTBALL, SOCCER, BASKETBALL}
MAX_AGE_S = 12 * 3600.0      # tab ids last as long as the browser session; an old memory is not trusted
REFRESH_S = 600.0            # the same answer again only rewrites the file this often


class SportMemory:
    def __init__(self, path: str | os.PathLike | None = None, max_age_s: float = MAX_AGE_S):
        self.path = pathlib.Path(path) if path else None
        self.max_age_s = max_age_s
        self._items: dict[str, tuple[str, float]] = {}   # str(tab id) -> (sport, when it was last seen)
        self._saved_at: dict[str, float] = {}            # what the file holds, to know when a write is due
        self._dirty = False
        self._load()

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("sport memory file unreadable, starting empty: %s", self.path)
            return
        if not isinstance(raw, dict):
            return
        for key, entry in raw.items():
            if isinstance(entry, dict) and entry.get("sport") in KNOWN and isinstance(entry.get("at"), (int, float)):
                self._items[str(key)] = (entry["sport"], float(entry["at"]))
                self._saved_at[str(key)] = float(entry["at"])

    def get(self, tab_id: int, now: float) -> str | None:
        item = self._items.get(str(tab_id))
        if item is None or now - item[1] > self.max_age_s:
            return None
        return item[0]

    def remember(self, tab_id: int, sport: str | None, now: float) -> None:
        if sport not in KNOWN:
            return
        key = str(tab_id)
        old = self._items.get(key)
        self._items[key] = (sport, now)
        if old is None or old[0] != sport or now - self._saved_at.get(key, float("-inf")) >= REFRESH_S:
            self._dirty = True

    def save(self) -> bool:
        """Writes the file when something worth writing changed; True when it did."""
        if self.path is None or not self._dirty:
            return False
        data = {k: {"sport": s, "at": at} for k, (s, at) in self._items.items()}
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.warning("sport memory could not be saved: %s", self.path)
            return False
        self._saved_at = {k: at for k, (_, at) in self._items.items()}
        self._dirty = False
        return True
