import operator
from typing import Annotated, Any, Literal, NotRequired

from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict

IntentType = Literal["greeting", "question", "request", "fallback"]
ConversationStage = Literal["qualification", "handoff_ready", "closed"]
PendingGoal = Literal[
    "name",
    "document",
    "activity",
    "city",
    "state",
    "species",
    "frequency",
    "consumption",
    "store_name",
    "works_with_nutrition",
    "current_brands",
    "product_category",
    "monthly_volume",
]


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    resume_context: NotRequired[str]
    latest_user_message: NotRequired[str]
    intent: NotRequired[IntentType]
    intent_reason: NotRequired[str]
    requires_specialist: NotRequired[bool]
    specialist_name: NotRequired[str | None]
    specialist_reason: NotRequired[str | None]
    specialist_status: NotRequired[str | None]
    specialist_result: NotRequired[dict[str, Any] | None]
    last_interpretation: NotRequired[dict[str, Any]]
    known_facts: NotRequired[dict[str, Any]]
    fact_sources: NotRequired[dict[str, str]]
    pending_goal: NotRequired[PendingGoal | None]
    conversation_stage: NotRequired[ConversationStage]
    response_text: NotRequired[str]
    response_media: NotRequired[list[dict[str, Any]]]
    response_audio: NotRequired[dict[str, Any] | None]
    supervisor_handoff_request: NotRequired[dict[str, Any] | None]
    supervisor_assignment: NotRequired[dict[str, Any] | None]
    handoff_delivery_status: NotRequired[str]
    status: NotRequired[str]
