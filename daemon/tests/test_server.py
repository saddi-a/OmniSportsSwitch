import asyncio
import json
import struct
import time

import numpy as np
import pytest
from websockets.asyncio.client import connect

from daemon.scoring import Config, Scorer
from daemon.server import Server

PORT = 8799


@pytest.mark.asyncio
async def test_crowd_roar_triggers_swap_message():
    config = Config(min_dwell_s=0, warmup_s=0, window_s=1.0)
    server = Server(Scorer(config))
    task = asyncio.create_task(server.run(port=PORT))
    await asyncio.sleep(0.2)
    try:
        async with connect(f"ws://127.0.0.1:{PORT}") as ws:
            await ws.send(json.dumps({"type": "register", "tab_id": 1, "is_main": True}))
            await ws.send(json.dumps({"type": "register", "tab_id": 2}))
            for _ in range(5):  # normal level for both
                await ws.send(json.dumps({"type": "audio", "tab_id": 1, "rms": 0.05}))
                await ws.send(json.dumps({"type": "audio", "tab_id": 2, "rms": 0.05}))
                await asyncio.sleep(0.05)
            for _ in range(8):  # tab 2 roars
                await ws.send(json.dumps({"type": "audio", "tab_id": 1, "rms": 0.05}))
                await ws.send(json.dumps({"type": "audio", "tab_id": 2, "rms": 0.20}))
                server.scorer.set_tags(2, 0.9, 0.1, 0.0, time.monotonic())
                await asyncio.sleep(0.1)
            reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert reply == {"type": "swap", "promote": 2, "demote": 1}
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_binary_audio_frame_feeds_the_scorer():
    server = Server()
    task = asyncio.create_task(server.run(port=PORT + 2))
    await asyncio.sleep(0.2)
    try:
        async with connect(f"ws://127.0.0.1:{PORT + 2}") as ws:
            await ws.send(json.dumps({"type": "register", "tab_id": 7}))
            pcm = (np.full(3200, 0.5) * 32767).astype("<i2").tobytes()  # constant level 0.5
            await ws.send(struct.pack("<I", 7) + pcm)
            await asyncio.sleep(0.2)
            level = server.scorer.streams[7].samples[-1][1]
            assert level == pytest.approx(0.5, abs=0.01)
    finally:
        task.cancel()


@pytest.mark.asyncio
async def test_bad_message_does_not_kill_connection():
    server = Server()
    task = asyncio.create_task(server.run(port=PORT + 1))
    await asyncio.sleep(0.2)
    try:
        async with connect(f"ws://127.0.0.1:{PORT + 1}") as ws:
            await ws.send("not json")
            await ws.send(json.dumps({"type": "audio"}))
            await ws.send(json.dumps({"type": "register", "tab_id": 1}))
            await asyncio.sleep(0.2)
            assert 1 in server.scorer.streams
    finally:
        task.cancel()


def test_video_rect_message_is_stored_and_cleared_on_unregister():
    server = Server()
    rect = {"left": 10, "top": 20, "width": 1280, "height": 720}
    server.handle_message({"type": "register", "tab_id": 5})
    server.handle_message({"type": "video_rect", "tab_id": 5, "rects": [rect], "visible": True, "fullscreen": False})
    assert server.rects[5]["rects"] == [rect] and server.rects[5]["visible"] is True
    server.handle_message({"type": "unregister", "tab_id": 5})
    assert 5 not in server.rects
