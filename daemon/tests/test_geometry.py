"""The video reporter's screen position is wrong when the page is zoomed (found live 2026-10-18: a DAZN window zoomed out was reported
about 1100 px off, so the daemon read other windows' pixels). `fix_rects` recomputes it from the raw numbers."""
from daemon.geometry import fix_rects, page_zoom

# a window at screen x 2560 / y 100 (DIP), 1440 x 1000 outside, 8 px frame, 100 px toolbar, on a 100 % screen
WIN_100 = {"x": 2560, "y": 100, "ow": 1440, "oh": 1000, "iw": 1424, "ih": 892, "dpr": 1.0}
# the same window with the page zoomed to 50 %: innerWidth/innerHeight and the CSS positions double, dpr halves
WIN_50 = {"x": 2560, "y": 100, "ow": 1440, "oh": 1000, "iw": 2848, "ih": 1784, "dpr": 0.5}


def rect(left, top, width, height, css_left, css_top, index=0):
    return {"index": index, "audible": False, "left": left, "top": top, "width": width, "height": height, "css": {"left": css_left, "top": css_top}}


def test_the_page_zoom_is_read_from_the_outer_and_inner_width():
    assert page_zoom(WIN_100) == 1.0
    assert page_zoom(WIN_50) == 0.5
    assert page_zoom(dict(WIN_100, iw=1424 / 1.25)) == 1.25       # zoomed in to 125 %


def test_without_a_zoom_the_reported_rects_are_kept_as_they_are():
    rects = [rect(2578, 208, 953, 660, 10, 108)]
    assert fix_rects(rects, WIN_100) == rects
    assert fix_rects(rects, None) == rects                        # an old extension sends no raw numbers
    assert fix_rects([{"index": 0, "left": 1, "top": 2, "width": 3, "height": 4}], WIN_50)[0]["left"] == 1   # no css numbers: nothing to recompute


def test_a_zoomed_out_page_gets_its_true_screen_position():
    # a tile at css (100, 216) = DIP (50, 108): screen x = 2560 + 8 (frame) + 50, screen y = 100 + 100 (toolbar) + 108
    wrong = rect(978, 154, 476, 330, 100, 216)                    # what the old formula reported
    fixed = fix_rects([wrong], WIN_50)[0]
    assert (fixed["left"], fixed["top"]) == (2618, 308)
    assert (fixed["width"], fixed["height"]) == (476, 330)        # the size was always right
    assert fixed["index"] == 0 and fixed["audible"] is False


def test_a_window_that_only_looks_odd_is_not_corrected():
    # DevTools docked or a narrow side panel: outer/inner is not a browser zoom preset
    assert page_zoom(dict(WIN_100, iw=1424 / 1.6)) == 1.0


def test_the_browsers_own_zoom_number_wins_over_the_estimate():
    assert page_zoom(dict(WIN_50, zoom=0.5)) == 0.5
    assert page_zoom(dict(WIN_100, zoom=0.57)) == 0.57            # a zoom that is no preset: no table needed
    assert page_zoom(dict(WIN_50, zoom=None)) == 0.5              # unknown: the estimate
    assert page_zoom(dict(WIN_50, zoom="x")) == 0.5 and page_zoom(dict(WIN_50, zoom=0)) == 0.5


def test_a_zoom_without_a_preset_still_gives_the_true_position():
    # 57 % zoom: DIP numbers are the same window, CSS numbers scale by 1 / 0.57
    zoom = 0.57
    win = {"x": 2560, "y": 100, "ow": 1440, "oh": 1000, "iw": 1424 / zoom, "ih": 892 / zoom, "dpr": zoom, "zoom": zoom}
    fixed = fix_rects([rect(0, 0, 476, 330, 100, 216)], win)[0]
    assert (fixed["left"], fixed["top"]) == (round(2560 + 8 + 100 * zoom), round(100 + 100 + 216 * zoom))
