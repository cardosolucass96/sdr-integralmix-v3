from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage
from langgraph.runtime import Runtime

from app.agent.context import AgentRunContext
from app.agent.state import AgentState

HANDOFF_PENDING_RESPONSE = (
    "Tive uma dificuldade ao encaminhar seus dados neste momento. "
    "As informações desta conversa ficaram registradas para que o atendimento possa ter "
    "continuidade."
)
HANDOFF_UNAVAILABLE_RESPONSE = (
    "Recebi suas informações, mas não consegui confirmar um responsável comercial disponível "
    "para a sua região. Ainda não concluí o encaminhamento e não quero te passar um contato "
    "incorreto."
)


def execute_supervisor_handoff(
    state: AgentState,
    runtime: Runtime[AgentRunContext] = None,
) -> dict[str, Any]:
    request = state.get("supervisor_handoff_request")
    if not request:
        return {}

    context = runtime.context if runtime and runtime.context else None
    action = context.integral_mix_handoff_action if context else None
    if not callable(action):
        return {}

    result = action(request)
    return _handoff_state_update(result)


def _handoff_state_update(result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("handled"):
        return {}

    status = str(result.get("status") or "supervisor_handoff_pending")
    update: dict[str, Any] = {
        "supervisor_handoff_request": None,
        "status": status,
    }
    assignment = result.get("supervisor_assignment")
    if isinstance(assignment, dict):
        update["supervisor_assignment"] = assignment
    delivery_status = result.get("handoff_delivery_status")
    if isinstance(delivery_status, str):
        update["handoff_delivery_status"] = delivery_status
    response = _handoff_response(status)
    if response:
        update["response_text"] = response
        update["messages"] = [AIMessage(content=response)]
    return update


def _handoff_response(status: str) -> str | None:
    if status == "supervisor_handoff_pending":
        return HANDOFF_PENDING_RESPONSE
    if status == "supervisor_handoff_unavailable":
        return HANDOFF_UNAVAILABLE_RESPONSE
    return None


__all__ = [
    "HANDOFF_PENDING_RESPONSE",
    "HANDOFF_UNAVAILABLE_RESPONSE",
    "execute_supervisor_handoff",
]
