import re, inspect, app.ws.adapter as A
src = inspect.getsource(A.ChatV2Adapter._publish)
print("HAS EVT_SESSION_STEP branch:", "if event_type == EVT_SESSION_STEP" in src)
print("HEAD OF _publish:\n", "\n".join(src.splitlines()[:20]))