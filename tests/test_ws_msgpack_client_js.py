from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_ws_client_msgpack_roundtrip() -> None:
    script = Path(__file__).parent / "js" / "ws_msgpack_roundtrip.mjs"
    result = subprocess.run(
        ["node", str(script)],
        capture_output=True,
        text=True,
        check=True,
    )
    output_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    assert output_lines, "Expected JSON payload on stdout"
    payload = json.loads(output_lines[-1])
    assert payload.get("ok") is True, f"Unexpected payload: {payload}"
