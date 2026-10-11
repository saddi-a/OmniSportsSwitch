import json

from daemon.sport_detect import FOOTBALL, SOCCER
from daemon.sport_memory import SportMemory

HOUR = 3600.0


def test_remembers_a_sport_per_tab_across_restarts(tmp_path):
    path = tmp_path / "sports.json"
    m = SportMemory(path)
    m.remember(485660207, FOOTBALL, 1000.0)
    m.remember(485660202, SOCCER, 1000.0)
    m.save()
    again = SportMemory(path)                            # the daemon restarted
    assert again.get(485660207, 1100.0) == FOOTBALL
    assert again.get(485660202, 1100.0) == SOCCER
    assert again.get(42, 1100.0) is None


def test_a_memory_older_than_the_limit_is_forgotten(tmp_path):
    m = SportMemory(tmp_path / "s.json", max_age_s=6 * HOUR)
    m.remember(1, FOOTBALL, 0.0)
    assert m.get(1, 5 * HOUR) == FOOTBALL
    assert m.get(1, 7 * HOUR) is None


def test_the_newest_detection_replaces_the_old_one(tmp_path):
    m = SportMemory(tmp_path / "s.json")
    m.remember(1, FOOTBALL, 0.0)
    m.remember(1, SOCCER, 100.0)
    assert m.get(1, 200.0) == SOCCER


def test_nothing_is_written_when_nothing_changed(tmp_path):
    path = tmp_path / "s.json"
    m = SportMemory(path)
    m.remember(1, FOOTBALL, 0.0)
    assert m.save() is True
    m.remember(1, FOOTBALL, 30.0)                        # the same answer again: refreshed in memory, no write needed yet
    assert m.save() is False
    m.remember(1, FOOTBALL, 20 * 60.0)                   # but the timestamp is refreshed now and then (every 10 minutes)
    assert m.save() is True


def test_a_missing_or_broken_file_is_an_empty_memory(tmp_path):
    assert SportMemory(tmp_path / "none.json").get(1, 0.0) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert SportMemory(bad).get(1, 0.0) is None
    odd = tmp_path / "odd.json"
    odd.write_text(json.dumps({"1": ["x"], "2": {"sport": "Chess", "at": 0}, "3": {"sport": FOOTBALL, "at": "never"}}), encoding="utf-8")
    m = SportMemory(odd)
    assert m.get(1, 0.0) is None and m.get(2, 0.0) is None and m.get(3, 0.0) is None   # unknown sports and bad entries are ignored


def test_an_unknown_sport_name_is_not_stored(tmp_path):
    m = SportMemory(tmp_path / "s.json")
    m.remember(1, "Chess", 0.0)
    m.remember(1, None, 0.0)
    assert m.get(1, 1.0) is None


def test_without_a_path_it_only_keeps_memory():
    m = SportMemory(None)
    m.remember(1, FOOTBALL, 0.0)
    assert m.get(1, 1.0) == FOOTBALL
    assert m.save() is False
