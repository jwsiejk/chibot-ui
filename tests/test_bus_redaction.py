import unittest

from app.telemetry.bus import publish, reset, subscribe, unsubscribe


class TestBusRedaction(unittest.TestCase):
    def setUp(self) -> None:
        reset()

    def tearDown(self) -> None:
        reset()

    def _capture_event(self, meta: dict | None = None, **extra: object) -> dict:
        delivered: list[dict] = []

        token = subscribe("EVT_WS_JSON_RECV", lambda event: delivered.append(event))
        try:
            payload: dict = {"type": "EVT_WS_JSON_RECV"}
            if meta is not None:
                payload["meta"] = meta
            if extra:
                payload.update(extra)
            publish(payload)
        finally:
            unsubscribe(token)

        self.assertEqual(len(delivered), 1)
        return delivered[0]

    def test_email_masking(self) -> None:
        event = self._capture_event({"user": "user@example.com"})
        self.assertEqual(event["meta"], {"user": "***@example.com"})

    def test_bearer_token_masking(self) -> None:
        event = self._capture_event({"auth": "Bearer abc123def456"})
        self.assertEqual(event["meta"], {"auth": "Bearer ****f456"})

    def test_secret_opaque_masking(self) -> None:
        secret_value = "abc" + ("d" * 42) + "xyz"
        event = self._capture_event({"secret": secret_value})
        self.assertEqual(event["meta"], {"secret": "abc…xyz"})

    def test_url_query_param_masking(self) -> None:
        event = self._capture_event({"callback": "https://x.y/cb?token=abcdef&ok=1"})
        self.assertEqual(
            event["meta"],
            {"callback": "https://x.y/cb?token=%2A%2A%2A%2A&ok=1"},
        )

    def test_generic_long_string_collapse(self) -> None:
        event = self._capture_event({"blob": "A" * 140})
        self.assertEqual(event["meta"], {"blob": "AAAAAAAA…AAAAAAAA"})

    def test_nested_container_redaction(self) -> None:
        event = self._capture_event(
            {
                "outer": [
                    {"email": "user@example.com"},
                    {"auth": "Bearer zzz999888777"},
                ]
            }
        )
        self.assertEqual(
            event["meta"],
            {
                "outer": [
                    {"email": "***@example.com"},
                    {"auth": "Bearer ****8777"},
                ]
            },
        )

    def test_schema_top_level_untouched(self) -> None:
        event = self._capture_event({"user": "user@example.com"}, sid="SESSION-123")
        self.assertEqual(event["type"], "EVT_WS_JSON_RECV")
        self.assertEqual(event["sid"], "SESSION-123")
        self.assertEqual(event["level"], "debug")
        self.assertIsInstance(event["ts_ms"], int)

    def test_redaction_is_deterministic(self) -> None:
        meta_payload = {"auth": "Bearer tokenXYZ123456"}
        first = self._capture_event(dict(meta_payload))
        second = self._capture_event(dict(meta_payload))
        self.assertEqual(first["meta"], second["meta"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
