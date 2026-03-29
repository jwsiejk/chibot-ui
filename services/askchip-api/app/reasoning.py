from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ReasoningMode = Literal['default']


@dataclass(frozen=True)
class ReasoningDecision:
    mode: ReasoningMode
    think: bool
    user_text: str


def route_reasoning(raw_text: str) -> ReasoningDecision:
    return ReasoningDecision(mode='default', think=False, user_text=raw_text.strip())
