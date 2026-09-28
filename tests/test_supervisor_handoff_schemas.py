from __future__ import annotations

import pytest

from app.agent.chains.schemas import (
    SupervisorHandoffProposal,
    _is_complete_supervisor_handoff,
)


def _proposal(**changes: object) -> SupervisorHandoffProposal:
    values: dict[str, object] = {
        "handoff_ready": True,
        "name": "Maria",
        "document": "12345678901",
        "activity": "criador",
        "city": "Fortaleza",
        "state": "CE",
        "species": "Peixes",
        "frequency": "Mensal",
        "consumption": "300 kg por mês",
        "store_name": None,
        "works_with_nutrition": None,
        "product_category": None,
        "monthly_volume": None,
        "current_brands": None,
        "routing_segment": "aquaculture_creator",
    }
    values.update(changes)
    return SupervisorHandoffProposal.model_validate(values)


def test_handoff_proposal_requires_readiness_and_all_common_fields() -> None:
    assert _proposal(handoff_ready=False).to_handoff_payload() is None
    assert _proposal(document=" ").to_handoff_payload() is None
    payload = _proposal().to_handoff_payload()
    assert payload is not None
    assert payload["routing_segment"] == "aquaculture_creator"
    assert payload["route_categories"] == ["AQUAMIX"]


@pytest.mark.parametrize(
    ("routing_segment", "expected_category"),
    [
        ("aquaculture_creator", "AQUAMIX"),
        ("ruminant_creator", "PECUARIAS"),
        ("other_livestock_creator", "AGRO"),
        ("petshop_retail", "PETSHOP"),
        ("pet_food_grocery", "CANAL_PET_ALIMENTAR"),
        ("agro_reseller", "AGROPECUARIAS"),
        ("unknown", None),
    ],
)
def test_handoff_routing_segment_maps_to_a_validated_supervisor_category(
    routing_segment: str,
    expected_category: str | None,
) -> None:
    payload = _proposal(routing_segment=routing_segment).to_handoff_payload()
    assert payload is not None
    assert payload["route_categories"] == ([expected_category] if expected_category else [])


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"species": None}, False),
        ({"frequency": None}, False),
        ({"consumption": None}, False),
        (
            {
                "activity": "revendedor",
                "store_name": "Loja",
                "works_with_nutrition": "nao",
                "product_category": "Pet",
                "monthly_volume": "20 sacos",
            },
            True,
        ),
        (
            {
                "activity": "revendedor",
                "store_name": "Loja",
                "works_with_nutrition": "sim",
                "product_category": "Pet",
                "monthly_volume": "20 sacos",
            },
            False,
        ),
        (
            {
                "activity": "revendedor",
                "store_name": "Loja",
                "works_with_nutrition": "sim",
                "product_category": "Pet",
                "monthly_volume": "20 sacos",
                "current_brands": "Marca",
            },
            True,
        ),
    ],
)
def test_handoff_proposal_checks_conditional_qualification_fields(
    changes: dict[str, object],
    expected: bool,
) -> None:
    proposal = _proposal(**changes)
    assert (proposal.to_handoff_payload() is not None) is expected


def test_handoff_payload_validation_rejects_unknown_activity_and_missing_common_data() -> None:
    assert (
        _is_complete_supervisor_handoff(
            {
                "name": "Maria",
                "document": "123",
                "activity": "outro",
                "city": "Fortaleza",
                "state": "CE",
            }
        )
        is False
    )
    assert _is_complete_supervisor_handoff({"name": "Maria"}) is False


def test_handoff_proposal_does_not_fill_missing_qualification_facts() -> None:
    assert _proposal(species=None).to_handoff_payload() is None
