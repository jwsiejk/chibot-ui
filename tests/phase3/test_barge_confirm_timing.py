
import time
from app.ws.barge import BargeState

def test_barge_confirm_occurs_around_420ms():
    b = BargeState()
    commits = []
    states = []
    start = time.perf_counter()
    def on_commit(): commits.append(time.perf_counter())
    def send_state(s): states.append((s, time.perf_counter()))
    assert b.start(confirm_ms=420, on_commit=on_commit, send_state=send_state) is True
    assert b.is_paused() is True
    deadline = time.perf_counter() + 1.5
    while not commits and time.perf_counter() < deadline:
        time.sleep(0.01)
    assert commits, "Commit not triggered"
    commit_time = commits[0] - start
    assert 0.3 <= commit_time <= 0.8, f"Commit at {commit_time:.3f}s"
    assert b.is_paused() is False
