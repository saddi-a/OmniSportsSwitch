"""Where a video sits on the screen, from the raw numbers the page reports.

The video reporter in `extension/background.js` computes the screen position as `(screenX + border + left) * devicePixelRatio`. That is right
at 100 % page zoom only: `devicePixelRatio` includes the zoom, `innerWidth` and the tile positions are in zoomed (CSS) pixels, `screenX` and
`outerWidth` are not. Zoomed out to 50 % the position came out about 1100 px off (found live, a DAZN multiview window), so the daemon read
other windows' pixels. The extension therefore also sends the raw numbers, and `fix_rects` recomputes `left` and `top` when the page is zoomed.
"""
import math

ZOOMS = (0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0)   # Edge's page zoom presets
ZOOM_TOLERANCE = 0.04   # outer / inner width must be this close (relative) to a preset, else the window only looks odd (DevTools docked): no correction


def page_zoom(win: dict) -> float:
    """The page zoom factor: the browser's own number (`chrome.tabs.getZoom`, sent as `zoom`) when the extension has it; else estimated from
    the outer and inner window width, 1.0 when that does not match a zoom preset."""
    zoom = win.get("zoom")
    if isinstance(zoom, (int, float)) and not isinstance(zoom, bool) and 0.2 <= zoom <= 6:
        return float(zoom)
    try:
        ratio = float(win["ow"]) / float(win["iw"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 1.0
    if ratio <= 0:
        return 1.0
    best = min(ZOOMS, key=lambda z: abs(math.log(ratio / z)))
    return best if abs(math.log(ratio / best)) <= ZOOM_TOLERANCE else 1.0


def fix_rects(rects: list[dict], win: dict | None) -> list[dict]:
    """The reported rects with `left` and `top` recomputed for a zoomed page. Unchanged without raw numbers or at 100 % zoom."""
    if not win or not rects:
        return rects
    zoom = page_zoom(win)
    if abs(zoom - 1.0) < 1e-9:
        return rects
    try:
        scale = float(win["dpr"]) / zoom                           # physical pixels per device-independent pixel
        frame = (float(win["ow"]) - float(win["iw"]) * zoom) / 2   # window frame on each side, in DIPs
        toolbar = float(win["oh"]) - float(win["ih"]) * zoom - frame   # tab strip and address bar
        fixed = []
        for r in rects:
            css = r.get("css")
            if not css:
                fixed.append(r)
                continue
            fixed.append(dict(r, left=round((float(win["x"]) + frame + css["left"] * zoom) * scale),
                              top=round((float(win["y"]) + toolbar + css["top"] * zoom) * scale)))
        return fixed
    except (KeyError, TypeError, ValueError):
        return rects
