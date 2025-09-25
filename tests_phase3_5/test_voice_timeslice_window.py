
from pathlib import Path
import re
code = Path("static/js/voice.js").read_text(encoding="utf-8")
m = re.search(r"rec\.start\((\d+)\)", code)
assert m, "MediaRecorder.start must be called"
val = int(m.group(1))
assert 64 <= val <= 128, f"timeslice must be 64–128 ms, got {val}"
