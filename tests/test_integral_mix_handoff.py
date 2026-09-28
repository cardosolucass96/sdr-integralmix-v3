from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

import app.application.integral_mix_handoff as handoff
from app.application.supervisores import (
    InMemoryIntegralMixSupervisorStore,
    IntegralMixSupervisorService,
)
from app.core.config import Settings
from app.core.integral_mix_supervisors import (
    NoEligibleSupervisorError,
    SupervisorFields,
)
from app.integrations.pipefacil import (
    PipefacilDealLookupError,
    PipefacilDealUpdateError,
    PipefacilDealUpdateResult,
    PipefacilSendMessageResult,
)


def _service_and_operation() -> tuple[IntegralMixSupervisorService, Any]:
    service = IntegralMixSupervisorService(InMemoryIntegralMixSupervisorStore())
    service.create_supervisor(
        SupervisorFields(
            name="Ana Supervisor",
            phone="+5585900000000",
            category="AGRO",
            region="CE",
            leads_received=1,
            is_active=True,
            minimum_order=None,
        )
    )
    request = {
        "name": "Maria",
        "document": "12345678901",
        "activity": "criador",
        "city": "Fortaleza",
        "state": "Ceará",
        "species": "Peixes",
        "frequency": "Mensal",
        "consumption": "300 kg por mês",
        "store_name": None,
        "works_with_nutrition": None,
        "product_category": None,
        "monthly_volume": None,
        "current_brands": None,
        "route_categories": ["AGRO"],
    }
    service.assign_supervisor(
        request_key="pipefacil-deal-77",
        lead_state="CE",
        route_categories=["AGRO"],
        deal_seq=77,
        handoff_payload={
            "profile": request,
            "lead_state": "CE",
            "lead_phone": "+5585999999999",
            "channel_id": "channel-1",
            "sender_phone_number_id": "sender-1",
        },
    )
    operation = service.claim_pending_handoffs(
        request_key="pipefacil-deal-77",
        limit=1,
    )[0]
    return service, operation


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        pipefacil_api_key="test-key",
        pipefacil_base_url="https://api.pipefacil.test",
    )


@pytest.fixture(autouse=True)
def _stub_pipefacil_stage_lookups(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        handoff,
        "fetch_deal_by_seq",
        lambda **_: {"pipelineId": "pipeline-1", "stageId": "stage-current", "notes": None},
    )
    monkeypatch.setattr(
        handoff,
        "fetch_pipefacil_pipelines",
        lambda **_: [
            {
                "id": "pipeline-1",
                "name": "Novos Leads",
                "stages": [
                    {
                        "id": "stage-current",
                        "name": "Atendimento IA",
                        "order": 1,
                        "isWon": False,
                        "isLost": False,
                    },
                    {
                        "id": "stage-handoff",
                        "name": "Enviado para o comercial",
                        "order": 3,
                        "isWon": False,
                        "isLost": False,
                    },
                ],
            }
        ],
    )


def test_handoff_rejects_an_invalid_brazilian_state_before_assignment() -> None:
    service = IntegralMixSupervisorService(InMemoryIntegralMixSupervisorStore())

    with pytest.raises(NoEligibleSupervisorError, match="Brazilian state"):
        handoff.execute_integral_mix_supervisor_handoff(
            service=service,
            request={"state": "Atlantico"},
            deal_seq=77,
            lead_phone=None,
            channel_id="channel-1",
            sender_phone_number_id="sender-1",
            settings=_settings(),
        )


def test_handoff_returns_pending_when_another_worker_holds_the_outbox_lease() -> None:
    service, _ = _service_and_operation()
    assignment_callbacks: list[int] = []

    result = handoff.execute_integral_mix_supervisor_handoff(
        service=service,
        request={"state": "CE", "route_categories": []},
        deal_seq=77,
        lead_phone=None,
        channel_id="channel-1",
        sender_phone_number_id="sender-1",
        settings=_settings(),
        on_assignment=lambda assignment: assignment_callbacks.append(
            assignment.supervisor.supervisor_id
        ),
    )

    assert result.delivery_status == "pending"
    assert result.assignment.already_assigned is True
    assert assignment_callbacks == [result.assignment.supervisor.supervisor_id]


def test_replayed_handoff_reports_delivered_without_resending_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, operation = _service_and_operation()
    outbound: list[str] = []
    monkeypatch.setattr(
        handoff,
        "update_deal_properties",
        lambda **_: PipefacilDealUpdateResult(200, "crm-request", {"data": {"seq": 77}}),
    )

    def send_message(**kwargs):
        outbound.append(kwargs["to"])
        return PipefacilSendMessageResult(
            201,
            f"message-request-{len(outbound)}",
            {"data": {"id": f"message-{len(outbound)}"}},
        )

    monkeypatch.setattr(handoff, "send_public_text_message", send_message)
    assert handoff.process_supervisor_handoff_operation(service, operation, settings=_settings())

    replay = handoff.execute_integral_mix_supervisor_handoff(
        service=service,
        request={"state": "CE", "route_categories": ["AGRO"]},
        deal_seq=77,
        lead_phone="+5585999999999",
        channel_id="channel-1",
        sender_phone_number_id="sender-1",
        settings=_settings(),
    )

    assert replay.delivery_status == "delivered"
    assert replay.assignment.already_assigned is True
    assert outbound == ["+5585900000000", "+5585999999999"]


def test_pending_handoff_batch_returns_zero_when_the_outbox_is_empty() -> None:
    service = IntegralMixSupervisorService(InMemoryIntegralMixSupervisorStore())

    assert handoff.process_pending_integral_mix_handoffs(service, settings=_settings()) == 0


def test_handoff_failure_is_recorded_then_retried_from_the_unsynced_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, operation = _service_and_operation()
    crm_calls: list[int] = []
    message_calls: list[str] = []

    def fail_crm(**kwargs):
        crm_calls.append(kwargs["seq"])
        raise PipefacilDealUpdateError(
            "Temporary CRM failure",
            error_code="pipefacil_upstream_error",
            status_code=503,
            request_id="crm-request-1",
        )

    monkeypatch.setattr(handoff, "update_deal_properties", fail_crm)
    assert (
        handoff.process_supervisor_handoff_operation(
            service,
            operation,
            settings=_settings(),
        )
        is False
    )

    retry_operation = service.claim_pending_handoffs(
        request_key=operation.request_key,
        limit=1,
    )[0]
    assert retry_operation.last_error_code == "pipefacil_upstream_error"

    def sync_crm(**kwargs):
        crm_calls.append(kwargs["seq"])
        return PipefacilDealUpdateResult(200, "crm-request-2", {"data": {"seq": 77}})

    def send_message(**kwargs):
        message_calls.append(kwargs["to"])
        return PipefacilSendMessageResult(
            201,
            f"message-request-{len(message_calls)}",
            {"data": {"id": f"message-{len(message_calls)}"}},
        )

    monkeypatch.setattr(handoff, "update_deal_properties", sync_crm)
    monkeypatch.setattr(handoff, "send_public_text_message", send_message)
    assert (
        handoff.process_supervisor_handoff_operation(
            service,
            retry_operation,
            settings=_settings(),
        )
        is True
    )
    assert crm_calls == [77, 77]
    assert message_calls == ["+5585900000000", "+5585999999999"]
    assert service.claim_pending_handoffs(request_key=operation.request_key, limit=1) == []


def test_handoff_records_unexpected_dispatch_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    service, operation = _service_and_operation()
    monkeypatch.setattr(
        handoff,
        "update_deal_properties",
        lambda **_: (_ for _ in ()).throw(RuntimeError("unexpected response shape")),
    )

    assert (
        handoff.process_supervisor_handoff_operation(
            service,
            operation,
            settings=_settings(),
        )
        is False
    )
    retry = service.claim_pending_handoffs(request_key=operation.request_key, limit=1)[0]
    assert retry.last_error_code == "unexpected_handoff_error"


def test_handoff_skips_completed_steps_and_only_notifies_the_lead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, operation = _service_and_operation()
    service.mark_handoff_step(
        request_key=operation.request_key,
        step="crm_synced",
        receipt={"status_code": 200},
    )
    service.mark_handoff_step(
        request_key=operation.request_key,
        step="supervisor_notified",
        receipt={"message_id": "already-sent"},
    )
    completed_prefix = replace(operation, crm_synced=True, supervisor_notified=True)
    outbound: list[dict[str, Any]] = []
    monkeypatch.setattr(
        handoff,
        "update_deal_properties",
        lambda **_: pytest.fail("CRM sync is already complete"),
    )
    monkeypatch.setattr(
        handoff,
        "send_public_text_message",
        lambda **kwargs: (
            outbound.append(kwargs)
            or PipefacilSendMessageResult(201, "lead-message-request", {"data": {"id": "m3"}})
        ),
    )

    assert (
        handoff.process_supervisor_handoff_operation(
            service,
            completed_prefix,
            settings=_settings(),
        )
        is True
    )
    assert len(outbound) == 1
    assert outbound[0]["to"] == "+5585999999999"


def test_sync_maps_only_present_qualification_fields_and_receipt_helpers() -> None:
    service, operation = _service_and_operation()
    del service
    minimal_payload = {
        **operation.lead_payload,
        "profile": {"city": "Fortaleza", "activity": "criador"},
    }
    minimal_operation = replace(operation, lead_payload=minimal_payload)
    updates: list[dict[str, Any]] = []

    def update(**kwargs):
        updates.append(kwargs)
        return PipefacilDealUpdateResult(200, None, None)

    original = handoff.update_deal_properties
    handoff.update_deal_properties = update
    try:
        receipt = handoff._sync_pipefacil_handoff(minimal_operation, settings=_settings())
    finally:
        handoff.update_deal_properties = original

    assert updates[0]["properties"] == {
        "cidade": "Fortaleza",
        "estado": "CE",
        "area_atuacao": "criador",
        "supervisor": "Ana Supervisor",
    }
    assert updates[0]["stage_id"] == "stage-handoff"
    assert updates[0]["notes"] is None
    assert receipt == {
        "status_code": 200,
        "request_id": None,
        "stage_id": "stage-handoff",
        "stage_name": "Enviado para o comercial",
    }
    assert handoff._message_receipt(200, "r1", None) == {
        "status_code": 200,
        "request_id": "r1",
        "message_id": None,
    }


@pytest.mark.parametrize(
    ("works_with_nutrition", "current_brands", "expected_notes"),
    [
        (
            "sim",
            "Marca de teste",
            "Nota anterior.\n[Integral Mix] Trabalha com nutrição animal: Sim",
        ),
        (
            "nao",
            "Não trabalha atualmente com nutrição animal",
            "Nota anterior.\n[Integral Mix] Trabalha com nutrição animal: Não",
        ),
    ],
)
def test_sync_maps_complete_reseller_fields_and_nutrition_answer(
    monkeypatch: pytest.MonkeyPatch,
    works_with_nutrition: str,
    current_brands: str,
    expected_notes: str,
) -> None:
    _, operation = _service_and_operation()
    profile = {
        **operation.lead_payload["profile"],
        "activity": "revendedor",
        "store_name": "Agropecuária de teste",
        "species": None,
        "frequency": None,
        "consumption": None,
        "works_with_nutrition": works_with_nutrition,
        "product_category": "Ração para bovinos",
        "monthly_volume": "R$ 3.000 por mês",
        "current_brands": current_brands,
        "route_categories": ["AGROPECUARIAS"],
    }
    reseller_operation = replace(
        operation,
        lead_payload={**operation.lead_payload, "profile": profile},
    )
    updates: list[dict[str, Any]] = []
    monkeypatch.setattr(
        handoff,
        "fetch_deal_by_seq",
        lambda **_: {"pipelineId": "pipeline-1", "notes": "Nota anterior."},
    )
    monkeypatch.setattr(
        handoff,
        "update_deal_properties",
        lambda **kwargs: (
            updates.append(kwargs) or PipefacilDealUpdateResult(200, "crm-request", None)
        ),
    )

    receipt = handoff._sync_pipefacil_handoff(reseller_operation, settings=_settings())

    assert updates[0]["properties"] == {
        "cidade": "Fortaleza",
        "estado": "CE",
        "area_atuacao": "revendedor",
        "supervisor": "Ana Supervisor",
        "documento": "12345678901",
        "marcas_atuais": current_brands,
        "produtos": "Ração para bovinos",
        "valor_de_compra": "R$ 3.000 por mês",
        "loja": "Agropecuária de teste",
    }
    assert updates[0]["stage_id"] == "stage-handoff"
    assert updates[0]["notes"] == expected_notes
    assert receipt["stage_name"] == "Enviado para o comercial"


def test_resolve_handoff_stage_uses_only_the_deals_current_pipeline() -> None:
    target = handoff._resolve_handoff_stage(
        {"pipelineId": "pipe-novos", "stageId": "stage-ai"},
        [
            {
                "id": "pipe-novos",
                "stages": [
                    {"id": "stage-ai", "name": "Atendimento IA", "order": 1},
                    {"id": "stage-target", "name": "Enviado para o comercial", "order": 3},
                    {"id": "stage-lost", "name": "Perda", "order": 4, "isLost": True},
                ],
            },
            {
                "id": "pipe-other",
                "stages": [{"id": "wrong-pipeline-target", "name": "Enviado para o comercial"}],
            },
        ],
    )

    assert target == {"id": "stage-target", "name": "Enviado para o comercial"}


def test_resolve_handoff_stage_fails_closed_when_pipeline_has_no_handoff_stage() -> None:
    with pytest.raises(PipefacilDealLookupError) as exc_info:
        handoff._resolve_handoff_stage(
            {"pipelineId": "pipe-1"},
            [{"id": "pipe-1", "stages": [{"id": "qualification", "name": "Qualificação"}]}],
        )

    assert exc_info.value.error_code == "pipefacil_handoff_stage_not_found"


def test_merge_nutrition_note_is_idempotent_and_preserves_existing_notes() -> None:
    first = handoff._merge_nutrition_note(
        "Observação do comercial.",
        works_with_nutrition="sim",
    )
    second = handoff._merge_nutrition_note(
        first,
        works_with_nutrition="nao",
    )

    assert first == ("Observação do comercial.\n[Integral Mix] Trabalha com nutrição animal: Sim")
    assert second == ("Observação do comercial.\n[Integral Mix] Trabalha com nutrição animal: Não")
    assert handoff._merge_nutrition_note(second, works_with_nutrition=None) == second


def test_merge_nutrition_note_rejects_notes_that_exceed_pipefacil_limit() -> None:
    with pytest.raises(PipefacilDealUpdateError) as exc_info:
        handoff._merge_nutrition_note("x" * 2000, works_with_nutrition="sim")

    assert exc_info.value.error_code == "pipefacil_deal_notes_too_long"
