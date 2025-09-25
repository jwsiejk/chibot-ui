
from pathlib import Path
import re
code = Path("static/js/voice.js").read_text(encoding="utf-8")
m = re.search(r"const\s+timeslice\s*=\s*(\d+);", code)
assert m, "timeslice constant must be defined"
val = int(m.group(1))
assert 100 <= val <= 200, f"timeslice must be 100–200 ms, got {val}"
assert "state.rec.start(timeslice)" in code, "MediaRecorder.start must use the timeslice constant"
