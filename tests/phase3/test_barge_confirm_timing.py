
import time
from app.ws.barge import BargeState

def test_barge_confirm_occurs_around_420ms():
    b = BargeState()
    commits = []
    states = []
    start = time.perf_counter()

    def on_commit():
        commits.append(time.perf_counter())

    def send_state(s):
        states.append((s, time.perf_counter()))

    # Start barge-in with ~420ms confirm
    assert b.start(confirm_ms=420, on_commit=on_commit, send_state=send_state) is True

    # Immediately should be paused
    assert b.is_paused() is True
    # Wait up to 1.5s for commit
    deadline = time.perf_counter() + 1.5
    while not commits and time.perf_counter() < deadline:
        time.sleep(0.01)

    assert commits, "Commit not triggered"
    commit_time = commits[0] - start
    # Acceptable window: 0.3s to 0.8s to allow CI variance
    assert 0.3 <= commit_time <= 0.8, f"Commit at {commit_time:.3f}s, outside 0.3-0.8s window"

    # After commit, barge should be not paused
    assert b.is_paused() is False
