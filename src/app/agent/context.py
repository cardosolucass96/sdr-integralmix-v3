"""Per-invocation dependencies and hints for LangGraph nodes.

Values here are not part of graph state and are never checkpointed. The interpreter validates
profile labels before promoting a recognized personal name to durable conversation facts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from langgraph.runtime import Runtime

from app.core.config import (
    RuntimeSettings,
    Settings,
    build_execution_settings,
    get_bootstrap_settings,
)


@dataclass(frozen=True, slots=True)
class AgentRunContext:
    settings: RuntimeSettings
    integral_mix_handoff_action: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    lead_profile_label: str | None = None


def default_agent_run_context() -> AgentRunContext:
    """Fallback for direct LangGraph Studio runs without a FastAPI request."""

    return AgentRunContext(settings=RuntimeSettings())


def execution_settings_from_runtime(
    runtime: Runtime[AgentRunContext] | None,
) -> Settings:
    if runtime is None or runtime.context is None:
        return build_execution_settings(get_bootstrap_settings(), RuntimeSettings())
    context = runtime.context if runtime and runtime.context else default_agent_run_context()
    return build_execution_settings(get_bootstrap_settings(), context.settings)
