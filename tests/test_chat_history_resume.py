"""Tests covering chat.history emission on connect and resume."""
from __future__ import annotations

import copy
import unittest

from app.telemetry import bus
from app.voice_v2 import EVT_CHAT_USER, EVT_WS_JSON_SEND
from app.voice_v2.engine import EngineV2


class ChatHistoryResumeTests(unittest.TestCase):
    """Validate conversation buffering and history emission semantics."""

    def setUp(self) -> None:
        bus.reset()
        self.history_frames: list[dict] = []
        self.messages: list[dict] = []

        def _capture(event: dict) -> None:
            frame = event.get("frame")
            if not isinstance(frame, dict):
                payload = event.get("payload")
                if isinstance(payload, dict):
                    frame = payload.get("frame")
            if not isinstance(frame, dict):
                return

            frame_type = frame.get("type")
            if frame_type == "chat.history":
                self.history_frames.append(copy.deepcopy(frame))
            elif frame_type == "chat.message":
                self.messages.append(copy.deepcopy(frame))

        self._token = bus.subscribe(EVT_WS_JSON_SEND, _capture)

    def tearDown(self) -> None:
        bus.reset()

    def test_history_buffer_on_connect_and_resume(self) -> None:
        engine = EngineV2()
        sid = "sid-history"

        engine.on_open(sid, {})
        self.assertEqual(len(self.history_frames), 1)
        self.assertEqual(self.history_frames[-1]["messages"], [])

        self.history_frames.clear()

        total_turns = 105
        for idx in range(total_turns):
            if idx % 2 == 0:
                bus.publish({"type": EVT_CHAT_USER, "sid": sid, "text": f"typed-{idx}"})
            else:
                engine.on_asr_final(sid, f"voice-{idx}")

            last_user = self.messages[-1]
            assistant_frame = {
                "type": "chat.message",
                "id": f"a-{idx}",
                "role": "assistant",
                "text": f"assistant-{idx}",
                "origin": "voice",
                "turn_id": last_user.get("turn_id"),
                "req_id": last_user.get("req_id"),
                "ts_ms": last_user.get("ts_ms", 0) + 1,
            }
            bus.publish({"type": EVT_WS_JSON_SEND, "sid": sid, "payload": {"frame": assistant_frame}})

        expected_messages = copy.deepcopy(self.messages)

        engine.on_close(sid, 1000, "disconnect")
        self.history_frames.clear()

        engine.on_open(sid, {})
        self.assertEqual(len(self.history_frames), 1)
        resume_history = self.history_frames[-1]
        history_messages = copy.deepcopy(resume_history["messages"])
        self.assertEqual(len(history_messages), 100)
        self.assertEqual(_sanitize(history_messages), _sanitize(expected_messages[-100:]))

        self.history_frames.clear()
        engine.on_json(sid, {"type": "client.resume", "resume_token": "token-1"})
        self.assertEqual(len(self.history_frames), 1)
        resume_messages = self.history_frames[-1]["messages"]
        self.assertEqual(len(resume_messages), 100)
        self.assertTrue(_texts(history_messages).issubset(_texts(resume_messages)))


def _sanitize(messages: list[dict]) -> list[dict]:
    sanitized: list[dict] = []
    for item in messages:
        if not isinstance(item, dict):
            continue
        sanitized.append(
            {key: item[key] for key in item if key not in {"id", "req_id", "turn_id"}}
        )
    return sanitized


def _texts(messages: list[dict]) -> set[str]:
    values: set[str] = set()
    for item in messages:
        if isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                values.add(text)
    return values


if __name__ == "__main__":  # pragma: no cover - convenience
    unittest.main()
