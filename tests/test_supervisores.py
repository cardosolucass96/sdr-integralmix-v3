from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.application.supervisores import (
    InMemoryIntegralMixSupervisorStore,
    IntegralMixSupervisorService,
    NoEligibleSupervisorError,
    Supervisor,
    SupervisorFields,
    SupervisorRoutingCategory,
    normalize_brazilian_state,
    select_supervisor_for_lead,
    states_in_supervisor_region,
    supervisor_display_phone,
    uncovered_brazilian_states,
)


def _supervisor(
    supervisor_id: int,
    *,
    region: str,
    category: str,
    name: str | None = None,
    leads: int = 0,
    active: bool = True,
    last_assigned_at: datetime | None = None,
) -> Supervisor:
    return Supervisor(
        supervisor_id=supervisor_id,
        nocodb_record_id=supervisor_id,
        name=name or f"Supervisor {supervisor_id}",
        phone=None,
        category=category,
        region=region,
        leads_received=leads,
        is_active=active,
        minimum_order=None,
        last_assigned_at=last_assigned_at,
    )


def test_state_and_region_normalization_supports_full_names_and_regions() -> None:
    assert normalize_brazilian_state(" Ceará ") == "CE"
    assert normalize_brazilian_state("SP") == "SP"
    assert normalize_brazilian_state("Atlantico") is None
    assert states_in_supervisor_region("CE/PI/MA/PA/MA/TO") == frozenset(
        {"CE", "PI", "MA", "PA", "TO"}
    )
    assert "RN" in states_in_supervisor_region("Norte e Nordeste")
    assert "SP" in states_in_supervisor_region("SP Oeste")
    assert "CE" in states_in_supervisor_region("Ceará")


def test_uncovered_states_ignores_inactive_supervisors_and_reports_missing_coverage() -> None:
    uncovered = uncovered_brazilian_states(
        [
            _supervisor(1, region="CE", category="AGRO"),
            _supervisor(2, region="RS", category="AGRO", active=False),
            _supervisor(3, region="Norte e Nordeste", category="AQUAMIX"),
        ]
    )

    assert "CE" not in uncovered
    assert "AC" not in uncovered
    assert "RS" in uncovered
    assert "MS" in uncovered


def test_exact_region_and_category_match_wins_before_lower_load_fallback() -> None:
    supervisors = [
        _supervisor(1, region="CE/RN", category="AGROPECUARIAS", leads=1),
        _supervisor(2, region="CE", category="AGRO,CRIADORES DE EQUINO", leads=20),
        _supervisor(3, region="RN", category="AGRO", leads=0),
    ]

    selected = select_supervisor_for_lead(
        supervisors,
        "Ceará",
        (SupervisorRoutingCategory.AGRO,),
    )

    assert selected.supervisor_id == 2
    assert selected.match_mode == "region_and_category"
    assert selected.lead_state == "CE"


def test_region_only_fallback_uses_least_loaded_and_ignores_inactive_records() -> None:
    supervisors = [
        _supervisor(1, region="CE/RN", category="AGROPECUARIAS", leads=10),
        _supervisor(2, region="CE", category="PETSHOP", leads=3),
        _supervisor(3, region="CE", category="AGRO", leads=0, active=False),
    ]

    selected = select_supervisor_for_lead(
        supervisors,
        "CE",
        (SupervisorRoutingCategory.AQUAMIX,),
    )

    assert selected.supervisor_id == 2
    assert selected.match_mode == "region_only"


def test_no_supervisor_outside_the_leads_region_is_selected() -> None:
    supervisors = [_supervisor(1, region="CE/RN", category="AGRO", leads=0)]

    with pytest.raises(NoEligibleSupervisorError, match="covers state BA"):
        select_supervisor_for_lead(
            supervisors,
            "BA",
            (SupervisorRoutingCategory.AGRO,),
        )


def test_invalid_state_and_unrecognized_category_fail_closed_or_use_regional_fallback() -> None:
    supervisor = _supervisor(1, region="CE", category="OUTRA CATEGORIA")

    with pytest.raises(NoEligibleSupervisorError, match="state code"):
        select_supervisor_for_lead(
            [supervisor],
            "Atlântico",
            (SupervisorRoutingCategory.AGRO,),
        )

    selected = select_supervisor_for_lead(
        [supervisor],
        "CE",
        (SupervisorRoutingCategory.AGRO,),
    )
    assert selected.match_mode == "region_only"


def test_service_validates_request_key_and_accepts_category_enum() -> None:
    service = IntegralMixSupervisorService(InMemoryIntegralMixSupervisorStore())
    service.create_supervisor(
        SupervisorFields(
            name="Ana",
            phone=None,
            category="AGRO",
            region="CE",
            leads_received=0,
            is_active=True,
            minimum_order=None,
        )
    )

    for request_key in ("   ", "x" * 201):
        with pytest.raises(ValueError, match="request_key"):
            service.assign_supervisor(
                request_key=request_key,
                lead_state="CE",
                route_categories=[SupervisorRoutingCategory.AGRO],
            )

    assignment = service.assign_supervisor(
        request_key=" enum-category ",
        lead_state="CE",
        route_categories=[SupervisorRoutingCategory.AGRO],
    )
    assert assignment.lead_state == "CE"


def test_in_memory_supervisor_updates_and_activation_handle_existing_and_missing_ids() -> None:
    store = InMemoryIntegralMixSupervisorStore()
    service = IntegralMixSupervisorService(store)

    assert (
        service.update_supervisor(90, SupervisorFields("A", None, "AGRO", "CE", 0, True, None))
        is None
    )
    assert service.set_supervisor_active(90, is_active=True) is None

    created = service.create_supervisor(SupervisorFields("Ana", None, "AGRO", "CE", 0, False, None))
    activated = service.set_supervisor_active(created.supervisor_id, is_active=True)

    assert activated is not None and activated.is_active is True


def test_ties_rotate_to_the_least_recently_assigned_supervisor() -> None:
    now = datetime.now(UTC)
    selected = select_supervisor_for_lead(
        [
            _supervisor(
                1,
                region="CE",
                category="AGRO",
                leads=7,
                last_assigned_at=now,
            ),
            _supervisor(
                2,
                region="CE",
                category="AGRO",
                leads=7,
                last_assigned_at=now - timedelta(minutes=10),
            ),
        ],
        "CE",
        (SupervisorRoutingCategory.AGRO,),
    )

    assert selected.supervisor_id == 2


def test_display_phone_uses_legacy_override_only_for_christian_bezerra() -> None:
    assert (
        supervisor_display_phone(
            _supervisor(1, region="CE", category="AGRO", name="Christian Bezerra")
        )
        == "99 91503634"
    )
    assert (
        supervisor_display_phone(_supervisor(2, region="CE", category="AGRO", name="Ana")) is None
    )


def test_assignment_key_is_idempotent_and_increments_the_counter_once() -> None:
    service = IntegralMixSupervisorService(InMemoryIntegralMixSupervisorStore())
    service.create_supervisor(
        SupervisorFields(
            name="Ana",
            phone="+55 85 90000-0000",
            category="AGRO",
            region="CE",
            leads_received=2,
            is_active=True,
            minimum_order=None,
        )
    )

    first = service.assign_supervisor(
        request_key="conversation-1",
        lead_state="CE",
        route_categories=["AGRO"],
    )
    repeated = service.assign_supervisor(
        request_key="conversation-1",
        lead_state="CE",
        route_categories=["AGRO"],
    )

    assert first.supervisor.supervisor_id == repeated.supervisor.supervisor_id
    assert repeated.already_assigned is True
    assert service.list_supervisors()[0].leads_received == 3


def test_in_memory_handoff_claims_validate_limit_and_ignore_unknown_operations() -> None:
    service = IntegralMixSupervisorService(InMemoryIntegralMixSupervisorStore())
    service.create_supervisor(
        SupervisorFields("Ana", "+5585900000000", "AGRO", "CE", 0, True, None)
    )
    service.assign_supervisor(
        request_key="pipefacil-deal-1",
        lead_state="CE",
        route_categories=["AGRO"],
        deal_seq=1,
        handoff_payload={"profile": {"name": "Maria"}},
    )

    assert service.claim_pending_handoffs(limit=0) == []
    assert service.claim_pending_handoffs(request_key="missing", limit=1) == []
    assert service.claim_pending_handoffs(limit=1)[0].request_key == "pipefacil-deal-1"
    service.mark_handoff_step(
        request_key="missing",
        step="crm_synced",
        receipt={"status_code": 200},
    )
    service.record_handoff_failure(request_key="missing", error_code="not_found")


def test_get_handoff_operation_reads_a_copy_without_claiming() -> None:
    service = IntegralMixSupervisorService(InMemoryIntegralMixSupervisorStore())
    service.create_supervisor(
        SupervisorFields("Ana", "+5585900000000", "AGRO", "CE", 0, True, None)
    )
    service.assign_supervisor(
        request_key="pipefacil-deal-1",
        lead_state="CE",
        route_categories=["AGRO"],
        deal_seq=1,
        handoff_payload={"profile": {"name": "Maria"}},
    )

    operation = service.get_handoff_operation(request_key="pipefacil-deal-1")

    assert operation is not None
    assert operation.request_key == "pipefacil-deal-1"
    assert service.claim_pending_handoffs(request_key="pipefacil-deal-1", limit=1)[0].attempts == 1
