"""Policy management service providing runtime snapshots and broadcasts."""
from __future__ import annotations

import logging
import threading
from typing import Callable, Dict, Iterable, List, Mapping, Optional

from app.db.admin_settings import AdminSettingsStore
from app.policy.schema import (
    AuthPolicyMode,
    AuthPolicySnapshot,
    DEFAULT_AUTH_POLICY,
    DEFAULT_WS_AUTH_MODE,
    build_auth_policy_snapshot,
    coerce_ws_auth_mode,
)
from app.telemetry import bus as telemetry_bus

_LOGGER = logging.getLogger(__name__)

PolicyListener = Callable[[Dict[str, object]], None]


class PolicyService:
    """Runtime accessors for policy state derived from admin settings."""

    _AUTH_MODE_KEY = "ws_auth_mode"

    def __init__(
        self,
        store: AdminSettingsStore | None = None,
        *,
        bus=telemetry_bus,
    ) -> None:
        self._store = store or AdminSettingsStore()
        self._bus = bus
        self._lock = threading.RLock()
        self._snapshot: AuthPolicySnapshot = dict(DEFAULT_AUTH_POLICY)
        self._ws_listeners: List[PolicyListener] = []
        self._sse_listeners: List[PolicyListener] = []
        self._load_initial_snapshot()

    # ------------------------------------------------------------------
    # Snapshot lifecycle helpers
    # ------------------------------------------------------------------
    def _load_initial_snapshot(self) -> None:
        snapshot = self._load_snapshot_from_store()
        with self._lock:
            self._snapshot = snapshot
        self._publish_snapshot(snapshot, reason="load")

    def _load_snapshot_from_store(self) -> AuthPolicySnapshot:
        value = self._store.get(self._AUTH_MODE_KEY)
        payload: Mapping[str, object] | None
        if value is None:
            payload = None
        else:
            payload = {self._AUTH_MODE_KEY: value}
        return build_auth_policy_snapshot(payload)

    def _publish_snapshot(self, snapshot: AuthPolicySnapshot, *, reason: str) -> None:
        try:
            self._bus.publish(
                {
                    "type": "EVT_POLICY_SNAPSHOT",
                    "level": "info",
                    "meta": {
                        "ws_auth_mode": snapshot["ws_auth_mode"],
                        "reason": reason,
                    },
                }
            )
        except Exception:  # pragma: no cover - telemetry must not break flow
            _LOGGER.exception("Failed to publish policy snapshot telemetry")

        payload = {"type": "policy_updated", "policy": dict(snapshot)}
        self._notify_listeners(self._ws_listeners, payload)
        self._notify_listeners(self._sse_listeners, payload)

    def _notify_listeners(self, listeners: Iterable[PolicyListener], payload: Dict[str, object]) -> None:
        for callback in list(listeners):
            try:
                callback(dict(payload))
            except Exception:  # pragma: no cover - defensive around callbacks
                _LOGGER.exception("Policy listener error for payload %s", payload.get("type"))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_auth_policy(self) -> AuthPolicySnapshot:
        """Return the cached auth policy snapshot."""

        with self._lock:
            return dict(self._snapshot)

    def set_ws_auth_mode(self, mode: AuthPolicyMode | str) -> AuthPolicySnapshot:
        """Persist the provided auth mode and broadcast updates when needed."""

        normalized = coerce_ws_auth_mode(mode)
        existing = self.get_auth_policy()
        if existing["ws_auth_mode"] == normalized:
            return existing

        self._store.set(self._AUTH_MODE_KEY, normalized)
        snapshot = {"ws_auth_mode": normalized}
        with self._lock:
            self._snapshot = snapshot
        self._publish_snapshot(snapshot, reason="change")
        return dict(snapshot)

    def refresh(self) -> AuthPolicySnapshot:
        """Reload the snapshot from the backing store and broadcast changes."""

        snapshot = self._load_snapshot_from_store()
        with self._lock:
            if snapshot == self._snapshot:
                return dict(self._snapshot)
            self._snapshot = snapshot
        self._publish_snapshot(snapshot, reason="refresh")
        return dict(snapshot)

    def handle_admin_setting_change(
        self, key: str, value: Optional[str] = None
    ) -> Optional[AuthPolicySnapshot]:
        """Handle an external admin_settings update notification."""

        if key != self._AUTH_MODE_KEY:
            return None
        if value is not None:
            normalized = coerce_ws_auth_mode(value, DEFAULT_WS_AUTH_MODE)
            snapshot = {"ws_auth_mode": normalized}
            with self._lock:
                if snapshot == self._snapshot:
                    return dict(self._snapshot)
                self._snapshot = snapshot
            self._publish_snapshot(snapshot, reason="change")
            return dict(snapshot)
        return self.refresh()

    # ------------------------------------------------------------------
    # Listener registration
    # ------------------------------------------------------------------
    def register_ws_listener(self, callback: PolicyListener, *, replay: bool = False) -> Callable[[], None]:
        """Register a callback for WebSocket broadcasts."""

        if not callable(callback):
            raise TypeError("callback must be callable")

        with self._lock:
            self._ws_listeners.append(callback)
            snapshot = dict(self._snapshot)

        if replay:
            callback({"type": "policy_updated", "policy": snapshot})

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._ws_listeners.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    def register_sse_listener(self, callback: PolicyListener, *, replay: bool = False) -> Callable[[], None]:
        """Register a callback for server-sent event broadcasts."""

        if not callable(callback):
            raise TypeError("callback must be callable")

        with self._lock:
            self._sse_listeners.append(callback)
            snapshot = dict(self._snapshot)

        if replay:
            callback({"type": "policy_updated", "policy": snapshot})

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._sse_listeners.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe


__all__ = ["PolicyService", "PolicyListener"]
