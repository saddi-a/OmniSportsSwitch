"""Tiny synthetic data for the tests (no logs, no network, no real settings)."""
import json
import random

from daemon.tests import test_dataset_builder as tdb

T0 = tdb.T0


def built_rows(n_games: int, events_per_game: int = 3, seed: int = 1, sport: str = "football", rows_per_game: int = 300) -> list[dict]:
    """Rows in the dataset builder's output shape (`rows.jsonl`): aligned, labelled, live. In each game positives follow a rise of crowd and surge,
    so a model has something to learn. `surge` carries the game index in its integer part (x10) only when `tag_games` is wanted: see games_tagged."""
    rng = random.Random(seed)
    rows = []
    for g in range(n_games):
        key = f"g{g}"
        event_ts = [40 + (rows_per_game * 2 - 100) * (k + 1) // (events_per_game + 1) for k in range(events_per_game)]
        for i in range(rows_per_game):
            t = T0 + 2 * i + g * 100000
            pt = 2 * i
            nxt = next((e - pt for e in event_ts if 0 < e - pt <= 600), None)
            pos = int(nxt is not None and nxt <= 60)
            goal = int(nxt is not None and nxt <= 30)
            crowd = min(1.0, max(0.0, (0.5 if pos else 0.1) + rng.uniform(-0.1, 0.1)))
            rows.append({"date": "d", "t": t, "tab": g + 1, "game_key": key, "game_id": key, "sport_used": sport, "sport": sport, "state": "live", "ad": False,
                         "aligned": True, "y_t12_60": pos, "y_goal_30": goal, "label_weight": 1.0, "secs_to_next_event": nxt,
                         "surge": min(1.0, max(0.0, (0.4 if pos else 0.0) + rng.uniform(0, 0.1))), "crowd": crowd,
                         "speech": rng.uniform(0.3, 0.8), "music": rng.uniform(0.0, 0.1), "shout": 0.0, "fav": 1.0, "lev": 0.0,
                         "event": 0.0, "cue_part": 0.1 if pos else 0.0})
    return rows


def write_jsonl(path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def signal_rows(crowd_of, span: int = tdb.SPAN, goal_off: int | None = None) -> tuple:
    """(rows, recorded swap events) of a tiny day: tab 1 (game gA) is Main, tab 2 (gB) gets loud (`crowd_of(tab, offset)`); recorded Main follows
    the rule `crowd_of(2) > 0.5 -> tab 2 from offset 102`."""
    swap_at = 102
    rows = tdb.make_rows(lambda t: 1 if t < T0 + swap_at else 2, span=span)
    for r in rows:
        off = r["t"] - T0
        r.update(warm=True, can_main=True, weight=1, on_ad=False, surge=0.0, crowd=crowd_of(r["tab"], off), speech=0.0, music=0.0, shout=0.0, event=0.0,
                 cue=None, cue_fade=0.0, cue_part=0.0, fav=1.0, lev=0.0, goal_hold=0.0, hold=0.0, score=None)
    events = [tdb.swap(swap_at, 2, 1, source="decide")]
    return rows, events
