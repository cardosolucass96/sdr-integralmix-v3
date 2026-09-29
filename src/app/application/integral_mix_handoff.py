from __future__ import annotations

import logging
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.application.supervisores import IntegralMixSupervisorService, normalize_brazilian_state
from app.core.config import Settings
from app.core.integral_mix_supervisors import (
    NoEligibleSupervisorError,
    SupervisorAssignment,
    SupervisorHandoffOperation,
    SupervisorHandoffStep,
)
from app.integrations.pipefacil import (
    PipefacilDealLookupError,
    PipefacilDealUpdateError,
    PipefacilSendMessageError,
    fetch_deal_by_seq,
    fetch_pipefacil_pipelines,
    send_public_text_message,
    update_deal_properties,
)

LOGGER = logging.getLogger(__name__)
HANDOFF_STAGE_NAMES = frozenset(
    {
        "enviado para o comercial",
        "enviados para o comercial",
        "encaminhado para o comercial",
        "encaminhados para o comercial",
        "encaminhado para supervisor",
        "encaminhados para supervisor",
        "encaminhado para o supervisor",
        "encaminhados para o supervisor",
        "encaminhado p assessor",
    }
)
NUTRITION_NOTE_MARKER = "[Integral Mix] Trabalha com nutrição animal:"


@dataclass(frozen=True, slots=True)
class IntegralMixHandoffResult:
    assignment: SupervisorAssignment
    delivery_status: str
    lead_message: str | None = None


def supervisor_handoff_request_key(deal_seq: int) -> str:
    return f"pipefacil-deal-{deal_seq}"


def execute_integral_mix_supervisor_handoff(
    *,
    service: IntegralMixSupervisorService,
    request: dict[str, Any],
    deal_seq: int,
    lead_phone: str | None,
    channel_id: str,
    sender_phone_number_id: str,
    settings: Settings,
    on_assignment: Callable[[SupervisorAssignment], None] | None = None,
) -> IntegralMixHandoffResult:
    normalized_state = normalize_brazilian_state(str(request.get("state") or ""))
    if normalized_state is None:
        raise NoEligibleSupervisorError("Lead state is not a Brazilian state or state code.")
    request_key = supervisor_handoff_request_key(deal_seq)
    handoff_payload = {
        "profile": dict(request),
        "lead_state": normalized_state,
        "lead_phone": (lead_phone or "").strip(),
        "channel_id": channel_id,
        "sender_phone_number_id": sender_phone_number_id,
    }
    assignment = service.assign_supervisor(
        request_key=request_key,
        lead_state=normalized_state,
        route_categories=request.get("route_categories", []),
        deal_seq=deal_seq,
        handoff_payload=handoff_payload,
    )
    if on_assignment is not None:
        on_assignment(assignment)
    operations = service.claim_pending_handoffs(request_key=request_key, limit=1)
    if not operations:
        existing_operation = service.get_handoff_operation(request_key=request_key)
        is_delivered = bool(
            existing_operation
            and existing_operation.crm_synced
            and existing_operation.supervisor_notified
            and existing_operation.lead_notified
        )
        return IntegralMixHandoffResult(
            assignment,
            "delivered" if is_delivered else "pending",
        )
    completed = process_supervisor_handoff_operation(
        service,
        operations[0],
        settings=settings,
    )
    return IntegralMixHandoffResult(
        assignment,
        "delivered" if completed else "pending",
        _lead_handoff_message(operations[0]) if completed else None,
    )


def process_pending_integral_mix_handoffs(
    service: IntegralMixSupervisorService,
    *,
    settings: Settings,
    limit: int = 20,
) -> int:
    operations = service.claim_pending_handoffs(limit=limit)
    return sum(
        process_supervisor_handoff_operation(service, operation, settings=settings)
        for operation in operations
    )


def process_supervisor_handoff_operation(
    service: IntegralMixSupervisorService,
    operation: SupervisorHandoffOperation,
    *,
    settings: Settings,
) -> bool:
    try:
        if not operation.crm_synced:
            receipt = _sync_pipefacil_handoff(operation, settings=settings)
            _mark_step(service, operation.request_key, "crm_synced", receipt)
        if not operation.supervisor_notified:
            receipt = _notify_supervisor(operation, settings=settings)
            _mark_step(service, operation.request_key, "supervisor_notified", receipt)
        if not operation.lead_notified:
            receipt = _notify_lead(operation, settings=settings)
            _mark_step(service, operation.request_key, "lead_notified", receipt)
    except (
        PipefacilDealLookupError,
        PipefacilDealUpdateError,
        PipefacilSendMessageError,
    ) as exc:
        service.record_handoff_failure(
            request_key=operation.request_key,
            error_code=exc.error_code,
        )
        LOGGER.warning(
            "integral_mix.supervisor_handoff_retry_scheduled",
            extra={
                "pipeline_step": "integral_mix.supervisor_handoff_retry_scheduled",
                "request_key": operation.request_key,
                "error_code": exc.error_code,
                "status_code": exc.status_code,
                "request_id": exc.request_id,
            },
        )
        return False
    except Exception:
        service.record_handoff_failure(
            request_key=operation.request_key,
            error_code="unexpected_handoff_error",
        )
        LOGGER.exception(
            "integral_mix.supervisor_handoff_failed",
            extra={
                "pipeline_step": "integral_mix.supervisor_handoff_failed",
                "request_key": operation.request_key,
            },
        )
        return False
    return True


def _sync_pipefacil_handoff(
    operation: SupervisorHandoffOperation,
    *,
    settings: Settings,
) -> dict[str, Any]:
    profile = operation.lead_payload["profile"]
    deal = fetch_deal_by_seq(seq=operation.deal_seq, settings=settings)
    pipelines = fetch_pipefacil_pipelines(settings=settings)
    target_stage = _resolve_handoff_stage(deal, pipelines)
    properties: dict[str, Any] = {
        "cidade": profile["city"],
        "estado": operation.lead_payload["lead_state"],
        "area_atuacao": profile["activity"],
        "supervisor": operation.supervisor.name,
    }
    optional_fields = {
        "documento": profile.get("document"),
        "marcas_atuais": profile.get("current_brands"),
        "produtos": profile.get("product_category"),
        "valor_de_compra": profile.get("monthly_volume"),
        "especies": profile.get("species"),
        "consumo": profile.get("consumption"),
        "loja": profile.get("store_name"),
    }
    properties.update(
        {
            field: value
            for field, value in optional_fields.items()
            if value is not None and str(value).strip()
        }
    )
    frequency = profile.get("frequency")
    if frequency:
        properties["frequencia_de_compra"] = [frequency]
    works_with_nutrition = profile.get("works_with_nutrition")

    notes = _merge_nutrition_note(
        deal.get("notes"),
        works_with_nutrition=works_with_nutrition,
    )
    result = update_deal_properties(
        seq=operation.deal_seq,
        properties=properties,
        stage_id=target_stage["id"],
        notes=notes,
        settings=settings,
    )
    return {
        "status_code": result.status_code,
        "request_id": result.request_id,
        "stage_id": target_stage["id"],
        "stage_name": target_stage["name"],
    }


def _resolve_handoff_stage(
    deal: dict[str, Any],
    pipelines: list[dict[str, Any]],
) -> dict[str, str]:
    pipeline = _pipeline_for_deal(deal, pipelines)
    stages = _pipeline_stages(pipeline)
    candidates = [stage for stage in stages if _is_handoff_stage_candidate(stage)]
    if not candidates:
        raise PipefacilDealLookupError(
            "Pipefacil pipeline has no recognized commercial handoff stage.",
            error_code="pipefacil_handoff_stage_not_found",
        )

    target = min(
        enumerate(candidates),
        key=lambda item: (
            _stage_order(item[1].get("order"), item[0]),
            str(item[1].get("id") or ""),
        ),
    )[1]
    return {"id": str(target["id"]).strip(), "name": str(target["name"]).strip()}


def _pipeline_for_deal(
    deal: dict[str, Any],
    pipelines: list[dict[str, Any]],
) -> dict[str, Any]:
    pipeline_id = str(deal.get("pipelineId") or "").strip()
    if not pipeline_id:
        raise PipefacilDealLookupError(
            "Pipefacil deal does not identify its pipeline.",
            error_code="pipefacil_deal_pipeline_missing",
        )

    pipeline = next(
        (item for item in pipelines if str(item.get("id") or "").strip() == pipeline_id),
        None,
    )
    if pipeline is None:
        raise PipefacilDealLookupError(
            "Pipefacil deal pipeline was not returned by the pipelines API.",
            error_code="pipefacil_deal_pipeline_not_found",
        )
    return pipeline


def _pipeline_stages(pipeline: dict[str, Any]) -> list[Any]:
    stages = pipeline.get("stages")
    if not isinstance(stages, list):
        raise PipefacilDealLookupError(
            "Pipefacil pipeline has no valid stage list.",
            error_code="pipefacil_pipeline_stages_invalid",
        )
    return stages


def _is_handoff_stage_candidate(stage: Any) -> bool:
    if not isinstance(stage, dict):
        return False
    return (
        _normalize_stage_name(str(stage.get("name") or "")) in HANDOFF_STAGE_NAMES
        and not stage.get("isWon")
        and not stage.get("isLost")
        and bool(str(stage.get("id") or "").strip())
    )


def _normalize_stage_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    without_marks = "".join(char for char in normalized if not unicodedata.combining(char))
    words = "".join(char if char.isalnum() else " " for char in without_marks).split()
    return " ".join(words)


def _stage_order(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _merge_nutrition_note(existing_notes: Any, *, works_with_nutrition: Any) -> str | None:
    if works_with_nutrition not in {"sim", "nao"}:
        return str(existing_notes).strip() if existing_notes else None

    existing = str(existing_notes or "").strip()
    nutrition_line = f"{NUTRITION_NOTE_MARKER} {'Sim' if works_with_nutrition == 'sim' else 'Não'}"
    lines = [line for line in existing.splitlines() if not line.startswith(NUTRITION_NOTE_MARKER)]
    lines.append(nutrition_line)
    merged = "\n".join(lines).strip()
    if len(merged) > 2000:
        raise PipefacilDealUpdateError(
            "Existing Pipefacil notes leave no room for the nutrition qualification value.",
            error_code="pipefacil_deal_notes_too_long",
        )
    return merged


def _notify_supervisor(
    operation: SupervisorHandoffOperation,
    *,
    settings: Settings,
) -> dict[str, Any]:
    payload = operation.lead_payload
    profile = payload["profile"]
    text = _supervisor_message(profile, lead_phone=payload["lead_phone"])
    result = send_public_text_message(
        to=operation.supervisor.phone,
        text=text,
        channel_id=payload["channel_id"],
        sender_phone_number_id=payload["sender_phone_number_id"],
        settings=settings,
    )
    return _message_receipt(result.status_code, result.request_id, result.payload)


def _notify_lead(
    operation: SupervisorHandoffOperation,
    *,
    settings: Settings,
) -> dict[str, Any]:
    payload = operation.lead_payload
    message = _lead_handoff_message(operation)
    result = send_public_text_message(
        to=payload["lead_phone"],
        text=message,
        channel_id=payload["channel_id"],
        sender_phone_number_id=payload["sender_phone_number_id"],
        settings=settings,
    )
    LOGGER.info(
        "integral_mix.supervisor_lead_notification_sent",
        extra={
            "pipeline_step": "integral_mix.supervisor_lead_notification_sent",
            "deal_seq": operation.deal_seq,
            "supervisor_id": operation.supervisor.supervisor_id,
            "status_code": result.status_code,
            "request_id": result.request_id,
        },
    )
    return _message_receipt(result.status_code, result.request_id, result.payload)


def _lead_handoff_message(operation: SupervisorHandoffOperation) -> str:
    phone = _display_phone(operation)
    contact_line = (
        f"Se quiser falar diretamente com o supervisor, o telefone é {phone}."
        if phone != "não cadastrado"
        else "No momento, não tenho um telefone direto cadastrado do supervisor."
    )
    return (
        f"Pronto, já encaminhei seus dados para o supervisor responsável pela sua região. "
        f"Ele vai direcionar seu atendimento a um vendedor, que entrará em contato com você. "
        f"{contact_line}"
    )


def _supervisor_message(profile: dict[str, Any], *, lead_phone: str) -> str:
    labels = (
        ("Nome", "name"),
        ("Telefone", None),
        ("Documento", "document"),
        ("Atuação", "activity"),
        ("Cidade", "city"),
        ("Estado", "state"),
        ("Consumo mensal (criador)", "consumption"),
        ("Volume mensal (revendedor)", "monthly_volume"),
        ("Espécie", "species"),
        ("Frequência", "frequency"),
        ("Loja", "store_name"),
        ("Já trabalha com nutrição", "works_with_nutrition"),
        ("Categoria de produtos", "product_category"),
        ("Marcas atuais", "current_brands"),
    )
    lines = ["Você recebeu um novo lead do marketing!"]
    for label, key in labels:
        value = lead_phone if key is None else profile.get(key)
        lines.append(f"*{label}*: {value or '—'}")
    return "\n".join(lines)


def _message_receipt(
    status_code: int,
    request_id: str | None,
    payload: dict[str, Any] | None,
) -> dict[str, Any]:
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    return {
        "status_code": status_code,
        "request_id": request_id,
        "message_id": data.get("id") if isinstance(data, dict) else None,
    }


def _display_phone(operation: SupervisorHandoffOperation) -> str:
    from app.application.supervisores import supervisor_display_phone

    return supervisor_display_phone(operation.supervisor) or "não cadastrado"


def _mark_step(
    service: IntegralMixSupervisorService,
    request_key: str,
    step: SupervisorHandoffStep,
    receipt: dict[str, Any],
) -> None:
    service.mark_handoff_step(request_key=request_key, step=step, receipt=receipt)


__all__ = [
    "IntegralMixHandoffResult",
    "execute_integral_mix_supervisor_handoff",
    "process_pending_integral_mix_handoffs",
]
