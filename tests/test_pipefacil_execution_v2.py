"""Protocol safety: scoped routes, stable receipts, stale executions and split credentials."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError
from test_integral_mix_handoff import _service_and_operation
from test_pipefacil import _message_received_payload

import app.application.integral_mix_handoff as handoff
from app.core.config import BootstrapSettings, RuntimeSettings, Settings, build_execution_settings
from app.core.pipefacil_execution import PipefacilExecution
from app.integrations.pipefacil import client as api
from app.integrations.pipefacil import execution_client as v2
from app.integrations.pipefacil.contracts import MessageReceivedEventRequest


def settings(**overrides):
    execution = PipefacilExecution(
        protocolVersion=2,
        executionId="delivery-1",
        agentId="agent-1",
        dealId="deal-example-001",
        generation=7,
        agentVersion=2,
        channelId="channel-example-001",
        conversationId="conversation-1",
        expiresAt=datetime.now(UTC) + timedelta(minutes=5),
    )
    return Settings(
        pipefacil_api_key="agent-key",
        pipefacil_execution=execution,
        pipefacil_effect_name="reply-1",
        **overrides,
    )


def transport(handler):
    return httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))


def applied():
    return httpx.Response(200, json={"data": {"id": "receipt-1", "status": "APPLIED"}})


def test_v2_webhook_and_batch_keep_execution():
    raw = _message_received_payload().model_dump(mode="json")
    raw["execution"] = settings().pipefacil_execution.model_dump(mode="json")
    event = MessageReceivedEventRequest.model_validate(raw)
    assert event.messages_for_processing()[0].execution == event.execution
    assert event.execution.generation == 7


@pytest.mark.parametrize(
    "change",
    [
        {"protocolVersion": 1},
        {"dealId": "other"},
        {"channelId": "other"},
        {"executionId": "../escape"},
        {"expiresAt": "2026-01-01T00:00:00"},
    ],
)
def test_bad_execution_is_rejected_not_downgraded(change):
    raw = _message_received_payload().model_dump(mode="json")
    raw["execution"] = settings().pipefacil_execution.model_dump(mode="json") | change
    with pytest.raises(ValidationError):
        MessageReceivedEventRequest.model_validate(raw)


def test_v2_without_deal_rejected():
    raw = _message_received_payload().model_dump(mode="json")
    raw.update(execution=settings().pipefacil_execution.model_dump(mode="json"))
    raw["data"]["deal"] = None
    with pytest.raises(ValidationError):
        MessageReceivedEventRequest.model_validate(raw)


@pytest.mark.parametrize("media", [False, True])
def test_send_uses_scoped_route_and_no_recipient(media):
    seen = []

    def handle(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(404)
        return applied()

    with transport(handle) as client:
        args = dict(to="+5500000000000", settings=settings(), client=client)
        result = (
            api.send_whatsapp_media_message(
                **args, media_type="image", media_url="https://example.test/a.png"
            )
            if media
            else api.send_public_text_message(**args, text="Olá")
        )
    assert result.status_code == 200
    body = json.loads(seen[-1].content)
    assert "to" not in body and "channelId" not in body
    assert body.get("mediaUrl") if media else body["body"] == "Olá"
    assert seen[-1].url.path == "/api/v1/ai-agent/executions/delivery-1/messages"
    assert seen[-1].headers["authorization"] == "Bearer agent-key"
    assert seen[-1].headers["idempotency-key"] in seen[0].url.path


@pytest.mark.parametrize("status", ["RESERVED", "UNCERTAIN", "FAILED"])
def test_unconfirmed_receipt_never_sends_again(status):
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json={"data": {"status": status}})

    with transport(handle) as client, pytest.raises(api.PipefacilSendMessageError):
        api.send_public_text_message(to="55000", text="Olá", settings=settings(), client=client)
    assert [r.method for r in seen] == ["GET"]


def test_acknowledged_effect_is_not_resent_even_after_expiry():
    cfg = settings()
    cfg = cfg.model_copy(
        update={
            "pipefacil_execution": cfg.pipefacil_execution.model_copy(
                update={"expiresAt": datetime.now(UTC) - timedelta(minutes=1)}
            )
        }
    )
    seen = []

    def handle(request):
        seen.append(request)
        return applied()

    with transport(handle) as client:
        v2.send_message(cfg, {"type": "text", "body": "retry"}, client=client)
    assert len(seen) == 1 and seen[0].method == "GET"


def test_expired_execution_cannot_create_effect():
    cfg = settings()
    cfg = cfg.model_copy(
        update={
            "pipefacil_execution": cfg.pipefacil_execution.model_copy(
                update={"expiresAt": datetime.now(UTC) - timedelta(minutes=1)}
            )
        }
    )
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(404)

    with transport(handle) as client, pytest.raises(api.PipefacilSendMessageError, match="expired"):
        v2.send_message(cfg, {"type": "text", "body": "x"}, client=client)
    assert len(seen) == 1


@pytest.mark.parametrize("status,body", [(403, {}), (200, {}), (200, None)])
def test_invalid_response_fails_closed(status, body):
    with transport(lambda r: httpx.Response(status, json=body)) as client:
        with pytest.raises(api.PipefacilDealLookupError):
            api.fetch_deal_by_seq(seq=100, settings=settings(), client=client)


def test_transport_failure_has_no_legacy_fallback():
    seen = []

    def handle(request):
        seen.append(request.url.path)
        raise httpx.ReadTimeout("timeout")

    with transport(handle) as client, pytest.raises(api.PipefacilSendMessageError):
        v2.send_message(settings(), {"type": "text", "body": "x"}, client=client)
    assert len(seen) == 1 and seen[0].startswith("/api/v1/ai-agent/receipts/")


@pytest.mark.parametrize(
    "deal", [None, {"id": "other", "seq": 100}, {"id": "deal-example-001", "seq": 101}]
)
def test_context_must_match_original_deal(deal):
    with transport(lambda r: httpx.Response(200, json={"data": {"deal": deal}})) as client:
        with pytest.raises(api.PipefacilDealLookupError):
            api.fetch_deal_by_seq(seq=100, settings=settings(), client=client)


def test_scoped_context_history_catalog_and_writes():
    seen = []

    def handle(request):
        seen.append(request)
        if "/receipts/" in request.url.path:
            return httpx.Response(404)
        if request.method != "GET":
            return applied()
        if request.url.path.endswith("/messages"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={"data": {"deal": {"id": "deal-example-001", "seq": 100}}})

    cfg = settings(pipefacil_handoff_pipelines=[{"id": "pipeline-1"}])
    with transport(handle) as client:
        assert api.fetch_deal_by_seq(seq=100, settings=cfg, client=client)["seq"] == 100
        api.fetch_pipefacil_conversation_history(settings=cfg, client=client)
        assert api.fetch_pipefacil_pipelines(settings=cfg, client=client) == [{"id": "pipeline-1"}]
        api.update_deal_properties(
            seq=100, properties={"cidade": "Fortaleza"}, settings=cfg, client=client
        )
        api.update_deal_stage(seq=100, stage_id="stage-2", settings=cfg, client=client)
        assert v2.finish_handoff(cfg, client=client)["status"] == "APPLIED"
    assert all(r.url.path.startswith("/api/v1/ai-agent/") for r in seen)


def test_missing_credentials_or_effect_name_cannot_send():
    with transport(lambda r: httpx.Response(404)) as client:
        with pytest.raises(api.PipefacilSendMessageError):
            v2.send_message(Settings(), {}, client=client)
        with pytest.raises(ValueError, match="stable"):
            v2.send_message(
                settings().model_copy(update={"pipefacil_effect_name": None}), {}, client=client
            )
        with pytest.raises(ValueError):
            v2._effect_key(Settings(), "messages", {})
    cfg = settings().model_copy(update={"pipefacil_effect_name": None})
    assert v2._effect_key(cfg, "deal", {"a": 1, "b": 2}) == v2._effect_key(
        cfg, "deal", {"b": 2, "a": 1}
    )


def test_separate_sender_is_not_persisted_or_used_for_lead(monkeypatch):
    cfg = settings(pipefacil_supervisor_api_key="supervisor-key")
    _, operation = _service_and_operation()
    checked = []
    monkeypatch.setattr(handoff, "fetch_deal", lambda s, **kw: checked.append(s))
    separate = handoff._supervisor_sender_settings(operation, cfg)
    assert separate.pipefacil_execution is None
    assert separate.pipefacil_api_key == "supervisor-key"
    assert checked == [cfg]
    assert cfg.pipefacil_api_key == "agent-key"
    with pytest.raises(api.PipefacilSendMessageError):
        handoff._supervisor_sender_settings(operation, settings())
    bootstrap = BootstrapSettings(pipefacil_supervisor_api_key="separate")
    assert (
        build_execution_settings(bootstrap, RuntimeSettings()).pipefacil_supervisor_api_key
        == "separate"
    )
    assert "separate" not in repr(bootstrap)


def test_handoff_retry_restores_context_and_pauses_last(monkeypatch):
    service, operation = _service_and_operation()
    cfg = settings()
    operation = replace(
        operation,
        crm_synced=True,
        supervisor_notified=True,
        lead_payload=operation.lead_payload
        | {"execution": cfg.pipefacil_execution.model_dump(mode="json")},
    )
    calls = []
    monkeypatch.setattr(
        handoff,
        "send_public_text_message",
        lambda **kw: (
            calls.append(("lead", kw["settings"])),
            api.PipefacilSendMessageResult(200, "r", {}),
        )[1],
    )
    monkeypatch.setattr(handoff, "finish_handoff", lambda s: (calls.append(("pause", s)), {})[1])
    assert handoff.process_supervisor_handoff_operation(service, operation, settings=Settings())
    assert [c[0] for c in calls] == ["lead", "pause"]
    assert calls[0][1].pipefacil_execution == cfg.pipefacil_execution
    assert calls[0][1].pipefacil_effect_name == "handoff-lead"


def test_separate_key_cannot_reuse_agent_key():
    _, operation = _service_and_operation()
    with pytest.raises(api.PipefacilSendMessageError):
        handoff._supervisor_sender_settings(
            operation, settings(pipefacil_supervisor_api_key="agent-key")
        )


def test_inbound_handler_propagates_v2_context(monkeypatch):
    import app.application.pipefacil as application

    raw = _message_received_payload().model_dump(mode="json")
    raw["execution"] = settings().pipefacil_execution.model_dump(mode="json")
    payload = MessageReceivedEventRequest.model_validate(raw)
    captured = []
    monkeypatch.setattr(
        application,
        "_handle_pipefacil_message_received_once",
        lambda *args, **kwargs: captured.append(kwargs["settings"]),
    )
    application.handle_pipefacil_message_received(payload, settings=Settings())
    assert captured[0].pipefacil_execution == payload.execution


def test_handoff_outbox_stores_execution_without_secrets(monkeypatch):
    service, operation = _service_and_operation()
    captured = []
    original = service.assign_supervisor
    monkeypatch.setattr(
        service,
        "assign_supervisor",
        lambda **kw: (captured.append(kw["handoff_payload"]), original(**kw))[1],
    )
    monkeypatch.setattr(service, "claim_pending_handoffs", lambda **kw: [])
    cfg = settings(pipefacil_supervisor_api_key="separate-key")
    handoff.execute_integral_mix_supervisor_handoff(
        service=service,
        request=operation.lead_payload["profile"],
        deal_seq=77,
        lead_phone="55000",
        channel_id="channel-example-001",
        sender_phone_number_id="sender",
        settings=cfg,
    )
    assert captured[0]["execution"]["executionId"] == "delivery-1"
    assert "separate-key" not in json.dumps(captured)
    assert "agent-key" not in json.dumps(captured)


def test_v2_resume_accepts_initial_agent_version_and_batch_signal():
    raw = _message_received_payload().model_dump(mode="json")
    raw["type"] = "attendance.resume"
    raw["execution"] = settings().pipefacil_execution.model_dump(mode="json")
    raw["execution"]["agentVersion"] = 0
    raw["data"]["messages"] = [raw["data"].pop("message")]
    event = MessageReceivedEventRequest.model_validate(raw)
    assert event.type == "attendance.resume"
    assert event.execution.agentVersion == 0
    assert event.messages_for_processing()[0].execution == event.execution


def test_resume_without_execution_is_rejected():
    raw = _message_received_payload().model_dump(mode="json")
    raw["type"] = "attendance.resume"
    with pytest.raises(ValidationError, match="requires a v2 execution"):
        MessageReceivedEventRequest.model_validate(raw)


def test_negative_agent_version_is_rejected():
    raw = _message_received_payload().model_dump(mode="json")
    raw["execution"] = settings().pipefacil_execution.model_dump(mode="json")
    raw["execution"]["agentVersion"] = -1
    with pytest.raises(ValidationError):
        MessageReceivedEventRequest.model_validate(raw)
