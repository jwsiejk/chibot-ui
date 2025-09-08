
import sys, json, os, requests
base = os.environ.get("ASKCHIP_BASE","http://localhost:8000")
sess = os.environ.get("ASKCHIP_SESSION","diag")
payload = {"session_id": sess, "text": "Hello Chip", "cmd": "user_text"}
r = requests.post(f"{base}/api/v1/chat", json=payload, timeout=20)
print("status:", r.status_code)
print("body:", r.text[:4000])
