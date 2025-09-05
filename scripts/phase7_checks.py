
#!/usr/bin/env python3
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(__file__))

def read(p):
    return open(p,"r",encoding="utf-8",errors="ignore").read()

ok=True
def assert_true(c, msg):
    global ok
    print(("PASS" if c else "FAIL")+": "+msg)
    if not c: ok=False

# 1. provider abstraction
prov = read(os.path.join(ROOT,"app/services/llm_provider.py"))
assert_true("get_provider(" in prov and "load_provider(" in prov, "llm_provider abstraction present")

# 2. config keys
db = read(os.path.join(ROOT,"app/db.py"))
assert_true("'llm_provider'" in db and "'openai_model'" in db, "config has llm_provider and openai_model")

# 3. streaming uses provider + persona + teacher_move
stream = read(os.path.join(ROOT,"app/services/streaming.py"))
assert_true("provider = get_provider(cfg)" in stream, "streaming loads provider")
assert_true(("annotate((seed_text or" in stream) or ("from .awareness import annotate" in stream), "awareness annotate used")
assert_true("persona_id" in stream and "db.memory.get('personas'" in stream, "persona fetched from db")
assert_true("provider.generate_reply" in stream, "provider.generate_reply is called")

# 4. API endpoints pass session_id
greet = read(os.path.join(ROOT,"app/api_v1/greet.py"))
chat = read(os.path.join(ROOT,"app/api_v1/chat.py"))
voice = read(os.path.join(ROOT,"app/api_v1/voice.py"))
assert_true("make_assistant_frames(\"greet\", sid)" in greet, "greet passes sid")
assert_true("make_assistant_frames((text or \"chat\"), sid)" in chat, "chat passes sid")
assert_true("make_assistant_frames(text or \"voice\", sid)" in voice, "voice passes sid")

# 5. server emits assistant_* frames somewhere pre-WS (streaming)
assert_true("\"assistant_chunk\"" in stream and "\"assistant_end\"" in stream, "assistant_* frames generated in streaming")

# 6. openai provider stub exists and no network use in tests
openai = read(os.path.join(ROOT,"app/services/providers/openai_provider.py"))
assert_true(("urllib.request" in openai) and ("OPENAI_API_KEY" in openai), "openai provider: network path present")
assert_true(("fallback" in openai) or ("openai-stub" in openai), "openai provider: fallback path present when no key")
assert_true("os.environ.get(\"OPENAI_MODEL\"" in openai, "openai model env read")

# 7. route linter
import subprocess
proc = subprocess.run([sys.executable, os.path.join(ROOT,"scripts","route_linter.py")], capture_output=True, text=True)
print(proc.stdout)
assert_true(proc.returncode == 0, "route linter passes")

print("\nRESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
