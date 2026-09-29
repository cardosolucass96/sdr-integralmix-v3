from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.agent.state import IntentType

SupervisorRouteCategory = Literal[
    "AQUAMIX",
    "PECUARIAS",
    "AGRO",
    "PETSHOP",
    "CANAL_PET_ALIMENTAR",
    "AGROPECUARIAS",
    "FALLBACK_AGROPECUARIAS",
]
SupervisorRoutingSegment = Literal[
    "aquaculture_creator",
    "ruminant_creator",
    "other_livestock_creator",
    "petshop_retail",
    "pet_food_grocery",
    "agro_reseller",
    "unknown",
]
SupervisorPurchaseFrequency = Literal["Semanal", "Quinzenal", "Mensal", "Bimestral"]
BrazilianStateCode = Literal[
    "AC",
    "AL",
    "AP",
    "AM",
    "BA",
    "CE",
    "DF",
    "ES",
    "GO",
    "MA",
    "MT",
    "MS",
    "MG",
    "PA",
    "PB",
    "PR",
    "PE",
    "PI",
    "RJ",
    "RN",
    "RS",
    "RO",
    "RR",
    "SC",
    "SP",
    "SE",
    "TO",
]


class IntentClassification(BaseModel):
    intent: IntentType
    reason: str = Field(min_length=1)
    requires_specialist: bool = False
    specialist_name: str | None = None
    specialist_reason: str | None = None


class QualificationFactUpdates(BaseModel):
    """Facts stated or corrected by the lead in the current turn."""

    name: str | None = None
    document: str | None = None
    activity: Literal["criador", "revendedor"] | None = None
    city: str | None = None
    state: BrazilianStateCode | None = None
    species: str | None = None
    frequency: Literal["Semanal", "Quinzenal", "Mensal", "Bimestral"] | None = None
    consumption: str | None = None
    store_name: str | None = None
    works_with_nutrition: Literal["sim", "nao"] | None = Field(
        default=None,
        description=(
            "For a reseller, use 'sim' when the lead explicitly confirms selling animal feed or "
            "nutrition, or when the lead-provided store name clearly says it sells feed, such "
            "as 'Cardoso Rações' or 'Casa da Ração'. Do not infer from generic names such as "
            "'Agro Cardoso' or 'Mundo Animal'. An explicit lead correction takes precedence."
        ),
    )
    product_category: str | None = None
    monthly_volume: str | None = None
    current_brands: str | None = None


class IntegralMixTurnInterpretation(IntentClassification):
    """Validated conversational meaning and incremental qualification facts."""

    is_question: bool = False
    objection: Literal["price", "technical", "other"] | None = None
    refusal: bool = False
    goodbye: bool = False
    human_handoff_requested: bool = False
    question_summary: str | None = None
    contact_profile_name: str | None = Field(
        default=None,
        description=(
            "A personal name clearly recognized in the untrusted Pipefacil contact label, "
            "or null when the label is absent, unclear, generic, or describes a business."
        ),
    )
    qualification_updates: QualificationFactUpdates = Field(
        default_factory=QualificationFactUpdates
    )
    routing_segment: SupervisorRoutingSegment | None = None


class OutboundMediaChoice(BaseModel):
    media_id: str = Field(min_length=1)
    caption: str | None = None
    reason: str = Field(min_length=1)


class OutboundMediaClassification(BaseModel):
    """Validated decision to use, or not use, one catalog media item."""

    action: Literal["send", "none"]
    media_id: str | None = None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_media_id_for_action(self) -> OutboundMediaClassification:
        has_media_id = bool(self.media_id and self.media_id.strip())
        if self.action == "send" and not has_media_id:
            raise ValueError("media_id is required when action is 'send'.")
        if self.action == "none" and self.media_id is not None:
            raise ValueError("media_id must be null when action is 'none'.")
        return self


class GeneratedAudioChoice(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=1600,
        description=("Spoken explanation only. Exact or copyable facts belong in response_text."),
    )
    reason: str = Field(
        min_length=1,
        description="Why spoken audio is more useful than text for this part of the reply.",
    )


ROUTING_SEGMENT_CATEGORIES: dict[SupervisorRoutingSegment, SupervisorRouteCategory] = {
    "aquaculture_creator": "AQUAMIX",
    "ruminant_creator": "PECUARIAS",
    "other_livestock_creator": "AGRO",
    "petshop_retail": "PETSHOP",
    "pet_food_grocery": "CANAL_PET_ALIMENTAR",
    "agro_reseller": "AGROPECUARIAS",
}


class SupervisorHandoffProposal(BaseModel):
    """Validated Pipefacil handoff payload assembled from complete qualification facts."""

    handoff_ready: bool
    name: str | None
    document: str | None
    activity: Literal["criador", "revendedor"] | None
    city: str | None
    state: BrazilianStateCode | None
    species: str | None
    frequency: SupervisorPurchaseFrequency | None
    consumption: str | None
    store_name: str | None
    works_with_nutrition: Literal["sim", "nao"] | None
    product_category: str | None
    monthly_volume: str | None
    current_brands: str | None
    routing_segment: SupervisorRoutingSegment = Field(
        description=(
            "Classify the confirmed lead profile: aquaculture_creator means fish or shrimp; "
            "ruminant_creator means cattle, goats, or sheep; other_livestock_creator means "
            "equines, pigs, poultry, or rabbits; petshop_retail means a pet specialty shop; "
            "pet_food_grocery means pet food sold by grocery/food retail; agro_reseller means "
            "an agricultural or livestock supply reseller; unknown means the profile does not "
            "fit one of these groups with confidence."
        )
    )

    def to_handoff_payload(self) -> dict[str, object] | None:
        if not self.handoff_ready:
            return None
        payload = self.model_dump(exclude={"handoff_ready"})
        if not _is_complete_supervisor_handoff(payload):
            return None
        routing_segment = payload["routing_segment"]
        route_category = ROUTING_SEGMENT_CATEGORIES.get(routing_segment)
        payload["route_categories"] = [route_category] if route_category else []
        return payload


def _is_complete_supervisor_handoff(payload: dict[str, object]) -> bool:
    common_fields = ("name", "document", "activity", "city", "state")
    if any(not str(payload.get(field) or "").strip() for field in common_fields):
        return False

    activity = str(payload.get("activity") or "").strip().casefold()
    if activity == "criador":
        required = ("species", "frequency", "consumption")
    elif activity == "revendedor":
        required = ("store_name", "works_with_nutrition", "product_category", "monthly_volume")
        if str(payload.get("works_with_nutrition") or "").strip().casefold() == "sim":
            required = (*required, "current_brands")
    else:
        return False

    return all(str(payload.get(field) or "").strip() for field in required)


class AgentResponsePlan(BaseModel):
    response_text: str = Field(
        min_length=1,
        description=(
            "WhatsApp text reply, including all exact, scannable, or copyable information."
        ),
    )
    media_choices: list[OutboundMediaChoice] = Field(default_factory=list)
    generated_audio: GeneratedAudioChoice | None = Field(
        default=None,
        description=(
            "Optional spoken explanation. Leave empty for text-only replies; combine with "
            "response_text for hybrid replies."
        ),
    )


# GPT-5.6 native JSON Schema output requires every property to be required and
# does not accept application-only defaults or min/max constraints. These wire
# schemas keep that provider contract separate from Pipefacil's app validation.
class OpenAIIntentClassification(BaseModel):
    intent: IntentType
    reason: str
    requires_specialist: bool
    specialist_name: str | None
    specialist_reason: str | None


class OpenAIQualificationFactUpdates(BaseModel):
    name: str | None
    document: str | None
    activity: Literal["criador", "revendedor"] | None
    city: str | None
    state: BrazilianStateCode | None
    species: str | None
    frequency: Literal["Semanal", "Quinzenal", "Mensal", "Bimestral"] | None
    consumption: str | None
    store_name: str | None
    works_with_nutrition: Literal["sim", "nao"] | None = Field(
        description=(
            "For a reseller, use 'sim' when the lead explicitly confirms selling animal feed or "
            "nutrition, or when the lead-provided store name clearly says it sells feed, such "
            "as 'Cardoso Rações' or 'Casa da Ração'. Do not infer from generic names such as "
            "'Agro Cardoso' or 'Mundo Animal'. An explicit lead correction takes precedence."
        )
    )
    product_category: str | None
    monthly_volume: str | None
    current_brands: str | None


class OpenAIIntegralMixTurnInterpretation(OpenAIIntentClassification):
    is_question: bool
    objection: Literal["price", "technical", "other"] | None
    refusal: bool
    goodbye: bool
    human_handoff_requested: bool
    question_summary: str | None
    contact_profile_name: str | None
    qualification_updates: OpenAIQualificationFactUpdates
    routing_segment: SupervisorRoutingSegment | None


class OpenAIOutboundMediaChoice(BaseModel):
    media_id: str
    caption: str | None
    reason: str


class OpenAIOutboundMediaClassification(BaseModel):
    action: Literal["send", "none"]
    media_id: str | None
    reason: str


class OpenAIGeneratedAudioChoice(BaseModel):
    text: str
    reason: str


class OpenAIAgentResponsePlan(BaseModel):
    response_text: str
    media_choices: list[OpenAIOutboundMediaChoice]
    generated_audio: OpenAIGeneratedAudioChoice | None
