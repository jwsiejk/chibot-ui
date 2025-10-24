from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from typing import List, Sequence, Tuple
from zipfile import ZipFile

from app.admin import flow_api


class AdminFlowApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        self._original_export_root = flow_api.EXPORT_ROOT
        exports_root = Path(self._tmp.name) / "exports"
        flow_api.EXPORT_ROOT = exports_root
        self.addCleanup(self._restore_export_root)

        self.exports_root = exports_root
        self.sid = "sid-test"
        self.session_dir = self.exports_root / self.sid
        self.session_dir.mkdir(parents=True, exist_ok=True)

        (self.session_dir / "manifest.json").write_text(
            json.dumps(
                {
                    "sid": self.sid,
                    "schema_version": "1",
                    "started_ms": 1_732_200_000_000,
                    "open": False,
                    "events_written": 0,
                    "by_type": {},
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )

    def _restore_export_root(self) -> None:
        flow_api.EXPORT_ROOT = self._original_export_root

    def test_trace_accessible_without_authorization(self) -> None:
        events = [
            {"type": "EVT_TURN_BEGIN", "ts_ms": 75, "sid": self.sid, "meta": {}},
        ]
        self._write_events(events)

        scope = self._make_scope(
            path=f"/api/v1/admin/flow/{self.sid}/trace",
            query_string=b"type=EVT_TURN_BEGIN",
            headers=(),
        )
        response = asyncio.run(flow_api.handle_flow_trace(scope, self._receive(), sid=self.sid))

        self.assertEqual(response.status, 200)
        body_lines = [line for line in response.body.decode("utf-8").splitlines() if line]
        self.assertEqual(len(body_lines), 1)
        event = json.loads(body_lines[0])
        self.assertEqual(event.get("type"), "EVT_TURN_BEGIN")

    def test_trace_filters_by_type_and_limit(self) -> None:
        events = [
            {"type": "EVT_TURN_BEGIN", "ts_ms": 75, "sid": self.sid, "meta": {"idx": 1}},
            {"type": "EVT_TURN_END", "ts_ms": 80, "sid": self.sid, "meta": {"idx": 2}},
            {"type": "EVT_TURN_BEGIN", "ts_ms": 120, "sid": self.sid, "meta": {"idx": 3}},
        ]
        self._write_events(events)

        query = b"type=EVT_TURN_BEGIN&since_ms=75&limit=1"
        scope = self._make_scope(
            path=f"/api/v1/admin/flow/{self.sid}/trace",
            query_string=query,
            headers=(),
        )

        response = asyncio.run(flow_api.handle_flow_trace(scope, self._receive(), sid=self.sid))

        self.assertEqual(response.status, 200)
        header_map = dict(response.headers)
        self.assertEqual(header_map[b"content-type"], b"application/x-ndjson")

        body_lines = [line for line in response.body.decode("utf-8").splitlines() if line]
        self.assertEqual(len(body_lines), 1)
        event = json.loads(body_lines[0])
        self.assertEqual(event.get("type"), "EVT_TURN_BEGIN")
        self.assertEqual(event.get("ts_ms"), 75)
        self.assertEqual(event.get("meta", {}).get("idx"), 1)

    def test_zip_returns_archive_with_valid_digests(self) -> None:
        events = [
            {"type": "EVT_TURN_BEGIN", "ts_ms": 100, "sid": self.sid, "meta": {"idx": 1}},
            {"type": "EVT_TURN_END", "ts_ms": 140, "sid": self.sid, "meta": {"idx": 2}},
        ]
        self._write_events(events)

        scope = self._make_scope(
            path=f"/api/v1/admin/flow/{self.sid}/zip",
            query_string=b"",
            headers=(),
        )

        response = asyncio.run(flow_api.handle_flow_zip(scope, self._receive(), sid=self.sid))

        self.assertEqual(response.status, 200)
        header_map = dict(response.headers)
        self.assertEqual(header_map[b"content-type"], b"application/zip")

        with ZipFile(BytesIO(response.body)) as zf:
            manifest_data = json.loads(zf.read("manifest.json").decode("utf-8"))
            sha_map = manifest_data.get("sha256", {})
            for name, digest in sha_map.items():
                payload = zf.read(name)
                self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)

    def _write_events(self, events: List[dict]) -> None:
        events_path = self.session_dir / "events.ndjson"
        with events_path.open("w", encoding="utf-8") as handle:
            for event in events:
                json.dump(event, handle, separators=(",", ":"))
                handle.write("\n")

    def _receive(self):
        messages = [
            {"type": "http.request", "body": b"", "more_body": False},
        ]

        async def _receive() -> dict:
            if messages:
                return messages.pop(0)
            return {"type": "http.disconnect"}

        return _receive

    def _make_scope(
        self,
        *,
        path: str,
        query_string: bytes,
        headers: Sequence[Tuple[bytes, bytes]],
    ) -> dict:
        return {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": query_string,
            "headers": list(headers),
        }


if __name__ == "__main__":
    unittest.main()
