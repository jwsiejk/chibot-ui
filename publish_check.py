import inspect, re, app.ws.adapter as A
src = inspect.getsource(A.ChatV2Adapter._publish)
print("HAS SESSION_STEP branch:", bool(re.search(r'if\s+event_type\s*==\s*SESSION_STEP\s*:', src)))
print("\n--- _publish head (first ~40 lines) ---\n")
print("\n".join(src.splitlines()[:40]))