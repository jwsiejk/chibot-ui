from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_user_turn_is_converted_to_chat_message() -> None:
    script = Path(__file__).parent / "js" / "user_turn_delivers_chat_message.mjs"
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

    message = payload.get("message") or {}
    assert message.get("role") == "user"
    assert message.get("text") == "discuss Pure Storage together"
    assert message.get("turn_index") == 1
