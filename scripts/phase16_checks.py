import sys, os, json, time
from pathlib import Path
BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))

from app.obs import metrics
from app.api_v1 import admin as admin_mod

def run():
    sink = metrics.MemorySink()
    metrics.set_sink(sink)
    # emit happy path metrics
    metrics.emit_request_metrics("http", True, 123.4, {"route":"/api/v1/greet"})
    metrics.llm_timing(200.0, True, "gpt4o")
    metrics.stt_timing(150.0, True, "en")
    metrics.tts_timing(120.0, False, "elevenlabs")
    metrics.smtp_timing(80.0, True)
    metrics.ws_disconnect("idle_timeout")
    metrics.cost_tally("llm", 3.21)
    names = [e[1] for e in sink.events]
    assert "askchip.requests" in names
    assert "askchip.latency_ms" in names
    assert "askchip.llm.latency_ms" in names
    assert "askchip.stt.latency_ms" in names
    assert "askchip.tts.latency_ms" in names
    assert "askchip.smtp.latency_ms" in names
    assert "askchip.ws.disconnects" in names
    assert "askchip.cost.cents" in names
    # correlation id
    from types import SimpleNamespace
    class DummyReq: headers={"X-Correlation-Id":"abc-123"}
    admin_mod.request = DummyReq
    cid = admin_mod.get_correlation_id()
    assert cid == "abc-123"
    print("PHASE16: PASS")

if __name__ == "__main__":
    run()
