import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.telemetry import bus
from app.telemetry.exporter import FileExporter
from app.voice_v2.engine import EngineV2


class PerformanceSummaryTests(unittest.TestCase):
    def test_summary_event_and_manifest_persistence(self) -> None:
        captured = []
        token = bus.subscribe("*", lambda event: captured.append(event))
        try:
            with TemporaryDirectory() as tmpdir:
                exporter = FileExporter(root=tmpdir, bus=bus)
                engine = EngineV2(exporter=exporter, telemetry_bus=bus)
                sid = "sid-perf"

                exporter.begin(sid)
                engine.on_open(sid, headers={})

                engine.on_audio(sid, b"\x00\x00", 0)
                context = engine.turn_context(sid)
                self.assertIsNotNone(context)
                assert context is not None  # for type checkers
                req_id = context["req_id"]

                engine.on_asr_partial(sid, req_id, 0.9, "he")
                engine.on_asr_final(sid, "hello there")
                engine.on_tts_start(sid, "utt-1")
                engine.on_tts_end(sid, "utt-1")

                summary_events = [
                    event
                    for event in captured
                    if event.get("type") == "EVT_PERF_SUMMARY" and event.get("sid") == sid
                ]
                self.assertEqual(1, len(summary_events))
                summary_event = summary_events[0]

                expected_fields = {
                    "turn_id",
                    "req_id",
                    "t_first_partial_ms",
                    "t_final_ms",
                    "t_tts_start_ms",
                }
                self.assertTrue(expected_fields.issubset(summary_event.keys()))
                for key in ["t_first_partial_ms", "t_final_ms", "t_tts_start_ms"]:
                    value = summary_event[key]
                    self.assertIsInstance(value, int)
                    self.assertGreaterEqual(value, 0)

                engine.on_close(sid, 1000, "done")
                exporter.end(sid, summary=summary_event)

                manifest_path = Path(tmpdir) / sid / "manifest.json"
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertIn("summary", manifest)
                self.assertEqual(summary_event, manifest["summary"])
        finally:
            bus.unsubscribe(token)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
