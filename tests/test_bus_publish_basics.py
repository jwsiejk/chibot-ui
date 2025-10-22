import unittest

from app.telemetry.bus import publish, reset, subscribe, unsubscribe


class TestBusPublishBasics(unittest.TestCase):
    def setUp(self) -> None:
        reset()

    def tearDown(self) -> None:
        reset()

    def test_defaults_are_filled(self) -> None:
        delivered = []

        token = subscribe("*", lambda event: delivered.append(event))
        try:
            publish({"type": "EVT_WS_OPEN"})
        finally:
            unsubscribe(token)

        self.assertEqual(len(delivered), 1)
        event = delivered[0]

        self.assertEqual(event["type"], "EVT_WS_OPEN")
        self.assertEqual(event["level"], "debug")
        self.assertIsInstance(event["ts_ms"], int)
        self.assertGreater(event["ts_ms"], 0)

    def test_non_mutation_of_caller(self) -> None:
        event = {"type": "EVT_WS_JSON_RECV", "meta": {"a": "b"}}

        publish(event)

        self.assertEqual(event, {"type": "EVT_WS_JSON_RECV", "meta": {"a": "b"}})

    def test_handler_isolation(self) -> None:
        delivered = []

        def boom_handler(_: dict) -> None:
            raise RuntimeError("boom")

        token1 = subscribe("EVT_WS_OPEN", boom_handler)
        token2 = subscribe("EVT_WS_OPEN", lambda event: delivered.append(event))
        try:
            publish({"type": "EVT_WS_OPEN"})
        finally:
            unsubscribe(token1)
            unsubscribe(token2)

        self.assertEqual(len(delivered), 1)

    def test_schema_version_defaults(self) -> None:
        delivered = []

        token = subscribe("*", lambda event: delivered.append(event))
        try:
            publish({"type": "EVT_ENGINE_EVENT"})
            publish({"type": "EVT_ADAPTER_EVENT", "schema_version": "2"})
        finally:
            unsubscribe(token)

        self.assertEqual(len(delivered), 2)
        self.assertEqual(delivered[0]["schema_version"], "1")
        self.assertEqual(delivered[1]["schema_version"], "2")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
