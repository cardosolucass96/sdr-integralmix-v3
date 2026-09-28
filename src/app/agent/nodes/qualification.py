from __future__ import annotations

from typing import Any

from app.agent.qualification import build_qualification_update
from app.agent.state import AgentState


def update_qualification(state: AgentState) -> dict[str, Any]:
    return build_qualification_update(state)


__all__ = ["update_qualification"]
