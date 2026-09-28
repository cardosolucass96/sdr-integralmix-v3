from __future__ import annotations

from app.agent.chains.schemas import (
    IntegralMixTurnInterpretation,
    QualificationFactUpdates,
)
from app.agent.nodes.qualification import update_qualification
from app.agent.routing import route_after_qualification


def _turn(
    *,
    updates: dict[str, object] | None = None,
    routing_segment: str | None = None,
    refusal: bool = False,
    goodbye: bool = False,
) -> dict[str, object]:
    interpretation = IntegralMixTurnInterpretation(
        intent="request",
        reason="Lead respondeu à qualificação.",
        refusal=refusal,
        goodbye=goodbye,
        qualification_updates=QualificationFactUpdates.model_validate(updates or {}),
        routing_segment=routing_segment,
    )
    return {"last_interpretation": interpretation.model_dump(mode="json")}


def test_creator_facts_create_deterministic_handoff_only_when_complete() -> None:
    result = update_qualification(
        _turn(
            updates={
                "name": "Maria",
                "document": "12345678901",
                "activity": "criador",
                "city": "Fortaleza",
                "state": "CE",
                "species": "Peixes",
                "frequency": "Mensal",
                "consumption": "R$ 3.000",
                "routing_segment": "aquaculture_creator",
            },
            routing_segment="aquaculture_creator",
        )
    )

    assert result["pending_goal"] is None
    assert result["conversation_stage"] == "handoff_ready"
    assert result["status"] == "supervisor_handoff_ready"
    assert result["supervisor_handoff_request"]["route_categories"] == ["AQUAMIX"]
    assert route_after_qualification({**result}) == "supervisor-handoff-action"


def test_incomplete_turn_keeps_facts_and_routes_to_response() -> None:
    result = update_qualification(
        {
            **_turn(updates={"name": "Maria", "activity": "criador"}),
            "known_facts": {"document": "12345678901"},
            "fact_sources": {"document": "lead_message"},
        }
    )

    assert result["known_facts"] == {
        "document": "12345678901",
        "name": "Maria",
        "activity": "criador",
    }
    assert result["fact_sources"]["name"] == "lead_message"
    assert result["pending_goal"] == "city"
    assert result["supervisor_handoff_request"] is None
    assert route_after_qualification(result) == "respond"


def test_reseller_who_does_not_work_with_nutrition_skips_brand_question() -> None:
    result = update_qualification(
        _turn(
            updates={
                "name": "Maria",
                "document": "12345678901",
                "activity": "revendedor",
                "store_name": "Agro Maria",
                "city": "Fortaleza",
                "state": "CE",
                "works_with_nutrition": "nao",
                "product_category": "Ração",
                "monthly_volume": "R$ 10.000",
            },
            routing_segment="agro_reseller",
        )
    )

    assert result["pending_goal"] is None
    assert result["known_facts"]["current_brands"] == (
        "Não trabalha atualmente com nutrição animal"
    )
    assert result["fact_sources"]["current_brands"] == "business_rule"
    assert result["supervisor_handoff_request"]["route_categories"] == ["AGROPECUARIAS"]


def test_explicit_correction_replaces_old_value_and_source() -> None:
    result = update_qualification(
        {
            **_turn(updates={"city": "Sobral"}),
            "known_facts": {"city": "Fortaleza"},
            "fact_sources": {"city": "lead_message"},
        }
    )

    assert result["known_facts"]["city"] == "Sobral"
    assert result["fact_sources"]["city"] == "lead_message"


def test_refusal_or_existing_assignment_prevents_handoff() -> None:
    full_profile = {
        "name": "Maria",
        "document": "12345678901",
        "activity": "criador",
        "city": "Fortaleza",
        "state": "CE",
        "species": "Peixes",
        "frequency": "Mensal",
        "consumption": "R$ 3.000",
    }
    refusal = update_qualification({**_turn(updates=full_profile, refusal=True), "known_facts": {}})
    assigned = update_qualification(
        {
            **_turn(updates=full_profile),
            "known_facts": full_profile,
            "supervisor_assignment": {"supervisor_id": 7},
        }
    )

    assert refusal["conversation_stage"] == "closed"
    assert refusal["supervisor_handoff_request"] is None
    assert assigned["supervisor_handoff_request"] is None
    assert route_after_qualification(assigned) == "respond"


def test_changing_activity_clears_facts_from_previous_profile() -> None:
    result = update_qualification(
        {
            **_turn(updates={"activity": "revendedor"}),
            "known_facts": {
                "activity": "criador",
                "species": "Peixes",
                "frequency": "Mensal",
                "consumption": "R$ 3.000",
            },
            "fact_sources": {
                "activity": "lead_message",
                "species": "lead_message",
                "frequency": "lead_message",
                "consumption": "lead_message",
                "routing_segment": "structured_classification",
            },
        }
    )

    assert result["known_facts"]["activity"] == "revendedor"
    assert (
        not {
            "species",
            "frequency",
            "consumption",
            "routing_segment",
        }
        & result["known_facts"].keys()
    )
    assert result["pending_goal"] == "name"
