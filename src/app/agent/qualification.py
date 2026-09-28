from __future__ import annotations

from typing import Any

from app.agent.chains.schemas import (
    IntegralMixTurnInterpretation,
    SupervisorHandoffProposal,
)

_FACT_FIELDS = (
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
    "product_category",
    "monthly_volume",
    "current_brands",
)
_CREATOR_FIELDS = ("species", "frequency", "consumption")
_RESELLER_FIELDS = (
    "store_name",
    "works_with_nutrition",
    "product_category",
    "monthly_volume",
    "current_brands",
)
_NO_NUTRITION_BRANDS_MARKER = "Não trabalha atualmente com nutrição animal"


def _interpretation(state: dict[str, Any]) -> IntegralMixTurnInterpretation:
    return IntegralMixTurnInterpretation.model_validate(state.get("last_interpretation") or {})


def _merge_fact_updates(
    current_facts: dict[str, Any],
    current_sources: dict[str, str],
    interpretation: IntegralMixTurnInterpretation,
) -> tuple[dict[str, Any], dict[str, str]]:
    facts = dict(current_facts)
    sources = dict(current_sources)
    updates = interpretation.qualification_updates.model_dump(exclude_none=True)

    new_activity = updates.get("activity")
    old_activity = facts.get("activity")
    if new_activity and old_activity and new_activity != old_activity:
        obsolete_fields = _CREATOR_FIELDS if new_activity == "revendedor" else _RESELLER_FIELDS
        for field in obsolete_fields:
            facts.pop(field, None)
            sources.pop(field, None)
        facts.pop("routing_segment", None)
        sources.pop("routing_segment", None)

    for field in _FACT_FIELDS:
        value = updates.get(field)
        if isinstance(value, str):
            value = value.strip()
        if value:
            facts[field] = value
            sources[field] = "lead_message"

    if facts.get("activity") == "revendedor":
        if facts.get("works_with_nutrition") == "nao":
            facts["current_brands"] = _NO_NUTRITION_BRANDS_MARKER
            sources["current_brands"] = "business_rule"
        elif (
            facts.get("works_with_nutrition") == "sim"
            and sources.get("current_brands") == "business_rule"
        ):
            facts.pop("current_brands", None)
            sources.pop("current_brands", None)

    if interpretation.routing_segment is not None:
        facts["routing_segment"] = interpretation.routing_segment
        sources["routing_segment"] = "structured_classification"

    return facts, sources


def _pending_goal(facts: dict[str, Any]) -> str | None:
    required = ["name", "document", "activity"]
    activity = facts.get("activity")
    if activity == "criador":
        required.extend(("city", "state", *_CREATOR_FIELDS))
    elif activity == "revendedor":
        required.extend(("store_name", "city", "state", "works_with_nutrition"))
        if facts.get("works_with_nutrition") != "nao":
            required.append("current_brands")
        required.extend(("product_category", "monthly_volume"))
    else:
        return next((field for field in required if not _has_fact(facts, field)), "activity")
    return next((field for field in required if not _has_fact(facts, field)), None)


def _has_fact(facts: dict[str, Any], field: str) -> bool:
    value = facts.get(field)
    return value is not None and bool(str(value).strip())


def _build_handoff_request(facts: dict[str, Any]) -> dict[str, object] | None:
    proposal = SupervisorHandoffProposal(
        handoff_ready=True,
        name=facts.get("name"),
        document=facts.get("document"),
        activity=facts.get("activity"),
        city=facts.get("city"),
        state=facts.get("state"),
        species=facts.get("species"),
        frequency=facts.get("frequency"),
        consumption=facts.get("consumption"),
        store_name=facts.get("store_name"),
        works_with_nutrition=facts.get("works_with_nutrition"),
        product_category=facts.get("product_category"),
        monthly_volume=facts.get("monthly_volume"),
        current_brands=facts.get("current_brands"),
        routing_segment=facts.get("routing_segment", "unknown"),
    )
    return proposal.to_handoff_payload()


def build_qualification_update(state: dict[str, Any]) -> dict[str, Any]:
    interpretation = _interpretation(state)
    facts, sources = _merge_fact_updates(
        dict(state.get("known_facts") or {}),
        dict(state.get("fact_sources") or {}),
        interpretation,
    )
    pending_goal = _pending_goal(facts)
    handoff_request = None
    stage = "qualification"

    if interpretation.refusal or interpretation.goodbye:
        stage = "closed"
    elif pending_goal is None:
        stage = "handoff_ready"
        if not state.get("supervisor_assignment"):
            handoff_request = _build_handoff_request(facts)

    update: dict[str, Any] = {
        "known_facts": facts,
        "fact_sources": sources,
        "pending_goal": pending_goal,
        "conversation_stage": stage,
        "supervisor_handoff_request": handoff_request,
    }
    if handoff_request is not None:
        update.update(
            {
                "status": "supervisor_handoff_ready",
                "response_text": "",
                "response_media": [],
                "response_audio": None,
            }
        )
    return update


__all__ = ["build_qualification_update"]
