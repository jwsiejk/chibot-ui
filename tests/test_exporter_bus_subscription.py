import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.telemetry import bus
from app.telemetry.exporter import FileExporter


class ExporterBusSubscriptionTests(unittest.TestCase):
    def test_exporter_subscribes_and_tracks_manifest(self) -> None:
        with TemporaryDirectory() as tmpdir:
            exporter = FileExporter(root=tmpdir, bus=bus)
            sid = "sid1"

            exporter.begin(sid)

            session_dir = Path(tmpdir) / sid
            events_path = session_dir / "events.ndjson"
            manifest_path = session_dir / "manifest.json"

            self.assertTrue(events_path.exists())
            self.assertTrue(manifest_path.exists())

            manifest_open = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest_open["open"])
            self.assertEqual(0, manifest_open["events_written"])

            secret_event = {
                "type": "voice.secret",
                "sid": sid,
                "meta": {
                    "authorization": "Bearer SECRET-TOKEN-123456",
                    "contact": "person@example.com",
                },
            }
            info_event = {
                "type": "voice.info",
                "sid": sid,
                "meta": {"note": "ok"},
            }

            bus.publish(secret_event)
            bus.publish(info_event)

            lines = [
                json.loads(line)
                for line in events_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(2, len(lines))

            first_meta = lines[0].get("meta", {})
            self.assertEqual("Bearer ****3456", first_meta.get("authorization"))
            self.assertEqual("***@example.com", first_meta.get("contact"))

            manifest_mid = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertTrue(manifest_mid["open"])
            self.assertEqual(2, manifest_mid["events_written"])
            self.assertEqual({"voice.secret": 1, "voice.info": 1}, manifest_mid["by_type"])

            exporter.end(sid)

            manifest_final = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertFalse(manifest_final["open"])
            self.assertEqual(2, manifest_final["events_written"])
            self.assertEqual({"voice.secret": 1, "voice.info": 1}, manifest_final["by_type"])
            self.assertIn("ended_ms", manifest_final)
            self.assertGreaterEqual(manifest_final["ended_ms"], manifest_final["started_ms"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
