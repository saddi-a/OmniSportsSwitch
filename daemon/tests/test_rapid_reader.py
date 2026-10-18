"""The second OCR reader (daemon/rapid_reader.py): region geometry, merging with the first reader's items, the rescue of a missing score and
clock, and the fail-safe behaviour. No OCR engine is needed: the engine is a fake that returns canned results."""
import numpy as np

from daemon import rapid_reader
from daemon.rapid_reader import RapidReader, has_scorebug_anchor, merge_items, needs_rescue, regions, to_items
from daemon.score_slots import windows
from daemon.score_watch import find_score_geo
from daemon.screen_ocr import find_clocks


def item(text, x0, y0, x1, y1, conf=0.9):
    return {"text": text, "conf": conf, "x0": x0, "y0": y0, "x1": x1, "y1": y1}


def box(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


# ---- geometry ---------------------------------------------------------------------------------------------------------------------

def test_the_regions_are_the_top_and_bottom_bands_at_double_size_and_optionally_the_whole_frame():
    found = {name: (y0, y1, scale) for name, y0, y1, scale in regions(1000)}
    assert "full" not in found                       # bands only by default (measured: as good and faster)
    assert found["bottom"] == (620, 1000, 2.0)
    assert found["top"] == (0, 300, 2.0)
    assert {name: (y0, y1, s) for name, y0, y1, s in regions(1000, full=True)}["full"] == (0, 1000, 1.0)


def test_engine_boxes_are_mapped_back_to_the_frame():
    raw = [(box(20, 40, 120, 80), "17", 0.97), (box(0, 0, 10, 10), " ", 0.9)]
    out = to_items(raw, ox=0, oy=500, scale=2.0)
    assert out == [{"text": "17", "conf": 0.97, "x0": 10, "y0": 520, "x1": 60, "y1": 540}]   # the blank text is dropped


# ---- merging ----------------------------------------------------------------------------------------------------------------------

def test_an_item_that_overlaps_nothing_is_added():
    merged = merge_items([item("ARIZONA ST", 0, 0, 100, 20)], [item("9:34", 300, 0, 360, 20)])
    assert [i["text"] for i in merged] == ["ARIZONA ST", "9:34"]


def test_a_word_read_by_both_readers_keeps_the_first_readers_box():
    merged = merge_items([item("TEXAS TECH", 0, 0, 100, 20)], [item("TEXAS TECH", 2, 1, 101, 21), item("TEX", 3, 2, 99, 19)])
    assert [i["text"] for i in merged] == ["TEXAS TECH"]


def test_a_number_read_by_both_readers_takes_the_second_readers_value():
    # the first reader sees 43 as 48 (the 3 is read as an 8); the second reader's digits win
    merged = merge_items([item("48", 100, 0, 140, 40)], [item("43", 101, 1, 141, 41)])
    assert [i["text"] for i in merged] == ["43"]


# ---- when the second reader is needed ---------------------------------------------------------------------------------------------

def test_a_scorebug_anchor_is_a_clock_a_down_a_quarter_or_a_team_name():
    assert has_scorebug_anchor([item("9:34", 0, 0, 50, 20)])
    assert has_scorebug_anchor([item("1st & 10", 0, 0, 80, 20)])
    assert has_scorebug_anchor([item("3RD", 0, 0, 40, 20)])
    assert has_scorebug_anchor([item("ARIZONA ST", 0, 0, 150, 24)])
    assert not has_scorebug_anchor([item("Follow", 0, 0, 80, 20), item("NFL GAME PASS", 0, 30, 200, 50)])


def test_the_rescue_is_wanted_when_the_score_or_the_clock_is_missing_and_a_scorebug_is_there():
    only_names = [item("ARIZONA ST", 0, 400, 150, 424), item("TEXAS TECH", 400, 400, 560, 424)]
    assert needs_rescue(only_names)                                   # names, no score, no clock
    nothing = [item("Follow", 0, 0, 80, 20)]
    assert not needs_rescue(nothing)                                  # no scorebug at all: an ad or a graphic


# ---- the reader --------------------------------------------------------------------------------------------------------------------

class FakeEngine:
    """Detection answers by image height (the bands are told apart by it); recognition-only answers come one after the other."""
    def __init__(self, by_height, rec_answers=()):
        self.by_height, self.rec_answers, self.calls = by_height, list(rec_answers), []

    def __call__(self, image, use_det=True, use_cls=True, use_rec=True):
        self.calls.append((image.shape[:2], use_det))
        if not use_det:
            return ([(self.rec_answers.pop(0), 0.97)] if self.rec_answers else None), None
        return self.by_height.get(image.shape[0]), None


def test_read_items_covers_the_regions_and_merges_them():
    image = np.zeros((100, 300, 3), dtype=np.uint8)
    engine = FakeEngine({60: [(box(10, 10, 60, 30), "ARIZONA ST", 0.99)], 76: [(box(100, 20, 160, 60), "9:34", 0.98)]})   # top band 30 px, bottom band 38 px; at 2x 60 and 76 px tall
    reader = RapidReader(engine=engine)
    texts = [i["text"] for i in reader.read_items(image)]
    assert "ARIZONA ST" in texts and "9:34" in texts


def test_a_failing_engine_never_raises_and_is_switched_off_after_a_few_errors():
    def broken(*args, **kwargs):
        raise RuntimeError("onnx failed")
    reader = RapidReader(engine=broken)
    image = np.zeros((100, 300, 3), dtype=np.uint8)
    for _ in range(rapid_reader.MAX_ERRORS + 2):
        assert reader.read_items(image) == []
    assert not reader.available


def test_a_missing_package_means_no_second_reader_and_no_crash(monkeypatch):
    monkeypatch.setattr(rapid_reader, "_load_engine", lambda gpu=False: None)
    reader = RapidReader()
    assert not reader.available
    items = [item("ARIZONA ST", 0, 0, 150, 24)]
    assert reader.rescue(np.zeros((50, 50, 3), dtype=np.uint8), items) == items   # the first reader's items come back untouched


# ---- the rescue --------------------------------------------------------------------------------------------------------------------

def test_the_rescue_adds_the_missing_score_digits_between_the_team_names():
    # a scorebug where only the names and the clock were read: the digits come from the slot reading
    image = np.zeros((200, 900, 3), dtype=np.uint8)
    items = [item("ARIZONA ST", 100, 100, 300, 130), item("TEXAS TECH", 560, 100, 760, 130), item("9:34", 380, 140, 440, 160)]
    n_left = len(windows(items[0], items[1], "left"))
    reader = RapidReader(engine=FakeEngine({}, rec_answers=["17"] * n_left + ["43"] * 40))     # the left windows are read first
    rescued = reader.rescue(image, items)
    texts = [i["text"] for i in rescued]
    assert "17" in texts and "43" in texts
    assert find_score_geo(rescued, find_clocks([i["text"] for i in rescued])) == (17, 43)


def test_a_mixed_case_team_code_still_gets_its_score_slots_read():
    # Man City-Sunderland: the first reader read "McI" (conf 0.56) and "SUN"; the slot sweep must still find the pair
    image = np.zeros((200, 900, 3), dtype=np.uint8)
    items = [item("McI", 60, 48, 128, 80, conf=0.56), item("SUN", 400, 48, 470, 80), item("0", 340, 44, 372, 80, conf=1.0)]
    n_left = len(windows(dict(items[0], text="MCI"), items[1], "left"))
    reader = RapidReader(engine=FakeEngine({}, rec_answers=["1"] * n_left + ["0"] * 40))
    rescued = reader.rescue(image, items)
    assert find_score_geo(rescued, find_clocks([i["text"] for i in rescued])) == (1, 0)


# ---- CPU or GPU --------------------------------------------------------------------------------------------------------------------

def test_the_gpu_is_used_only_when_asked_for_and_directml_is_installed():
    assert rapid_reader.engine_options(True, ["DmlExecutionProvider", "CPUExecutionProvider"])["rec_use_dml"] is True
    assert rapid_reader.engine_options(True, ["CPUExecutionProvider"]) == {}      # not installed: the CPU
    assert rapid_reader.engine_options(False, ["DmlExecutionProvider"]) == {}     # not asked for: the CPU


def test_the_reader_passes_the_gpu_choice_to_the_loader(monkeypatch):
    seen = []
    monkeypatch.setattr(rapid_reader, "_load_engine", lambda gpu=False: seen.append(gpu))
    RapidReader(gpu=True)
    assert seen == [True]
    assert RapidReader(engine=FakeEngine({})).device == "cpu"


def test_a_short_team_code_beside_a_digit_is_a_scorebug_anchor():
    # Man City-Sunderland: the first reader read only "SUN" and one "0" (no clock, no 4-letter name), so the second reader never ran
    assert has_scorebug_anchor([item("SUN", 256, 48, 330, 80, conf=0.9), item("0", 208, 44, 240, 80)])
    assert has_scorebug_anchor([item("LIV", 56, 30, 98, 54), item("0", 114, 28, 138, 54)])


def test_a_code_alone_or_a_digit_far_from_it_is_no_anchor():
    assert not has_scorebug_anchor([item("FOX", 10, 10, 60, 30)])
    assert not has_scorebug_anchor([item("SUN", 256, 48, 330, 80), item("5", 900, 400, 930, 440)])    # another row, far away
    assert not has_scorebug_anchor([item("NFL", 256, 48, 330, 80), item("3", 340, 48, 360, 80, conf=0.3)])   # an unsure digit


def test_a_small_tile_with_nothing_read_still_gets_the_second_reader():
    # a DAZN multiview tile 467 px wide: the first reader read nothing, so there is no anchor, but the scorebug is probably there
    assert not needs_rescue([])
    assert not needs_rescue([item("FOLLOWNFLONDAZN", 405, 479, 600, 499)])
    assert needs_rescue([], small_tile=True)
    assert needs_rescue([item("FOLLOWNFLONDAZN", 405, 479, 600, 499)], small_tile=True)


def test_a_small_tile_whose_score_and_clock_are_read_needs_no_second_reader():
    items = [item("TCU", 100, 40, 140, 60), item("7", 150, 36, 170, 62), item("3", 190, 36, 210, 62), item("BAY", 220, 40, 260, 60), item("3:27", 150, 70, 190, 84)]
    assert not needs_rescue(items, small_tile=True)


def test_the_rescue_reads_a_stacked_scorebug_slot_by_slot():
    # Ligue 1 (DAZN): LOSC above HAC with the clock below, each score right of its own name; the first reader found no digit at all
    from daemon.score_slots import stacked_windows
    image = np.zeros((200, 400, 3), dtype=np.uint8)
    items = [item("LOSC", 103, 34, 137, 49), item("HAC", 103, 56, 132, 72), item("49:56", 57, 78, 97, 94)]
    n_top = len(stacked_windows(items[0]))
    reader = RapidReader(engine=FakeEngine({}, rec_answers=["1"] * n_top + ["0"] * 40))
    rescued = reader.rescue(image, items)
    assert find_score_geo(rescued, find_clocks([i["text"] for i in rescued])) == (1, 0)


def test_the_stacked_rescue_keeps_a_digit_the_full_read_already_found():
    from daemon.score_slots import stacked_windows
    image = np.zeros((200, 400, 3), dtype=np.uint8)
    items = [item("LOSC", 103, 34, 137, 49), item("HAC", 103, 56, 132, 72), item("0", 153, 57, 163, 71, conf=0.95), item("49:56", 57, 78, 97, 94)]
    n_top = len(stacked_windows(items[0]))
    reader = RapidReader(engine=FakeEngine({}, rec_answers=["1"] * n_top + ["7"] * 40))      # a bottom-slot sweep would say 7: it must not run
    rescued = reader.rescue(image, items)
    assert find_score_geo(rescued, find_clocks([i["text"] for i in rescued])) == (1, 0)


# Barcelona-Getafe on a small DAZN tile (real boxes): BAR above GET with the clock left of them, the "0" read, the "1" not. The ad boards
# (read as "TOVELO") look like team names to the side-by-side rule, which used to take the rescue's only turn and read no slot.
BAR_GET_ITEMS = [item("BAR", 95, 37, 121, 51, 0.98), item("TCVELO", 475, 47, 568, 80, 0.85), item("05:11", 54, 65, 89, 78, 0.97),
                 item("GET", 97, 64, 116, 77, 1.0), item("0", 134, 63, 145, 79, 0.89), item("VEL", 4, 67, 53, 93, 0.95),
                 item("TOVELO", 231, 61, 332, 90, 0.83), item("TONELO", 651, 69, 681, 89, 0.91)]


def test_a_stacked_scorebug_is_read_even_when_ad_boards_look_like_a_side_by_side_pair(monkeypatch):
    from daemon.score_slots import stacked_windows
    monkeypatch.setattr(rapid_reader, "read_slots", lambda *args, **kwargs: None)   # the sweep between BAR and the board reads no digits
    image = np.zeros((200, 900, 3), dtype=np.uint8)
    n_top = len(stacked_windows(BAR_GET_ITEMS[0]))
    reader = RapidReader(engine=FakeEngine({}, rec_answers=["1"] * n_top + ["9"] * 80))   # the known "0" is kept, so the 9s must never be used
    rescued = reader.rescue(image, BAR_GET_ITEMS)
    assert find_score_geo(rescued, find_clocks([i["text"] for i in rescued])) == (1, 0)


def test_the_same_scorebug_with_an_unsure_top_slot_still_gives_no_score():
    image = np.zeros((200, 900, 3), dtype=np.uint8)
    reader = RapidReader(engine=FakeEngine({}, rec_answers=[]))   # the recogniser says nothing for any window
    rescued = reader.rescue(image, BAR_GET_ITEMS)
    assert find_score_geo(rescued, find_clocks([i["text"] for i in rescued])) is None
