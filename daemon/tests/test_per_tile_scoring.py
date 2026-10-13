"""Stage 2 of the per-tile sport work (docs/per-tile-sport.md, task T3): the Scorer keeps the picture signals of each tile of a multiview
tab apart, scores each with its own sport's settings, gives the audio to the audible tile only, and scores the tab as its best tile."""
import json
import random

import pytest

from daemon.scoring import Config, Scorer
from daemon.tests.golden_single_tile import FIXTURE, run
from daemon.tests.test_scoring import make, roar, steady, warm_up

NFL, SOCCER = "american football", "football"


def mixed(audible=0, **cfg):
    """Tab 2 is a multiview: tile 0 American football, tile 1 football (soccer); `audible` has the sound. Tabs 1 (MAIN) and 3 are single."""
    s = make(**cfg)
    sports = {0: NFL, 1: SOCCER}
    s.tile_sport_of = lambda tab, tile: sports.get(tile)
    s.sport_of = lambda tab: sports.get(box[0]) if tab == 2 else None
    box = [audible]
    s.audible_of = lambda tab: box[0] if tab == 2 else None
    s.set_tiles(2, [0, 1])
    s.audible_box = box
    return s


def hear(s, tab=2, start=0, seconds=60, crowd=0.05):
    for t in range(start, start + seconds):
        s.add_audio(tab, 0.05, t)
        s.set_tags(tab, crowd, 0.1, 0.0, t)
    return start + seconds


def test_single_tile_golden_sequence_is_unchanged():
    """The recorded sequence (soccer goal + VAR, American football replay, an ad, build-up swaps) gives the same scores and swaps as before."""
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert json.loads(json.dumps(run())) == expected


def test_a_tile_with_only_one_video_matches_the_default_slot_on_random_sequences():
    rng = random.Random(7)
    for _ in range(25):
        a, b = make(), make()
        for s, tile in ((a, None), (b, 0)):
            s.tile_sport_of = lambda tab, t: None
            if tile is not None:
                s.set_tiles(2, [0])
        now = warm_up(a)
        warm_up(b)
        for step in range(40):
            now += rng.uniform(0.5, 4)
            action = rng.choice(["event", "goal", "cue", "strong", "lev", "ad", "state", "roar"])
            for s, tile in ((a, None), (b, 0)):
                if action == "event":
                    s.set_event(2, now, random.Random(step).random(), tile=tile)
                elif action == "goal":
                    s.set_event(2, now, 1.0, goal=True, tile=tile)
                elif action == "cue":
                    s.set_anticipation(2, now, 0.25, tile=tile)
                elif action == "strong":
                    s.set_anticipation(2, now, 0.5, strong=True, tile=tile)
                elif action == "lev":
                    s.set_leverage(2, step / 20 - 1, tile=tile)
                elif action == "ad":
                    s.set_on_ad(2, step % 2 == 0, now, tile=tile)
                elif action == "state":
                    s.set_auto_state(2, random.Random(step).choice(["live", "break", "over"]), tile=tile)
                else:
                    s.add_audio(2, 0.15, now)
                    s.set_tags(2, 0.7, 0.1, 0.0, now)
            ca, cb = a.components(2, now), b.components(2, now)
            assert (ca is None) == (cb is None)
            if ca is not None:
                ignore = ("tile", "tile_scores")
                assert {k: v for k, v in ca.items() if k not in ignore} == {k: v for k, v in cb.items() if k not in ignore}


def test_components_keeps_its_keys_and_adds_tile_and_sport():
    s = make()
    now = warm_up(s)
    c = s.components(2, now)
    old = {"score", "ad", "surge", "crowd", "speech", "music", "shout", "anticipation", "cue", "event", "fav", "lev", "level", "status"}
    assert old <= set(c) and {"tile", "sport", "tile_scores"} <= set(c)
    assert c["tile"] is None and c["sport"] is None


def test_two_tiles_keep_independent_records_each_scored_with_its_own_sport():
    s = mixed(audible=1)
    s.config.sport_weights = {NFL: {"event": 0.4}, SOCCER: {"event": 1.0}}
    now = hear(s)
    s.set_anticipation(2, now, 0.25, tile=0)               # an NFL cue on tile 0 ...
    s.set_event(2, now, 1.0, goal=True, tile=1)             # ... and a soccer goal on tile 1: two records, neither overwrites the other
    stream = s.streams[2]
    assert stream.tiles[0].anticipation_value == 0.25 and stream.tiles[0].event_at == float("-inf")
    assert stream.tiles[1].event_at == now and stream.tiles[1].anticipation_value == 0.0
    c = s.components(2, now)
    assert c["tile"] == 1 and c["sport"] == SOCCER
    assert c["tile_scores"][1] == pytest.approx(c["score"]) and c["tile_scores"][0] < c["score"]
    assert c["tile_scores"][1] == pytest.approx(1.0 * 1.0 + 0.3 * 0.0, abs=0.2)     # the soccer event weight (1.0), not the NFL one (0.4)
    s.set_event(2, now, 1.0, goal=True, tile=0)                                      # the same goal on the NFL tile counts 0.4 (+ its 0.25 cue)
    assert s.components(2, now)["tile_scores"][0] == pytest.approx(0.65, abs=0.03)
    assert s.components(2, now)["tile_scores"][0] < s.components(2, now)["tile_scores"][1]


def test_the_tab_scores_as_its_best_tile_and_reports_the_winning_tile_and_sport():
    s = mixed(audible=0)
    now = hear(s)
    s.set_event(2, now, 1.0, goal=True, tile=1)              # the goal is on the silent soccer tile
    c = s.components(2, now)
    assert c["tile"] == 1 and c["sport"] == SOCCER and c["score"] == max(c["tile_scores"].values())
    assert s.tile_scores(2, now) == c["tile_scores"]


def test_audio_terms_count_only_for_the_audible_tile():
    s = mixed(audible=0)                                     # the NFL tile has the sound
    now = hear(s)
    now = roar(s, 2, now, crowd=0.9)                         # a crowd roar
    c = s.components(2, now)
    assert c["crowd"] > 0.5                                  # the tab's audio is reported
    assert c["tile_scores"][0] > 0.5 and c["tile_scores"][1] == pytest.approx(0.0)    # ...but the silent soccer tile does not gain from it
    assert c["tile"] == 0


def test_when_the_sound_moves_the_audio_terms_follow_and_stored_picture_signals_stay_with_their_tile():
    s = mixed(audible=0)
    now = hear(s)
    s.set_event(2, now, 1.0, goal=True, tile=1)
    now = roar(s, 2, now, crowd=0.9)
    before = s.components(2, now)
    assert before["tile_scores"][0] > 0.5
    s.audible_box[0] = 1
    after = s.components(2, now)
    assert after["tile_scores"][0] == pytest.approx(0.0, abs=0.02)        # the NFL tile no longer hears the roar
    assert after["tile_scores"][1] >= before["tile_scores"][1]              # the soccer tile keeps its goal and now has the roar too
    assert s.streams[2].tiles[1].event_at == now - 0.0 + 0.0 or s.streams[2].tiles[1].event_at > float("-inf")
    assert s.streams[2].tiles[0].event_at == float("-inf")


def test_unknown_audible_tile_lets_every_tile_hear_the_audio():
    s = mixed(audible=None)
    now = roar(s, 2, hear(s), crowd=0.9)
    c = s.components(2, now)
    assert c["tile_scores"][0] == pytest.approx(c["tile_scores"][1]) and c["tile_scores"][0] > 0.5


def test_each_tile_has_its_own_goal_hold_read_with_its_own_sport():
    s = mixed(audible=1)
    s.config.sport_scoring = {NFL: {"goal_hold_s": 10.0}, SOCCER: {"goal_hold_s": 40.0}}
    now = hear(s)
    s.set_event(2, now, 1.0, goal=True, tile=0)
    s.set_event(2, now, 1.0, goal=True, tile=1)
    assert s.goal_hold_left(2, now + 5, tile=0) == pytest.approx(5.0)
    assert s.goal_hold_left(2, now + 5, tile=1) == pytest.approx(35.0)
    assert s.goal_hold_left(2, now + 20, tile=0) == 0.0 and s.goal_hold_left(2, now + 20) == pytest.approx(20.0)   # the tab's = the longest
    s.note_replay(2, now + 8, tile=0)                          # a replay on the NFL tile extends only the NFL hold
    assert s.goal_hold_left(2, now + 12, tile=0) == pytest.approx(s.config.for_sport(NFL).goal_hold_extend_s - 4)
    assert s.streams[2].tiles[1].replay_seen_at == float("-inf")
    s.set_replay(2, True, now + 9, tile=1)
    assert s.streams[2].tiles[1].replay_seen_at == now + 9 and s.replay_of(2, 1) and not s.replay_of(2, 0)


def test_a_football_goal_does_not_hold_an_nfl_cue_and_the_other_way_round():
    s = mixed(audible=1)
    now = hear(s)
    s.set_event(2, now, 1.0, goal=True, tile=1)
    assert s.goal_hold_left(2, now + 1, tile=1) > 0 and s.goal_hold_left(2, now + 1, tile=0) == 0.0
    s.set_anticipation(2, now + 1, 0.25, tile=0)
    assert s.streams[2].tiles[0].goal_at == float("-inf")


def test_the_post_goal_hold_of_main_uses_the_tile_that_scored():
    s = mixed(audible=1)
    s.config.sport_scoring = {NFL: {"goal_hold_s": 10.0}, SOCCER: {"goal_hold_s": 40.0}}
    s.main = 2
    s.streams[1].can_main = True
    now = hear(s)
    for tab in (1, 3):
        hear(s, tab=tab)
    s.set_event(2, now, 1.0, goal=True, tile=0)              # the NFL tile of MAIN scored: a 10 s hold only
    assert s.take_main_block(1, now + 20) is None            # (dwell 20 s passed, hold over)
    s.set_event(2, now + 20, 1.0, goal=True, tile=1)         # the soccer tile scores: the 40 s hold applies
    assert "goal on MAIN" in s.take_main_block(1, now + 30)


def test_a_replay_or_ad_or_state_on_one_tile_does_not_touch_the_other():
    s = mixed(audible=0)
    now = hear(s)
    s.set_on_ad(2, True, now, tile=0)
    assert not s.tab_on_ad(2) and s.streams[2].tiles[1].on_ad is False
    c = s.components(2, now)
    assert c["ad"] is False and c["tile_scores"][0] < 0 <= c["tile_scores"][1]
    s.set_on_ad(2, True, now, tile=1)
    assert s.tab_on_ad(2) and s.components(2, now)["ad"] is True
    s.set_on_ad(2, False, now, tile=0)
    s.set_on_ad(2, False, now, tile=1)
    s.set_auto_state(2, "over", tile=0)
    assert s.tab_state(2) == "live"                          # live if any tile is live
    s.set_auto_state(2, "break", tile=1)
    assert s.tab_state(2) == "break"


def test_leverage_is_per_tile_and_read_with_each_tiles_sport():
    s = mixed(audible=0)
    s.config.sport_scoring = {NFL: {"leverage_weight": 0.5}, SOCCER: {"leverage_weight": 0.1}}
    now = hear(s)
    s.set_leverage(2, 1.0, tile=0)
    s.set_leverage(2, 1.0, tile=1)
    c = s.components(2, now)
    assert c["tile_scores"][0] == pytest.approx(0.5, abs=0.06) and c["tile_scores"][1] == pytest.approx(0.1, abs=0.02)
    assert c["tile"] == 0 and c["lev"] == 1.0


def test_decide_judges_the_challenger_by_the_sport_of_its_best_tile():
    s = mixed(audible=1)
    s.config.sport_scoring = {NFL: {"min_score": 5.0}, SOCCER: {"min_score": 0.2}}      # the NFL tile can never win; the soccer tile easily
    now = hear(s)
    for tab in (1, 3):
        hear(s, tab=tab)
    s.set_event(2, now, 1.0, goal=True, tile=1)
    swap = s.decide(now)
    assert swap is not None and swap.promote == 2
    t = mixed(audible=0)
    t.config.sport_scoring = {NFL: {"min_score": 5.0}, SOCCER: {"min_score": 0.2}}
    now = hear(t)
    for tab in (1, 3):
        hear(t, tab=tab)
    t.set_event(2, now, 1.0, goal=True, tile=0)
    assert t.decide(now) is None


def test_set_tiles_drops_the_records_of_videos_that_are_gone_and_swap_tiles_exchanges_two():
    s = mixed()
    s.set_event(2, 5.0, 1.0, goal=True, tile=1)
    s.swap_tiles(2, 0, 1)
    assert s.streams[2].tiles[0].goal_at == 5.0 and s.streams[2].tiles[1].goal_at == float("-inf")
    s.set_tiles(2, [1])
    assert list(s.streams[2].tiles) == [1]
    s.set_tiles(2, [])
    assert s.streams[2].tiles == {}                           # a single video again: the stream's own record counts


def test_no_sport_anywhere_scores_like_before():
    s = make()
    t = make()
    t.set_tiles(2, [0, 1])
    now = warm_up(s)
    warm_up(t)
    s.set_event(2, now, 1.0, goal=True)
    t.set_event(2, now, 1.0, goal=True, tile=0)
    for tab in (s, t):
        tab.add_audio(2, 0.15, now)
        tab.set_tags(2, 0.7, 0.1, 0.0, now)
    assert s.components(2, now)["score"] == t.components(2, now)["score"]


def test_hot_episode_is_per_tile():
    s = mixed(audible=1)
    now = hear(s)
    s.set_anticipation(2, now, 0.5, strong=True, tile=1)
    assert s.hot_left(2, now + 1) > 0
    s.set_on_ad(2, True, now + 1, tile=1)                     # an ad on the soccer tile ends ITS episode
    assert s.hot_left(2, now + 2) == 0.0
