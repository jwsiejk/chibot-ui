import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import asgi_gateway
from app.admin import flow_api


def _make_receive():
    messages = [
        {"type": "http.request", "body": b"", "more_body": False},
    ]

    async def _receive() -> dict:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    return _receive


class AdminConfigRouteTest(unittest.TestCase):
    def test_get_returns_client_snapshot(self) -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/config",
            "query_string": b"",
            "headers": [],
        }

        snapshot = {"DIAG_CLIENT_HUD": True, "DIAG_CHUNK_SAMPLE_N": 5}
        with patch(
            "app.config.get_client_config_snapshot",
            return_value=snapshot,
        ):
            response = asyncio.run(asgi_gateway._handle_admin_config(scope, _make_receive()))

        self.assertEqual(response.status, 200)
        body = json.loads(response.body.decode("utf-8"))
        self.assertEqual(body, snapshot)

    def test_head_strips_body(self) -> None:
        scope = {
            "type": "http",
            "method": "HEAD",
            "path": "/api/v1/admin/config",
            "query_string": b"",
            "headers": [],
        }

        with patch("app.config.get_client_config_snapshot", return_value={}):
            response = asyncio.run(asgi_gateway._handle_admin_config(scope, _make_receive()))

        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, b"")

    def test_rejects_unsupported_method(self) -> None:
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/admin/config",
            "query_string": b"",
            "headers": [],
        }

        response = asyncio.run(asgi_gateway._handle_admin_config(scope, _make_receive()))
        self.assertEqual(response.status, 405)


class AdminFlowLiveRouteTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

        self.export_root = Path(self._tmp.name) / "exports"
        self.export_root.mkdir(parents=True, exist_ok=True)

        self.sid = "sid-live"
        session_dir = self.export_root / self.sid
        session_dir.mkdir(parents=True, exist_ok=True)

        events_path = session_dir / "events.ndjson"
        events_path.write_text(
            json.dumps({"type": "EVT_TURN_BEGIN", "ts_ms": 1, "sid": self.sid}) + "\n",
            encoding="utf-8",
        )

        self._orig_flow_export_root = flow_api.EXPORT_ROOT
        self._orig_gateway_export_root = asgi_gateway.EXPORT_ROOT
        flow_api.EXPORT_ROOT = self.export_root
        asgi_gateway.EXPORT_ROOT = self.export_root
        self.addCleanup(self._restore_export_roots)

    def _restore_export_roots(self) -> None:
        flow_api.EXPORT_ROOT = self._orig_flow_export_root
        asgi_gateway.EXPORT_ROOT = self._orig_gateway_export_root

    def test_streams_existing_events(self) -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/flow/live",
            "query_string": f"sid={self.sid}".encode("ascii"),
            "headers": [],
        }

        with patch("app.asgi_gateway._require_admin_api", new=AsyncMock(return_value=None)):
            response = asyncio.run(
                asgi_gateway._handle_admin_flow_live_query(scope, _make_receive())
            )

        self.assertEqual(response.status, 200)
        headers = dict(response.headers)
        self.assertEqual(headers[b"content-type"], b"text/event-stream; charset=utf-8")
        self.assertIn(b"data: {\"type\": \"EVT_TURN_BEGIN\"", response.body)

    def test_missing_sid_returns_400(self) -> None:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/flow/live",
            "query_string": b"",
            "headers": [],
        }

        with patch("app.asgi_gateway._require_admin_api", new=AsyncMock(return_value=None)):
            response = asyncio.run(
                asgi_gateway._handle_admin_flow_live_query(scope, _make_receive())
            )

        self.assertEqual(response.status, 400)

