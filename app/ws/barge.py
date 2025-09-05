import threading

class BargeState:
    def __init__(self):
        self._lock = threading.Lock()
        self._paused = False
        self._timer = None
        self._on_commit = None

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def start(self, confirm_ms: int, on_commit, send_state) -> bool:
        """Enter paused state, schedule confirm; if still paused after confirm_ms, commit."""
        with self._lock:
            if self._paused:
                return False
            self._paused = True
            self._on_commit = on_commit
        try:
            send_state("paused")
        except Exception:
            pass
        # schedule timer
        t = threading.Timer(max(0, confirm_ms or 0)/1000.0, self._confirm_and_commit, [send_state])
        t.daemon = True
        with self._lock:
            self._timer = t
        t.start()
        return True

    def _confirm_and_commit(self, send_state):
        with self._lock:
            if not self._paused:
                return
        self.commit(send_state)

    def cancel(self, send_state):
        """Cancel barge-in; resume speaking."""
        with self._lock:
            self._paused = False
            t = self._timer
            self._timer = None
        try:
            if t: t.cancel()
        except Exception:
            pass
        try:
            send_state("assistant_speaking")
        except Exception:
            pass

    def commit(self, send_state):
        """Commit interrupt immediately; call on_commit, set ready."""
        with self._lock:
            on_commit = self._on_commit
            self._paused = False
            t = self._timer
            self._timer = None
        try:
            if t: t.cancel()
        except Exception:
            pass
        if on_commit:
            try:
                on_commit()
            except Exception:
                pass
        try:
            send_state("ready")
        except Exception:
            pass