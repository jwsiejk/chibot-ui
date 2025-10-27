from __future__ import annotations

import unittest

from app import config
from app.telemetry import bus
from app.voice_v2.engine import EngineV2


class ClientDiagEventsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._original_flag = config.DIAG_CLIENT_HUD
        config.DIAG_CLIENT_HUD = True
        bus.reset()
        self._events = []
        self._token = bus.subscribe("*", self._events.append)
        self.engine = EngineV2()

    def tearDown(self) -> None:
        bus.reset()
        config.DIAG_CLIENT_HUD = self._original_flag

    def test_emits_diag_event_when_enabled(self) -> None:
        frame = {
            "type": "client.diag",
            "event": "EVT_CLIENT_RECORDER_STARTED",
            "data": {"foo": "bar"},
            "level": "info",
            "badge": "rec:start",
            "ts": 1_234_567,
            "sample": True,
            "message": "Recorder started",
        }

        self.engine.on_json("sid-diag", frame)

        diag_events = [evt for evt in self._events if evt.get("type") == "EVT_DIAG_HUD"]
        self.assertEqual(len(diag_events), 1)
        diag = diag_events[0]

        self.assertEqual(diag.get("sid"), "sid-diag")
        self.assertEqual(diag.get("who"), "client")
        self.assertEqual(diag.get("source"), "client_hud")
        self.assertEqual(diag.get("level"), "info")

        meta = diag.get("meta") or {}
        self.assertEqual(meta.get("event"), "EVT_CLIENT_RECORDER_STARTED")
        self.assertEqual(meta.get("badge"), "rec:start")
        self.assertTrue(meta.get("sample"))
        self.assertEqual(meta.get("client_ts"), 1_234_567)
        self.assertEqual(meta.get("dir"), "in")
        self.assertIn("data", meta)

    def test_does_not_emit_when_disabled(self) -> None:
        config.DIAG_CLIENT_HUD = False
        frame = {
            "type": "client.diag",
            "event": "EVT_CLIENT_RECORDER_STARTED",
        }

        self.engine.on_json("sid-diag", frame)

        diag_events = [evt for evt in self._events if evt.get("type") == "EVT_DIAG_HUD"]
        self.assertEqual(diag_events, [])


if __name__ == "__main__":
    unittest.main()
