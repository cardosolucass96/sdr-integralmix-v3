from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from app.agent.interpretation import interpret_turn as interpret_conversation_turn
from app.agent.state import AgentState


def interpret_turn(
    state: AgentState,
    config: RunnableConfig = None,
    runtime: Runtime[Any] = None,
) -> dict[str, Any]:
    return interpret_conversation_turn(state, config=config, runtime=runtime)


__all__ = ["interpret_turn"]
