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
async def test_louder_stream_triggers_swap_message():
    server = Server(Scorer(Config(min_dwell_s=0, window_s=1.0)))
    task = asyncio.create_task(server.run(port=PORT))
    await asyncio.sleep(0.2)
    try:
        async with connect(f"ws://127.0.0.1:{PORT}") as ws:
            await ws.send(json.dumps({"type": "register", "tab_id": 1, "is_main": True}))
            await ws.send(json.dumps({"type": "register", "tab_id": 2}))
            for _ in range(8):
                await ws.send(json.dumps({"type": "audio", "tab_id": 1, "rms": 0.05}))
                await ws.send(json.dumps({"type": "audio", "tab_id": 2, "rms": 0.20}))
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
            score = server.scorer.score(7, time.monotonic())
            assert score == pytest.approx(0.5, abs=0.01)
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
