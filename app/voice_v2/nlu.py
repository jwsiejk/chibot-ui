"""Rule-based NLU adapter used by the voice engine."""
from __future__ import annotations

import re
from typing import Dict


class NLUAdapter:
    """Deterministic, lightweight intent and entity extractor."""

    _INTENT_KEYWORDS = {
        "greeting": {"hello", "hi", "hey"},
        "goodbye": {"bye", "goodbye", "see you"},
        "status.check": {"status", "progress", "update"},
        "support.request": {"help", "support", "assist", "issue"},
        "order.lookup": {"order", "shipment", "tracking"},
    }
    _ORDER_RE = re.compile(r"\border(?:\s+number)?\s*(?P<order>[A-Za-z0-9-]{3,})", re.IGNORECASE)
    _PRODUCT_RE = re.compile(r"\bproduct\s+(?P<product>[A-Za-z0-9-]+)", re.IGNORECASE)
    _TICKET_RE = re.compile(r"\b(ticket|case)\s*(?P<ticket>[A-Za-z0-9-]{3,})", re.IGNORECASE)

    def extract(self, req_id: str, text: str) -> Dict[str, object]:
        """Return a stable NLU interpretation for ``text``.

        The adapter intentionally keeps the implementation minimal so it can
        run synchronously inside the engine hot path. Keyword matching is used
        to infer intents and a few regexes capture simple entity candidates.
        ``req_id`` is unused by the extractor today but is accepted so callers
        can pass through contextual identifiers without branching.
        """

        source = text or ""
        normalized = source.strip().lower()
        intent = "chitchat.fallback"
        confidence = 0.35

        for candidate, keywords in self._INTENT_KEYWORDS.items():
            if any(keyword in normalized for keyword in keywords):
                intent = candidate
                confidence = 0.82
                break

        entities: Dict[str, str] = {}
        if normalized:
            order_match = self._ORDER_RE.search(source)
            if order_match:
                entities["order"] = order_match.group("order")

            product_match = self._PRODUCT_RE.search(source)
            if product_match:
                entities["product"] = product_match.group("product")

            ticket_match = self._TICKET_RE.search(source)
            if ticket_match:
                entities["ticket"] = ticket_match.group("ticket")

        return {"intent": intent, "entities": entities, "confidence": confidence}


__all__ = ["NLUAdapter"]
