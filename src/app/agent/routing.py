"""Node names and routing helpers for the agent graph."""

from typing import Literal

from app.agent.state import AgentState

CLASSIFY_INTENT_NODE = "classify-intent"
INTERPRET_TURN_NODE = "interpret-turn"
UPDATE_QUALIFICATION_NODE = "update-qualification"
DELEGATE_SPECIALIST_NODE = "delegate-specialist"
RESPOND_NODE = "respond"
SUPERVISOR_HANDOFF_NODE = "supervisor-handoff-action"


def route_after_interpretation(
    state: AgentState,
) -> Literal["delegate-specialist", "update-qualification"]:
    if state.get("requires_specialist") and state.get("specialist_name"):
        return DELEGATE_SPECIALIST_NODE
    return UPDATE_QUALIFICATION_NODE


def route_after_qualification(
    state: AgentState,
) -> Literal["respond", "supervisor-handoff-action"]:
    if state.get("supervisor_handoff_request"):
        return SUPERVISOR_HANDOFF_NODE
    return RESPOND_NODE


def route_after_intent(state: AgentState) -> Literal["delegate-specialist", "respond"]:
    if state.get("requires_specialist") and state.get("specialist_name"):
        return DELEGATE_SPECIALIST_NODE
    return RESPOND_NODE


__all__ = [
    "CLASSIFY_INTENT_NODE",
    "INTERPRET_TURN_NODE",
    "UPDATE_QUALIFICATION_NODE",
    "DELEGATE_SPECIALIST_NODE",
    "RESPOND_NODE",
    "SUPERVISOR_HANDOFF_NODE",
    "route_after_intent",
    "route_after_interpretation",
    "route_after_qualification",
]
