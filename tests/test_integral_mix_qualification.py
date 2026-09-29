from __future__ import annotations

from app.agent.chains.schemas import (
    IntegralMixTurnInterpretation,
    OpenAIQualificationFactUpdates,
    QualificationFactUpdates,
)
from app.agent.nodes.qualification import update_qualification
from app.agent.routing import route_after_interpretation, route_after_qualification


def _turn(
    *,
    updates: dict[str, object] | None = None,
    contact_profile_name: str | None = None,
    routing_segment: str | None = None,
    refusal: bool = False,
    goodbye: bool = False,
) -> dict[str, object]:
    interpretation = IntegralMixTurnInterpretation(
        intent="request",
        reason="Lead respondeu à qualificação.",
        refusal=refusal,
        goodbye=goodbye,
        contact_profile_name=contact_profile_name,
        qualification_updates=QualificationFactUpdates.model_validate(updates or {}),
        routing_segment=routing_segment,
    )
    return {"last_interpretation": interpretation.model_dump(mode="json")}


def test_valid_contact_profile_name_is_reused() -> None:
    result = update_qualification(_turn(contact_profile_name="  Maria da Silva  "))

    assert result["known_facts"]["name"] == "Maria da Silva"
    assert result["fact_sources"]["name"] == "contact_profile"
    assert result["pending_goal"] == "document"


def test_invalid_contact_profile_name_keeps_name_as_next_question() -> None:
    # Structured interpretation rejects the business label before qualification.
    result = update_qualification(_turn(contact_profile_name=None))

    assert "name" not in result["known_facts"]
    assert result["pending_goal"] == "name"


def test_lead_message_name_overrides_valid_contact_profile_name() -> None:
    result = update_qualification(
        _turn(updates={"name": "Ana Souza"}, contact_profile_name="Maria Silva")
    )

    assert result["known_facts"]["name"] == "Ana Souza"
    assert result["fact_sources"]["name"] == "lead_message"


def test_invalid_existing_name_type_is_removed_from_facts() -> None:
    result = update_qualification(
        {
            **_turn(),
            "known_facts": {"name": 123},
            "fact_sources": {"name": "lead_message"},
        }
    )

    assert "name" not in result["known_facts"]
    assert "name" not in result["fact_sources"]
    assert result["pending_goal"] == "name"


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


def test_assignment_freezes_qualification_and_skips_specialist_routing() -> None:
    prior_facts = {
        "name": "Lucas",
        "document": "12345678901",
        "activity": "revendedor",
        "store_name": "Cardoso Rações",
        "monthly_volume": "R$ 15.000",
    }
    state = {
        **_turn(updates={"monthly_volume": "R$ 20.000"}),
        "known_facts": prior_facts,
        "fact_sources": {field: "lead_message" for field in prior_facts},
        "supervisor_assignment": {"supervisor_id": 7},
        "handoff_delivery_status": "delivered",
        "requires_specialist": True,
        "specialist_name": "test_specialist",
    }

    result = update_qualification(state)

    assert route_after_interpretation(state) == "update-qualification"
    assert result["known_facts"] == prior_facts
    assert result["pending_goal"] is None
    assert result["supervisor_handoff_request"] is None
    assert result["conversation_stage"] == "handoff_ready"
    assert result["status"] == "supervisor_handoff_delivered"


def test_works_with_nutrition_schema_documents_clear_store_name_inference() -> None:
    for schema in (QualificationFactUpdates, OpenAIQualificationFactUpdates):
        description = schema.model_fields["works_with_nutrition"].description
        assert description is not None
        assert "Cardoso Rações" in description
        assert "Mundo Animal" in description
        assert "explicit lead correction takes precedence" in description


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
