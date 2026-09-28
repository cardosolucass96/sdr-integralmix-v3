from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.runtime import Runtime

from app.agent.chains import build_integral_mix_interpreter_chain, invoke_with_temperature_fallback
from app.agent.chains.schemas import IntegralMixTurnInterpretation, QualificationFactUpdates
from app.agent.context import AgentRunContext, default_agent_run_context
from app.agent.messages import latest_user_message, serialize_messages
from app.agent.state import AgentState

_RUNTIME_SETTINGS: ContextVar[Any] = ContextVar("integral_mix_interpreter_settings", default=None)


def _build_interpreter_chain(*, use_custom_temperature: bool = True):
    return build_integral_mix_interpreter_chain(
        _RUNTIME_SETTINGS.get(),
        use_custom_temperature=use_custom_temperature,
    )


def _as_interpretation(value: Any) -> IntegralMixTurnInterpretation:
    if isinstance(value, IntegralMixTurnInterpretation):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        value = model_dump()
    return IntegralMixTurnInterpretation.model_validate(value)


def _invoke_interpreter(
    state: AgentState,
    latest_message: str,
    config: RunnableConfig,
    runtime: Runtime[AgentRunContext] | None,
) -> IntegralMixTurnInterpretation:
    context = runtime.context if runtime and runtime.context else default_agent_run_context()
    token = _RUNTIME_SETTINGS.set(context.settings)
    try:
        profile_label = context.lead_profile_label
        result = invoke_with_temperature_fallback(
            _build_interpreter_chain,
            {
                "latest_user_message": latest_message,
                "conversation_history": serialize_messages(state.get("messages", [])),
                "known_facts": json.dumps(
                    state.get("known_facts") or {},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "contact_profile_label": json.dumps(
                    profile_label,
                    ensure_ascii=False,
                ),
            },
            config=config,
        )
    finally:
        _RUNTIME_SETTINGS.reset(token)
    return _as_interpretation(result)


def _interpretation_for_turn(
    state: AgentState,
    latest_message: str,
    config: RunnableConfig,
    runtime: Runtime[AgentRunContext] | None,
) -> IntegralMixTurnInterpretation:
    if not latest_message:
        return IntegralMixTurnInterpretation(
            intent="fallback",
            reason="No user message was available for interpretation.",
        )
    return _invoke_interpreter(state, latest_message, config, runtime)


def interpret_turn(
    state: AgentState,
    config: RunnableConfig = None,
    runtime: Runtime[AgentRunContext] = None,
) -> dict[str, Any]:
    message = latest_user_message(state)
    resume_only = not message and bool(state.get("resume_context"))
    latest_message = message or (
        "Retome a conversa com o lead de forma natural." if resume_only else ""
    )
    interpretation = _interpretation_for_turn(state, latest_message, config, runtime)

    if resume_only:
        interpretation = interpretation.model_copy(
            update={"qualification_updates": QualificationFactUpdates()}
        )

    return {
        "latest_user_message": latest_message,
        "intent": interpretation.intent,
        "intent_reason": interpretation.reason,
        "requires_specialist": interpretation.requires_specialist,
        "specialist_name": interpretation.specialist_name,
        "specialist_reason": interpretation.specialist_reason,
        "last_interpretation": interpretation.model_dump(mode="json"),
        "status": "interpreted",
    }
