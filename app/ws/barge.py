import threading
import time

class BargeState:
    def __init__(self):
        self._lock = threading.Lock()
        self._paused = False
        self._timer = None
        self._on_commit = None
        self._deadline = None

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def start(self, confirm_ms: int, on_commit, send_state, *, auto_commit: bool = True) -> bool:
        """Enter paused state, schedule confirm; if still paused after confirm_ms, commit."""
        with self._lock:
            if self._paused:
                return False
            self._paused = True
            self._on_commit = on_commit
            if confirm_ms:
                self._deadline = time.monotonic() + max(0, confirm_ms) / 1000.0
            else:
                self._deadline = None
        try:
            send_state("paused")
        except Exception:
            pass
        if auto_commit:
            delay = max(0, confirm_ms or 0) / 1000.0
            t = threading.Timer(delay, self._confirm_and_commit, [send_state])
            t.daemon = True
            with self._lock:
                self._timer = t
            t.start()
        else:
            with self._lock:
                self._timer = None
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
            self._deadline = None
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
            self._on_commit = None
            self._paused = False
            t = self._timer
            self._timer = None
            self._deadline = None
        try:
            if t: t.cancel()
        except Exception:
            pass
        wait_token = None            
        if on_commit:
            try:
                wait_token = on_commit()
            except Exception:
                wait_token = None

        def _await_ready(token):
            if token is None:
                return
            if isinstance(token, (list, tuple, set)):
                for part in token:
                    _await_ready(part)
                return
            wait_fn = getattr(token, "wait", None)
            if callable(wait_fn):
                try:
                    wait_fn()
                except Exception:
                    pass
                return
            if callable(token):
                try:
                    token()
                except Exception:
                    pass

        _await_ready(wait_token)
        try:
            send_state("ready")
        except Exception:
            pass

    def confirm_deadline(self):
        with self._lock:
            return self._deadline
