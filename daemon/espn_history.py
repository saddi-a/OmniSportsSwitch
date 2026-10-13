"""ESPN history for FINISHED soccer games (tasks T2/T4 of docs/learning-plan.md): a polite fetcher with an on-disk cache and the parsing of the
summary `commentary` list and of the core `plays` endpoint into normalised, tiered events. OFFLINE use only: nothing here is called by the daemon.

Cache (gitignored, never committed): `logs/feed/history/<league>/<event>/` = `summary.json`, `plays-001.json` ..., `meta.json`. Raw ESPN JSON and
fetched pages stay there and nowhere else. A game is fetched only when its summary says it is finished (status state `post`, i.e. FULL_TIME); a
cached finished game is NEVER fetched again (`meta.json` `complete`); a partly fetched one resumes with the missing pages. Requests are
sequential, at least `MIN_INTERVAL_S` (2 s) apart, with the daemon's User-Agent. The `fetch` and `sleep` arguments are injectable so the
tests use fakes and never touch the network.

Sources
  * summary (`site.api.espn.com/.../summary?event=<id>`): `commentary` list. Complete for a finished game. Each play appears twice (a text line
    and the play itself, same `play.id`): one event per play id. Entries without a `play` (line-ups, notes) are not events. `play.wallclock`
    (UTC) is on about 95 % of entries; a missing one is estimated from the nearest entry of the same period with one (`wallclock_est`).
    WALLCLOCK: measured on 3 finished games, `wallclock - time.value` is constant within a period (spread <= 2 s over 9-18 events per
    period), so the wallclock follows the match clock and is not the operator's entry time; it is still ESPN's own time base, not the picture's.
    `time.value` (seconds of game time) is used for the game clock: in stoppage time it keeps counting while `play.clock` freezes.
  * core plays (`sports.core.api.espn.com/v2/sports/soccer/leagues/<league>/events/<id>/competitions/<id>/plays?limit=300&page=N`, about 5 pages
    of about 1350 plays, passes dominate). Used (a) to add details to commentary events by play id (`shot_info`, `target_zone`) and (b) for
    the events the commentary does not list (`Free Kick`, the kick itself).

Normalised event: {game_id, league, wallclock_epoch, game_seconds, period, type, type_id, kind, family, tier, team, text, x, y, in_box, play_id,
src ('commentary' or 'plays'), wallclock_est}. `text` is the operator's sentence (names); it is kept in memory for matching only and the
dataset builder never writes it out.

Tier mapping v1 (docs/learning-plan.md section 3), one table, `kind` -> tier:
  tier 1  goal, own goal, penalty goal (ids 70, 137 header, 173 volley, 97, 98), red card (93), penalty AWARDED (no id: two foul entries (66)
          in the same second whose text starts 'Penalty '; one event is kept, the one that does not start 'Penalty conceded').
  tier 2  shot on target (106; kind `save` when the text says 'saved'), shot off target (117) and shot blocked (135) ONLY from inside the
          penalty area (rule below), hit woodwork (136), handball (122) only when the offender is in his own penalty area (see the caveat),
          VAR outcome (172 'VAR - Referee decision cancelled': kind `var goal` / `var no goal` / `var decision` from the text 'VAR Decision: Goal'
          / 'No Goal' / other; 175 'Deleted After Review' = kind `goal overturned`), corner (95), free kick (plays 'Free Kick', taker with
          fieldPositionX >= FREE_KICK_X 75, about 26 m from goal or closer; from further out it is tier 3).
  tier 3  foul (66), offside (68), yellow card (94), substitution (76), shots off target / blocked from outside the box, handballs elsewhere,
          free kicks from far out. Tier 3 events are parsed (and exported by `parse_*`) but the dataset builder does not load them (they
          change no label and would flood `next_event_kind`).
  No 'big chance' and no 'VAR check started' exists in the data; neither is invented.

Position rule (clear in the data, so it is used): `fieldPositionX/Y` are in the ACTING team's attacking direction, 0..100 on both axes (checked
on the corner / foul / free-kick pairs of a finished game: a foul by team A at x 7 is followed by team B's free kick at x 92). The penalty area is
16.5 m of 105 m deep and 40.3 m of 68 m wide: x >= `BOX_X` 84.0 and `BOX_Y_MIN` 20.0 <= y <= `BOX_Y_MAX` 80.0. A shot with no position falls back
to the operator's wording ('from the centre of the box', 'very close range', 'six yard box', 'penalty spot'; 'outside the box' is outside); with
neither it is tier 3. A shot's position is where the shot was taken from.
CAVEAT, handball: the same coordinates are assumed to be the OFFENDER's, so a handball in his own box is x <= 100 - BOX_X (16.0) and y in the
band. This was inferred from foul entries only (not verified on a handball that led to a penalty); a penalty AWARDED is tier 1 anyway.
"""
import datetime
import json
import os
import pathlib
import re
import time

MIN_INTERVAL_S = 2.0
CORE_BASE = "https://sports.core.api.espn.com/v2/sports/"
PLAYS_PAGE_SIZE = 300
FETCH_TIMEOUT_S = 25.0
BOX_X, BOX_Y_MIN, BOX_Y_MAX = 84.0, 20.0, 80.0
FREE_KICK_X = 75.0

# ESPN type id -> kind. Text fallback below for a type id we have not seen.
KIND_BY_ID = {"70": "goal", "137": "goal", "173": "goal", "97": "own goal", "98": "penalty goal", "93": "red card", "106": "shot on target",
              "117": "shot off target", "135": "shot blocked", "136": "hit woodwork", "95": "corner", "66": "foul", "68": "offside",
              "94": "yellow card", "122": "handball", "172": "var", "175": "goal overturned", "76": "substitution"}
KIND_BY_TEXT = {"goal": "goal", "own goal": "own goal", "penalty - scored": "penalty goal", "red card": "red card", "shot on target": "shot on target",
                "shot off target": "shot off target", "shot blocked": "shot blocked", "hit woodwork": "hit woodwork", "corner awarded": "corner",
                "foul": "foul", "offside": "offside", "yellow card": "yellow card", "handball": "handball", "substitution": "substitution",
                "deleted after review": "goal overturned", "var - referee decision cancelled": "var", "free kick": "free kick"}
FAMILY = {"goal": "goal", "own goal": "goal", "penalty goal": "goal", "red card": "red card", "penalty awarded": "penalty",
          "yellow card": "yellow card"}
TIER_BY_KIND = {"goal": 1, "own goal": 1, "penalty goal": 1, "red card": 1, "penalty awarded": 1,
                "shot on target": 2, "save": 2, "hit woodwork": 2, "var goal": 2, "var no goal": 2, "var decision": 2, "goal overturned": 2, "corner": 2,
                "foul": 3, "offside": 3, "yellow card": 3, "substitution": 3}
IN_BOX_WORDS = re.compile(r"\b(from (?:the (?:centre|center|left side|right side) of )?the box|very close range|six yard box|penalty spot)\b", re.I)


# ---------------------------------------------------------------------------------------------------------------
# Fetching (polite, cached)
# ---------------------------------------------------------------------------------------------------------------
class Pacer:
    """Waits so that two requests are at least `interval_s` apart. `clock` and `sleep` are injectable."""

    def __init__(self, interval_s: float = MIN_INTERVAL_S, clock=time.monotonic, sleep=time.sleep):
        self.interval_s, self.clock, self.sleep, self.last = interval_s, clock, sleep, None

    def wait(self) -> None:
        if self.last is not None:
            gap = self.interval_s - (self.clock() - self.last)
            if gap > 0:
                self.sleep(gap)
        self.last = self.clock()


def default_fetch(url: str):
    """(status, parsed JSON) with the daemon's User-Agent (daemon.feed.fetch_json)."""
    from daemon import feed
    return feed.fetch_json(url, timeout=FETCH_TIMEOUT_S)


def summary_url(league: str, game_id: str) -> str:
    from daemon import feed
    return feed.summary_url(league, game_id)


def plays_url(league: str, game_id: str, competition_id: str, page: int = 1) -> str:
    return f"{CORE_BASE}soccer/leagues/{league}/events/{game_id}/competitions/{competition_id}/plays?limit={PLAYS_PAGE_SIZE}&page={page}"


def game_dir(cache_dir, league: str, game_id: str) -> pathlib.Path:
    return pathlib.Path(cache_dir) / league / str(game_id)


def _read_json(path: pathlib.Path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_json(path: pathlib.Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def summary_status(summary: dict) -> tuple:
    """(finished, status name, competition id) of a summary. Finished = status state `post` and completed."""
    try:
        comp = summary["header"]["competitions"][0]
        st = comp["status"]["type"]
        return (st.get("state") == "post" and bool(st.get("completed", True))), st.get("name"), str(comp.get("id") or "")
    except (KeyError, IndexError, TypeError, AttributeError):
        return False, None, ""


def is_complete(cache_dir, league: str, game_id: str) -> bool:
    meta = _read_json(game_dir(cache_dir, league, game_id) / "meta.json")
    return bool(meta and meta.get("complete"))


def fetch_game(league: str, game_id: str, cache_dir, fetch=default_fetch, pacer: Pacer | None = None, with_plays: bool = True,
               now=time.time) -> dict:
    """Fetches and caches ONE game. Returns {game, league, action, requests, pages, status}; action = `cached` (complete in the cache: no request),
    `fetched`, `not-finished` (the summary does not say post/FULL_TIME: nothing is stored), `error` (a failed request: what was stored is kept and a
    rerun resumes). Never refetches a complete game; a partial one fetches only what is missing."""
    pacer = pacer or Pacer()
    d = game_dir(cache_dir, league, game_id)
    report = {"game": str(game_id), "league": league, "action": None, "requests": 0, "pages": 0, "status": None}
    meta = _read_json(d / "meta.json") or {}
    if meta.get("complete"):
        report.update(action="cached", status=meta.get("status"))
        return report
    summary = _read_json(d / "summary.json")
    if summary is None:
        pacer.wait()
        status, summary = fetch(summary_url(league, game_id))
        report["requests"] += 1
        if status != 200 or not isinstance(summary, dict):
            report.update(action="error", status=f"summary HTTP {status}")
            return report
    finished, name, comp_id = summary_status(summary)
    report["status"] = name
    if not finished:
        report["action"] = "not-finished"
        return report
    _write_json(d / "summary.json", summary)
    pages = meta.get("page_count")
    if with_plays:
        page = 1
        while pages is None or page <= pages:
            path = d / f"plays-{page:03d}.json"
            data = _read_json(path)
            if data is None:
                pacer.wait()
                status, data = fetch(plays_url(league, game_id, comp_id or str(game_id), page))
                report["requests"] += 1
                if status != 200 or not isinstance(data, dict) or not isinstance(data.get("items"), list):
                    _write_json(d / "meta.json", {**meta, "complete": False, "status": name, "competition": comp_id, "page_count": pages})
                    report.update(action="error", status=f"plays page {page} HTTP {status}")
                    return report
                _write_json(path, data)
                report["pages"] += 1
            if pages is None:
                pages = int(data.get("pageCount") or 1)
                meta["page_count"] = pages
            page += 1
    _write_json(d / "meta.json", {"complete": True, "status": name, "competition": comp_id, "page_count": pages if with_plays else None,
                                  "plays": bool(with_plays), "fetched_at": int(now())})
    report["action"] = "fetched"
    return report


def load_game(cache_dir, league: str, game_id: str) -> tuple:
    """(summary or None, plays items list) from the cache."""
    d = game_dir(cache_dir, league, game_id)
    summary = _read_json(d / "summary.json")
    items = []
    for path in sorted(d.glob("plays-*.json")):
        data = _read_json(path)
        if data:
            items.extend(data.get("items") or [])
    return summary, items


# ---------------------------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------------------------
def parse_wallclock(text) -> float | None:
    """'2026-10-13T16:55:33Z' -> epoch seconds (UTC)."""
    if not isinstance(text, str):
        return None
    try:
        d = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d.timestamp()


def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def in_box(x, y, text: str = "") -> bool | None:
    """True / False from the position, else from the wording, else None (unknown)."""
    if x is not None and y is not None:
        return x >= BOX_X and BOX_Y_MIN <= y <= BOX_Y_MAX
    if re.search(r"outside the box", text or "", re.I):
        return False
    if IN_BOX_WORDS.search(text or ""):
        return True
    return None


def classify(type_id, type_text, text: str, x, y) -> tuple:
    """(kind, tier) of one play, or (None, None) for a play that is not an event (pass, kickoff, ...)."""
    kind = KIND_BY_ID.get(str(type_id)) or KIND_BY_TEXT.get(str(type_text or "").strip().lower())
    if kind is None:
        return None, None
    low = (text or "").lower()
    if kind == "var":
        kind = "var goal" if "var decision: goal" in low else "var no goal" if "no goal" in low else "var decision"
        return kind, 2
    if kind == "shot on target":
        return ("save" if "saved" in low else kind), 2
    if kind in ("shot off target", "shot blocked"):
        box = in_box(x, y, text)
        return kind, 2 if box else 3
    if kind == "handball":
        own_box = x is not None and y is not None and x <= 100.0 - BOX_X and BOX_Y_MIN <= y <= BOX_Y_MAX
        return kind, 2 if own_box else 3
    if kind == "foul" and low.startswith("penalty "):
        return "penalty awarded", 1
    if kind == "free kick":
        return kind, 2 if (x is not None and x >= FREE_KICK_X) else 3
    return kind, TIER_BY_KIND.get(kind, 3)


def _event(game_id, league, wallclock, game_seconds, period, play: dict, kind, tier, team, text, x, y, src, est=False, extra=None) -> dict:
    ev = {"game_id": str(game_id), "league": league, "wallclock_epoch": wallclock, "game_seconds": game_seconds, "period": period,
          "type": (play.get("type") or {}).get("text"), "type_id": str((play.get("type") or {}).get("id")), "kind": kind,
          "family": FAMILY.get(kind), "tier": tier, "team": team, "text": text, "x": x, "y": y, "in_box": in_box(x, y, text) if x is not None else None,
          "play_id": str(play.get("id")) if play.get("id") is not None else None, "src": src, "wallclock_est": est}
    if extra:
        ev.update(extra)
    return ev


def _fill_wallclocks(entries: list) -> None:
    """Gives each entry without a wallclock one estimated from the nearest entry of the same period that has one (in place)."""
    known = [e for e in entries if e["wallclock_epoch"] is not None and e["game_seconds"] is not None]
    for e in entries:
        if e["wallclock_epoch"] is None and e["game_seconds"] is not None:
            near = [k for k in known if k["period"] == e["period"]] or known
            if near:
                k = min(near, key=lambda k: abs(k["game_seconds"] - e["game_seconds"]))
                e["wallclock_epoch"] = k["wallclock_epoch"] + (e["game_seconds"] - k["game_seconds"])
                e["wallclock_est"] = True


def _merge_penalty_awarded(events: list, window_s: float = 5.0) -> list:
    """The two foul entries of one awarded penalty ('Penalty <team>. ...' and 'Penalty conceded by ...') become one event (the one that does not
    say 'conceded' is kept)."""
    out = []
    for e in events:
        if e["kind"] == "penalty awarded":
            same = [o for o in out if o["kind"] == "penalty awarded" and abs((o["wallclock_epoch"] or 0) - (e["wallclock_epoch"] or 0)) <= window_s]
            if same:
                if "conceded" in (same[0]["text"] or "").lower() and "conceded" not in (e["text"] or "").lower():
                    out[out.index(same[0])] = e
                continue
        out.append(e)
    return out


def _merge_goal_variants(events: list, window_s: float = 5.0) -> list:
    """One goal event per goal when ESPN lists a 'Goal' and a header / volley / penalty entry of it within a few seconds (same team)."""
    out = []
    for e in events:
        if e["family"] == "goal" and any(o["family"] == "goal" and o["team"] == e["team"] and abs((o["wallclock_epoch"] or 0) - (e["wallclock_epoch"] or 0)) <= window_s
                                         for o in out):
            continue
        out.append(e)
    return out


def parse_commentary(summary: dict, league: str, game_id: str) -> list:
    """Normalised events (all tiers) from the summary `commentary` list, one per play id, sorted by game time."""
    seen, entries = set(), []
    for c in (summary or {}).get("commentary") or []:
        play = c.get("play") or {}
        pid = play.get("id")
        if not play or pid is None or pid in seen:
            continue
        seen.add(pid)
        ptype = play.get("type") or {}
        x, y = _num(play.get("fieldPositionX")), _num(play.get("fieldPositionY"))
        text = play.get("text") or c.get("text") or ""
        kind, tier = classify(ptype.get("id"), ptype.get("text"), text, x, y)
        gs = _num((c.get("time") or {}).get("value"))
        period = (play.get("period") or {}).get("number")
        team = (play.get("team") or {}).get("displayName")
        entries.append(_event(game_id, league, parse_wallclock(play.get("wallclock")), gs, period, play, kind, tier, team, text, x, y, "commentary"))
    _fill_wallclocks(entries)
    events = [e for e in entries if e["kind"] is not None]
    events = _merge_goal_variants(_merge_penalty_awarded(events))
    return sorted(events, key=lambda e: (e["wallclock_epoch"] if e["wallclock_epoch"] is not None else 0.0, e["game_seconds"] or 0.0))


def _team_id(play: dict):
    ref = (play.get("team") or {}).get("$ref") or ""
    m = re.search(r"/teams/(\d+)", ref)
    return m.group(1) if m else None


def parse_plays(items: list, league: str, game_id: str, commentary: list | None = None) -> tuple:
    """(details, extra events) from the core plays. `details` = play id -> {shot_info, target_zone, penalty_kick, scoring_play} for commentary
    events; extra events = plays the commentary does not list, of a mapped kind (so far the `Free Kick`), `src` 'plays', team = ESPN team id."""
    known = {e["play_id"] for e in (commentary or [])}
    details, extra = {}, []
    for p in items or []:
        pid = str(p.get("id"))
        if p.get("shotInfo") or p.get("targetZone") or p.get("penaltyKick") or p.get("scoringPlay"):
            details[pid] = {"shot_info": (p.get("shotInfo") or {}).get("text"), "target_zone": (p.get("targetZone") or {}).get("text"),
                            "penalty_kick": bool(p.get("penaltyKick")), "scoring_play": bool(p.get("scoringPlay"))}
        if pid in known or (p.get("shootout") is True):
            continue
        ptype = p.get("type") or {}
        x, y = _num(p.get("fieldPositionX")), _num(p.get("fieldPositionY"))
        kind, tier = classify(ptype.get("id"), ptype.get("text"), p.get("text") or "", x, y)
        if kind != "free kick":                # the other mapped kinds are all in the commentary; passes and the rest are no events
            continue
        extra.append(_event(game_id, league, parse_wallclock(p.get("wallclock")), _num((p.get("clock") or {}).get("value")),
                            (p.get("period") or {}).get("number"), p, kind, tier, _team_id(p), p.get("text") or "", x, y, "plays"))
    return details, sorted(extra, key=lambda e: e["wallclock_epoch"] or 0.0)


def game_events(cache_dir, league: str, game_id: str, tiers=(1, 2)) -> list:
    """The normalised events of one cached game, only the given tiers (default 1 and 2), commentary + plays-only events, with the plays details
    merged in. [] when the game is not in the cache."""
    summary, items = load_game(cache_dir, league, game_id)
    if not summary:
        return []
    com = parse_commentary(summary, league, game_id)
    details, extra = parse_plays(items, league, game_id, com)
    for e in com:
        e.update(details.get(e["play_id"], {}))
    return [e for e in sorted(com + extra, key=lambda e: e["wallclock_epoch"] or 0.0) if e["tier"] in tiers and e["wallclock_epoch"] is not None]
