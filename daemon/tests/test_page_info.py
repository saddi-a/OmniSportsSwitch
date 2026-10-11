"""What the page says about the game (title, media session, player header) reaches the daemon with the video-area message."""
from daemon.server import Server

HEADER = {"title": "Sport | CANAL+", "media": None,
          "texts": ["LIVERPOOL / MANCHESTER CITY", "Premier League - 6e journée", "Hello"]}


def rect_message(tab_id, info):
    msg = {"type": "video_rect", "tab_id": tab_id, "rects": [{"left": 0, "top": 0, "width": 1280, "height": 720}],
           "visible": True, "fullscreen": False}
    if info is not None:
        msg["info"] = info
    return msg


def test_page_text_is_kept_and_feeds_the_team_mapping_when_the_popup_has_no_teams():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 5})
    server.handle_message(rect_message(5, HEADER))
    assert "LIVERPOOL / MANCHESTER CITY" in server.page_text[5]
    assert server.feed_map.text[5] == server.page_text[5]


def test_the_popup_teams_win_over_the_page_text():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 5, "teams": "Liverpool vs Man City"})
    server.handle_message(rect_message(5, HEADER))
    assert server.feed_map.text[5] == "Liverpool vs Man City"
    server.handle_message({"type": "register", "tab_id": 5, "teams": ""})   # the popup text is cleared: the page text is used again
    assert server.feed_map.text[5] == server.page_text[5]


def test_media_session_data_is_used_and_strings_are_cut_short():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 6})
    server.handle_message(rect_message(6, {"title": "x", "media": {"title": "Jaguars at Eagles", "artist": "NFL", "album": None}, "texts": ["a" * 500]}))
    text = server.page_text[6]
    assert text.startswith("Jaguars at Eagles | NFL")
    assert max(len(part) for part in text.split(" | ")) <= 120


def test_a_message_without_page_info_or_with_junk_changes_nothing():
    server = Server()
    server.handle_message({"type": "register", "tab_id": 7})
    server.handle_message(rect_message(7, None))
    server.handle_message(rect_message(7, {"title": 5, "media": "no", "texts": [1, None, "ok text"]}))
    assert server.page_text.get(7, "") in ("", "ok text")
    server.handle_message({"type": "unregister", "tab_id": 7})
    assert 7 not in server.page_text
