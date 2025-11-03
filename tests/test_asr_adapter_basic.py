import asyncio
import json
import unittest

from app.telemetry import bus
from app.ws.adapter import AdapterContext, ChatV2Adapter


class TestASRAdapterBasic(unittest.TestCase):
    def setUp(self) -> None:
        bus.reset()

    def tearDown(self) -> None:
        bus.reset()

    def test_ready_bundle_uses_pcm_policy(self) -> None:
        adapter = ChatV2Adapter()
        ctx = AdapterContext(sid="sid-ready-bundle", headers={})
        ctx.asr_vendor = "speechmatics"
        ctx.audio_pipeline_mode = "pcm16"
        ctx.session_capture_policy = adapter._session_capture_policy_for_mode("pcm16")

        sent = asyncio.run(self._emit_ready_bundle(adapter, ctx))

        self.assertFalse(ctx.asr_ready)
        self.assertTrue(ctx.awaiting_asr_ready)
        self.assertTrue(ctx.client_capture_armed)
        self.assertIsInstance(ctx.mic_armed_ms, int)

        frames = [
            json.loads(msg["text"])
            for msg in sent
            if msg.get("type") == "websocket.send" and msg.get("text")
        ]

        self.assertEqual(
            [frame.get("type") for frame in frames],
            ["asr.ready", "input.start", "start_listening"],
        )

        ready_frame = frames[0]
        self.assertEqual(ready_frame.get("vendor"), "speechmatics")
        input_desc = ready_frame.get("input")
        self.assertIsInstance(input_desc, dict)
        self.assertEqual(input_desc.get("mode"), "pcm16")
        self.assertEqual(input_desc.get("container"), "raw")
        self.assertEqual(input_desc.get("codec"), "pcm_s16le")
        self.assertEqual(input_desc.get("rate_hz"), 16000)
        self.assertEqual(input_desc.get("channels"), 1)

        input_start = frames[1]
        capture = input_start.get("capture")
        self.assertIsInstance(capture, dict)
        self.assertEqual(capture.get("mode"), "pcm16")
        self.assertEqual(capture.get("container"), "raw")
        self.assertEqual(capture.get("codec"), "pcm_s16le")
        self.assertEqual(capture.get("rate_hz"), 16000)
        self.assertEqual(capture.get("channels"), 1)
        self.assertEqual(capture.get("timeslice_ms"), 50)
        self.assertIs(capture.get("manual_gate"), False)

        policy = input_start.get("policy")
        self.assertIsInstance(policy, dict)
        self.assertEqual(policy, ctx.session_capture_policy)
        self.assertEqual(policy.get("media", {}).get("asr_input"), "pcm_16k")
        self.assertEqual(policy.get("audio", {}).get("pipeline", {}).get("mode"), "pcm16")
        self.assertEqual(policy.get("capture", {}).get("sample_rate"), 16000)
        self.assertEqual(policy.get("capture", {}).get("channels"), 1)

        start_listening = frames[2]
        self.assertEqual(start_listening.get("policy"), policy)

        self.assertEqual(len(sent), 3)

    async def _emit_ready_bundle(
        self, adapter: ChatV2Adapter, ctx: AdapterContext
    ) -> list[dict]:
        sent: list[dict] = []

        async def send(message: dict) -> None:
            sent.append(message)

        await adapter._send_asr_ready_bundle(send, ctx)
        return sent


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
