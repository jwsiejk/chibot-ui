import unittest

from app.voice_v2.gate import GateController


class GateControllerTests(unittest.TestCase):
    def test_set_clear_reasons_and_effective(self) -> None:
        published: list[dict] = []

        controller = GateController(publish=published.append)

        controller.set_reason("tts_active", True)
        self.assertEqual(len(published), 1)
        event_on = published[-1]
        gate_meta = event_on["meta"]["gate"]
        self.assertEqual(gate_meta["state"], "on")
        self.assertTrue(gate_meta["mask"])

        controller.set_reason("tts_active", False)
        self.assertEqual(len(published), 2)
        event_off = published[-1]
        gate_meta_off = event_off["meta"]["gate"]
        self.assertEqual(gate_meta_off["state"], "off")
        self.assertFalse(gate_meta_off["mask"])

        snapshot = controller.snapshot()
        self.assertFalse(snapshot["effective"])
        self.assertFalse(snapshot["reasons"]["tts_active"])

    def test_idempotent_no_duplicate_publish(self) -> None:
        published: list[dict] = []
        controller = GateController(publish=published.append)

        controller.set_reason("manual_gate", True)
        controller.set_reason("manual_gate", True)

        self.assertEqual(len(published), 1)

    def test_multi_reason_marks_multi(self) -> None:
        published: list[dict] = []
        controller = GateController(publish=published.append)

        controller.set_reason("manual_gate", True)
        controller.set_reason("system_hold", True)

        self.assertEqual(len(published), 2)
        event = published[-1]
        gate_meta = event["meta"]["gate"]
        self.assertEqual(gate_meta["reason"], "multi")
        self.assertTrue(gate_meta["mask"])


if __name__ == "__main__":
    unittest.main()

