
# app/services/test_runner.py
from __future__ import annotations
import time, threading, uuid, json
from typing import Dict, List, Any, Tuple

from ..db import db
from .streaming import make_assistant_frames

_TEST_RUNS: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Lock()

def _log(run_id: str, kind: str, msg: str, **extra: Any) -> None:
    rec = {"ts": time.time(), "kind": kind, "msg": msg}
    if extra:
        rec.update(extra)
    with _LOCK:
        _TEST_RUNS[run_id]["logs"].append(rec)

def start_test(mode: str = "voice") -> str:
    """Start a test run. mode in {"voice","chat"}"""
    run_id = uuid.uuid4().hex[:10]
    with _LOCK:
        _TEST_RUNS[run_id] = {
            "id": run_id,
            "mode": mode,
            "status": "running",
            "created_at": time.time(),
            "logs": [],
            "result": {},
        }
    t = threading.Thread(target=_run, args=(run_id, mode), daemon=True)
    t.start()
    return run_id

def _run(run_id: str, mode: str) -> None:
    sid = f"testrun-{run_id}"
    s0 = db.get_config()
    audio_on_before = s0.get("feature_audio", True)

    try:
        # Adjust audio setting based on mode
        want_audio = (mode == "voice")
        db.update_config({"feature_audio": want_audio})
        s = db.get_config()

        _log(run_id, "start", "test run started", mode=mode, settings=s)

        # Step 1: GREET
        _log(run_id, "greet:req", "calling make_assistant_frames('greet')")
        tid, frames = make_assistant_frames("greet", sid)
        _log(run_id, "greet:ok", "frames ready", turn_id=tid, n=len(frames))

        # Summarize audio bytes and visemes if present
        a_sizes = [len(f.get("audio_bytes", b"")) for f in frames if f.get("type")=="audio"]
        vis_cts = [len(f.get("visemes", [])) for f in frames if f.get("type")=="audio"]
        _log(run_id, "greet:audio", "summary", audio_chunks=len(a_sizes), total_bytes=sum(a_sizes), viseme_sets=sum(1 for v in vis_cts if v))

        # Step 2: CHAT TURN
        _log(run_id, "chat:req", "calling make_assistant_frames('chat')", text="Run a built-in health check.")
        tid2, frames2 = make_assistant_frames("chat", sid)
        _log(run_id, "chat:ok", "frames ready", turn_id=tid2, n=len(frames2))

        a2_sizes = [len(f.get("audio_bytes", b"")) for f in frames2 if f.get("type")=="audio"]
        v2_cts = [len(f.get("visemes", [])) for f in frames2 if f.get("type")=="audio"]
        _log(run_id, "chat:audio", "summary", audio_chunks=len(a2_sizes), total_bytes=sum(a2_sizes), viseme_sets=sum(1 for v in v2_cts if v))

        status = "ok"
        _log(run_id, "done", "test run complete", status=status)
        with _LOCK:
            _TEST_RUNS[run_id]["status"] = status
            _TEST_RUNS[run_id]["result"] = {
                "greet_frames": len(frames),
                "chat_frames": len(frames2),
                "audio_enabled": s.get("feature_audio", True),
            }
    except Exception as e:
        _log(run_id, "error", str(e))
        with _LOCK:
            _TEST_RUNS[run_id]["status"] = "fail"
            _TEST_RUNS[run_id]["result"] = {"error": repr(e)}
    finally:
        # restore audio setting
        db.update_config({"feature_audio": audio_on_before})

def get(run_id: str) -> Dict[str, Any] | None:
    with _LOCK:
        return dict(_TEST_RUNS.get(run_id) or {})

def list_runs(limit: int = 20) -> List[Dict[str, Any]]:
    with _LOCK:
        runs = list(_TEST_RUNS.values())
    runs.sort(key=lambda r: r["created_at"], reverse=True)
    return [dict(r) for r in runs[:limit]]
