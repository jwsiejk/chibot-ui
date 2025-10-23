"""Deterministic policy decider used by the voice engine."""
from __future__ import annotations

from typing import Mapping, MutableMapping


EVT_POLICY_DECISION = "EVT_POLICY_DECISION"


class PolicyDecider:
    """Return simple policy decisions based on the current snapshot."""

    def decide(
        self,
        req_id: str,
        nlu: Mapping[str, object] | None,
        policy_snapshot: Mapping[str, object] | None,
    ) -> MutableMapping[str, object]:
        """Compute the dialog action for the current turn.

        The implementation is intentionally lightweight. It inspects the
        provided ``policy_snapshot`` (if any) to mirror the boolean flags that
        downstream components expect while always returning an action of
        ``"respond"`` so the stubbed LLM path can be exercised in tests.
        """

        if not isinstance(req_id, str) or not req_id:
            raise ValueError("req_id must be a non-empty string")

        snapshot = dict(policy_snapshot or {})
        action = "respond"
        barge_in_enabled = bool(snapshot.get("barge_in_enabled", False))
        auto_commit = bool(snapshot.get("auto_commit_when_ready", False))

        return {
            "action": action,
            "barge_in_enabled": barge_in_enabled,
            "auto_commit_when_ready": auto_commit,
        }


__all__ = ["PolicyDecider", "EVT_POLICY_DECISION"]
