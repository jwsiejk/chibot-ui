
import os

def test_docs_present_and_ws_only():
    ws_plan = os.path.join("docs", "WS_PHASE_PLAN.md")
    ops = os.path.join("docs", "OPERATING_INSTRUCTIONS.md")
    for p in (ws_plan, ops):
        assert os.path.exists(p), f"Missing required doc: {p}"
        txt = open(p, "r", encoding="utf-8").read()
        assert "/ws/v1/chat" in txt and "WS-only" in txt, f"Doc {p} must mention WS-only and /ws/v1/chat"
