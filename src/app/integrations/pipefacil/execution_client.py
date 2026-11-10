"""Execution-scoped v2 transport. Never falls back to the unrestricted public API."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import Settings
from app.integrations.pipefacil.client_types import (
    PipefacilConversationHistoryLookupError,
    PipefacilDealLookupError,
    PipefacilDealUpdateError,
    PipefacilDealUpdateResult,
    PipefacilSendMessageError,
    PipefacilSendMessageResult,
)
from app.integrations.pipefacil.conversation import (
    PipefacilConversationHistoryError,
    normalize_conversation_history,
)

BASE_PATH = "/api/v1/ai-agent"


def _effect_key(settings: Settings, resource: str, payload: dict | None) -> str:
    execution = settings.pipefacil_execution
    if execution is None:
        raise ValueError("A v2 execution is required.")
    name = settings.pipefacil_effect_name
    if not name:
        if resource == "messages":
            raise ValueError("A stable message effect name is required.")
        name = json.dumps(payload, sort_keys=True, ensure_ascii=True)
    identity = f"{execution.executionId}:{resource}:{name}"
    return "raquel-v2-" + hashlib.sha256(identity.encode()).hexdigest()


def _read_response(response: httpx.Response, error_type):
    if response.is_error:
        raise error_type(
            "Pipefacil rejected the execution request.",
            error_code="pipefacil_upstream_error",
            status_code=response.status_code,
            request_id=response.headers.get("x-request-id"),
        )
    try:
        return response.json()["data"]
    except (ValueError, KeyError, TypeError) as exc:
        raise error_type(
            "Invalid execution response.", error_code="pipefacil_upstream_error"
        ) from exc


def _applied_receipt(data: Any, error_type) -> None:
    if not isinstance(data, dict) or data.get("status") != "APPLIED":
        raise error_type(
            "Effect is not confirmed; inspect the receipt before retrying.",
            error_code="pipefacil_upstream_error",
        )


def _request(settings, method, resource, *, payload=None, client=None, error_type):
    execution = settings.pipefacil_execution
    if execution is None or not settings.pipefacil_api_key:
        raise error_type("Execution credentials missing.", error_code="pipefacil_api_key_missing")
    headers = {"Authorization": f"Bearer {settings.pipefacil_api_key}"}
    owns_client = client is None
    current = client or httpx.Client(
        base_url=settings.pipefacil_base_url.rstrip("/"),
        timeout=settings.pipefacil_timeout_seconds,
    )
    try:
        if method != "GET":
            key = _effect_key(settings, resource, payload)
            receipt = current.get(f"{BASE_PATH}/receipts/{key}", headers=headers)
            if receipt.status_code != 404:
                data = _read_response(receipt, error_type)
                _applied_receipt(data, error_type)
                return receipt, data
            headers["Idempotency-Key"] = key
        if execution.expiresAt <= datetime.now(UTC):
            raise error_type("Execution expired.", error_code="pipefacil_upstream_error")
        path = f"{BASE_PATH}/executions/{execution.executionId}"
        if resource:
            path += "/" + resource
        response = current.request(method, path, json=payload, headers=headers)
        data = _read_response(response, error_type)
        if method != "GET":
            _applied_receipt(data, error_type)
        return response, data
    except httpx.HTTPError as exc:
        # A future attempt checks the same receipt before submitting anything.
        raise error_type(
            "Execution transport failed.", error_code="pipefacil_transport_error"
        ) from exc
    finally:
        if owns_client:
            current.close()


def fetch_deal(settings: Settings, *, client=None, seq=None) -> dict:
    _, data = _request(settings, "GET", "", client=client, error_type=PipefacilDealLookupError)
    deal = data.get("deal") if isinstance(data, dict) else None
    if not isinstance(deal, dict) or deal.get("id") != settings.pipefacil_execution.dealId:
        raise PipefacilDealLookupError(
            "Execution returned a different deal.", error_code="pipefacil_upstream_error"
        )
    if seq is not None and deal.get("seq") != seq:
        raise PipefacilDealLookupError(
            "Execution returned a different deal sequence.", error_code="pipefacil_upstream_error"
        )
    return deal


def fetch_history(settings: Settings, *, client=None):
    _, data = _request(
        settings,
        "GET",
        "messages",
        client=client,
        error_type=PipefacilConversationHistoryLookupError,
    )
    try:
        return normalize_conversation_history({"data": data})
    except PipefacilConversationHistoryError as exc:
        raise PipefacilConversationHistoryLookupError(
            "Invalid execution history.", error_code="pipefacil_history_response_invalid"
        ) from exc


def update_deal(settings: Settings, payload: dict, *, client=None):
    response, data = _request(
        settings,
        "PATCH",
        "deal",
        payload=payload,
        client=client,
        error_type=PipefacilDealUpdateError,
    )
    return PipefacilDealUpdateResult(
        response.status_code, response.headers.get("x-request-id"), {"data": data}
    )


def send_message(settings: Settings, payload: dict, *, client=None):
    response, data = _request(
        settings,
        "POST",
        "messages",
        payload=payload,
        client=client,
        error_type=PipefacilSendMessageError,
    )
    return PipefacilSendMessageResult(
        response.status_code, response.headers.get("x-request-id"), {"data": data}
    )


def finish_handoff(settings: Settings, *, client=None) -> dict:
    _, data = _request(
        settings, "POST", "handoff", client=client, error_type=PipefacilDealUpdateError
    )
    return data
