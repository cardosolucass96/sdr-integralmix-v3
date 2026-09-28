from __future__ import annotations

import hmac
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.api.schemas.supervisores import SupervisorForm
from app.api.settings_security import create_csrf_token
from app.application.supervisores import (
    IntegralMixSupervisorService,
    uncovered_brazilian_states,
)
from app.core.integral_mix_supervisors import SupervisorFields

supervisores_router = APIRouter(prefix="/supervisores", include_in_schema=False)
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
NOTICE_MESSAGES = {
    "created": "Supervisor cadastrado.",
    "updated": "Cadastro atualizado.",
}


@supervisores_router.get("", response_class=HTMLResponse)
def supervisors_page(request: Request) -> Response:
    if not _is_authenticated(request):
        return _redirect("/settings/login")
    notice_key = request.query_params.get("notice")
    return _supervisors_response(
        request,
        notice=NOTICE_MESSAGES.get(notice_key or ""),
    )


@supervisores_router.post("", response_class=HTMLResponse)
async def create_supervisor(request: Request) -> Response:
    if not _is_authenticated(request):
        return _redirect("/settings/login")
    form = await request.form()
    if not _valid_csrf(request, form.get("csrf_token")):
        return _supervisors_response(
            request,
            error="Sessão expirada. Recarregue a página antes de salvar.",
            code=status.HTTP_403_FORBIDDEN,
        )

    try:
        fields = _supervisor_fields(form)
    except (TypeError, ValueError, ValidationError) as exc:
        return _supervisors_response(
            request,
            error=_validation_message(exc),
            code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    _service(request).create_supervisor(fields)
    return _redirect("/supervisores?notice=created")


@supervisores_router.post("/{supervisor_id}", response_class=HTMLResponse)
async def update_supervisor(request: Request, supervisor_id: int) -> Response:
    if not _is_authenticated(request):
        return _redirect("/settings/login")
    form = await request.form()
    if not _valid_csrf(request, form.get("csrf_token")):
        return _supervisors_response(
            request,
            error="Sessão expirada. Recarregue a página antes de salvar.",
            code=status.HTTP_403_FORBIDDEN,
        )

    try:
        fields = _supervisor_fields(form)
    except (TypeError, ValueError, ValidationError) as exc:
        return _supervisors_response(
            request,
            error=_validation_message(exc),
            code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )

    saved = _service(request).update_supervisor(supervisor_id, fields)
    if saved is None:
        return _supervisors_response(
            request,
            error="Supervisor não encontrado.",
            code=status.HTTP_404_NOT_FOUND,
        )
    return _redirect("/supervisores?notice=updated")


def _service(request: Request) -> IntegralMixSupervisorService:
    return request.app.state.integral_mix_supervisor_service


def _supervisor_fields(form: Any) -> SupervisorFields:
    payload = {
        "name": str(form.get("name") or ""),
        "phone": str(form.get("phone") or ""),
        "category": str(form.get("category") or ""),
        "region": str(form.get("region") or ""),
        "leads_received": int(str(form.get("leads_received") or "0")),
        "is_active": form.get("is_active") in {"true", "on", "1"},
        "minimum_order": str(form.get("minimum_order") or ""),
    }
    validated = SupervisorForm.model_validate(payload)
    return SupervisorFields(**validated.model_dump())


def _supervisors_response(
    request: Request,
    *,
    error: str | None = None,
    notice: str | None = None,
    code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    csrf_token = request.session.setdefault("csrf_token", create_csrf_token())
    supervisors = _service(request).list_supervisors()
    return _template_response(
        request,
        {
            "csrf_token": csrf_token,
            "supervisors": supervisors,
            "uncovered_states": uncovered_brazilian_states(supervisors),
            "active_count": sum(supervisor.is_active for supervisor in supervisors),
            "inactive_count": sum(not supervisor.is_active for supervisor in supervisors),
            "lead_count": sum(supervisor.leads_received for supervisor in supervisors),
            "error": error,
            "notice": notice,
        },
        code=code,
    )


def _is_authenticated(request: Request) -> bool:
    return bool(request.session.get("settings_authenticated"))


def _valid_csrf(request: Request, provided: object) -> bool:
    expected = request.session.get("csrf_token")
    return (
        isinstance(expected, str)
        and isinstance(provided, str)
        and hmac.compare_digest(expected, provided)
    )


def _validation_message(error: Exception) -> str:
    if isinstance(error, ValidationError):
        first = error.errors()[0]
        field = ".".join(str(item) for item in first.get("loc", ()))
        return f"Valor inválido em {field}: {first.get('msg', 'verifique o campo.')}"
    return "Valor inválido. Revise os campos e tente novamente."


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(path, status_code=status.HTTP_303_SEE_OTHER)


def _template_response(
    request: Request,
    context: dict[str, object],
    *,
    code: int,
) -> HTMLResponse:
    response = TEMPLATES.TemplateResponse(
        request,
        "supervisores.html",
        context,
        status_code=code,
    )
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response
