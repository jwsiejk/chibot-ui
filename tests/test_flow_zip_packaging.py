from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from app.admin.flow_zip import build_flow_zip


_README = "README.txt"
_EVENTS_REDACTED = "events.redacted.ndjson"
_TIMELINE = "flow_timeline.ndjson"
_MANIFEST = "manifest.json"
_NLG = "nlg.ndjson"
_NLU = "nlu.ndjson"
_LOGS = "logs.ndjson"

class BuildFlowZipPackagingTest(unittest.TestCase):
    def test_redaction_and_sha_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "exports"
            sid = "sid-redaction"
            session_dir = self._prepare_session(root, sid)

            events = [
                {
                    "type": "EVT_WS_JSON_RECV",
                    "sid": sid,
                    "ts_ms": 111,
                    "meta": {
                        "authorization": "Bearer supersecretvalue",
                        "email": "agent@example.com",
                    },
                },
                {
                    "type": "EVT_DIAG_HUD",
                    "sid": sid,
                    "ts_ms": 444,
                    "level": "info",
                    "meta": {"event": "recorder_started", "badge": "rec:start"},
                },
                {
                    "type": "EVT_NLU",
                    "sid": sid,
                    "ts_ms": 222,
                    "meta": {"intent": "help_install"},
                },
                {
                    "type": "EVT_NLG",
                    "sid": sid,
                    "ts_ms": 333,
                    "meta": {"utterance": "Masked"},
                },
            ]
            self._write_events(session_dir, events)

            logs = [
                {
                    "sid": sid,
                    "ts": 111.1,
                    "level": "INFO",
                    "logger": "test",
                    "msg": "first",
                },
                {
                    "sid": sid,
                    "ts": 222.2,
                    "level": "ERROR",
                    "logger": "test",
                    "msg": "second",
                },
            ]
            self._write_logs(session_dir, logs)

            archive_path = build_flow_zip(sid, root=root)
            self.assertTrue(archive_path.exists())

            with ZipFile(archive_path) as zf:
                names = zf.namelist()
                self.assertEqual(
                    names,
                    [_README, _EVENTS_REDACTED, _TIMELINE, _LOGS, _MANIFEST, _NLG, _NLU],
                )

                events_data = zf.read(_EVENTS_REDACTED).decode("utf-8").strip().splitlines()
                self.assertEqual(len(events_data), 4)
                first_event = json.loads(events_data[0])
                self.assertNotIn("supersecretvalue", json.dumps(first_event))
                self.assertIn("***@example.com", json.dumps(first_event))
                auth_value = first_event["meta"]["authorization"]
                self.assertTrue(auth_value.startswith("Bearer "))
                self.assertIn("****", auth_value)

                timeline_data = [
                    json.loads(line)
                    for line in zf.read(_TIMELINE).decode("utf-8").strip().splitlines()
                    if line
                ]
                self.assertTrue(
                    any(evt.get("type") == "EVT_DIAG_HUD" for evt in timeline_data),
                    "timeline should include DIAG events",
                )

                logs_data = [json.loads(line) for line in zf.read(_LOGS).decode("utf-8").strip().splitlines() if line]
                self.assertEqual(len(logs_data), len(logs))
                self.assertEqual(logs_data[0]["msg"], "first")

                manifest = json.loads(zf.read(_MANIFEST).decode("utf-8"))
                sha_map = manifest.get("sha256", {})
                for name in [_README, _EVENTS_REDACTED, _TIMELINE, _LOGS, _NLG, _NLU]:
                    digest = self._sha256_bytes(zf.read(name))
                    self.assertEqual(sha_map[name], digest)

                manifest_files = manifest.get("files", [])
                self.assertTrue(
                    any(entry.get("name") == _LOGS and entry.get("size") == len(zf.read(_LOGS)) for entry in manifest_files)
                )

                self.assertNotIn("events.ndjson", names)
                self.assertFalse(manifest.get("truncated"))

    def test_truncation_applies_cap_and_records_drops(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "exports"
            sid = "sid-truncation"
            session_dir = self._prepare_session(root, sid)

            cap_override = 1_500
            vendor_events = [self._make_event("EVT_VENDOR_DEBUG", sid, self._blob(8_000)) for _ in range(2)]
            partial_events = [self._make_event("EVT_ASR_PARTIAL", sid, self._blob(9_000)) for _ in range(2)]
            main_events = [self._make_event("EVT_WS_JSON_SEND", sid, self._blob(10_000)) for _ in range(3)]
            events = vendor_events + partial_events + main_events
            self._write_events(session_dir, events)

            archive_path = build_flow_zip(sid, root=root, cap_bytes=cap_override)
            self.assertLessEqual(archive_path.stat().st_size, cap_override)

            with ZipFile(archive_path) as zf:
                manifest = json.loads(zf.read(_MANIFEST).decode("utf-8"))
                self.assertTrue(manifest.get("truncated"))
                self.assertEqual(manifest.get("cap_bytes"), cap_override)
                dropped = manifest.get("dropped", {})
                self.assertGreaterEqual(dropped.get("vendor_debug", 0), 1)
                self.assertGreaterEqual(dropped.get("partials", 0), 1)
                self.assertGreater(dropped.get("events_tail", 0), 0)

                redacted_lines = [json.loads(line) for line in zf.read(_EVENTS_REDACTED).decode("utf-8").strip().splitlines() if line]
                vendor_remaining = sum(1 for evt in redacted_lines if evt.get("type") == "EVT_VENDOR_DEBUG")
                partial_remaining = sum(1 for evt in redacted_lines if evt.get("type") == "EVT_ASR_PARTIAL")
                self.assertEqual(
                    vendor_remaining,
                    len(vendor_events) - dropped.get("vendor_debug", 0),
                )
                self.assertEqual(
                    partial_remaining,
                    len(partial_events) - dropped.get("partials", 0),
                )

    def _prepare_session(self, root: Path, sid: str) -> Path:
        session_dir = root / sid
        session_dir.mkdir(parents=True, exist_ok=True)
        manifest = {
            "sid": sid,
            "schema_version": "1",
            "started_ms": 1_732_200_000_000,
            "open": False,
            "events_written": 0,
            "by_type": {},
        }
        (session_dir / "manifest.json").write_text(
            json.dumps(manifest, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
        return session_dir

    def _write_events(self, session_dir: Path, events: list[dict]) -> None:
        events_path = session_dir / "events.ndjson"
        with events_path.open("w", encoding="utf-8") as handle:
            for event in events:
                json.dump(event, handle, separators=(",", ":"), ensure_ascii=False)
                handle.write("\n")

    def _write_logs(self, session_dir: Path, logs: list[dict]) -> None:
        logs_path = session_dir / "logs.ndjson"
        with logs_path.open("w", encoding="utf-8") as handle:
            for entry in logs:
                json.dump(entry, handle, separators=(",", ":"), ensure_ascii=False)
                handle.write("\n")

    def _sha256_bytes(self, payload: bytes) -> str:
        import hashlib

        return hashlib.sha256(payload).hexdigest()

    def _blob(self, size: int) -> str:
        return base64.b64encode(os.urandom(size)).decode("ascii")

    def _make_event(self, event_type: str, sid: str, blob: str) -> dict:
        return {
            "type": event_type,
            "sid": sid,
            "ts_ms": 1000,
            "meta": {"hint": "ok"},
            "payload": {"blob": blob},
        }


if __name__ == "__main__":
    unittest.main()
