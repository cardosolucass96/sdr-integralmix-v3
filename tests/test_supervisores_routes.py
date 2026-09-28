from __future__ import annotations

import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import app.api.routes.settings as settings_routes
from app.application.supervisores import SupervisorFields
from app.main import create_app

CSRF_PATTERN = re.compile(r'name="csrf_token" value="([^"]+)"')


@pytest.fixture
def supervisors_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setattr(settings_routes, "verify_settings_access_key", lambda *_: True)
    with TestClient(create_app(app_env="development")) as client:
        yield client


def _login(client: TestClient) -> str:
    login_page = client.get("/settings/login")
    csrf_token = _hidden_value(login_page.text)
    response = client.post(
        "/settings/login",
        data={"csrf_token": csrf_token, "access_key": "test-key"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/supervisores")
    return _hidden_value(page.text)


def _hidden_value(html: str) -> str:
    match = CSRF_PATTERN.search(html)
    assert match is not None
    return match.group(1)


def _form(csrf_token: str, **changes: object) -> dict[str, str]:
    values: dict[str, object] = {
        "name": "Supervisora de Teste",
        "phone": "+55 85 90000-0000",
        "category": "AGRO",
        "region": "CE/RN",
        "minimum_order": "10 sacos",
        "leads_received": 4,
        "is_active": "true",
    }
    values.update(changes)
    return {"csrf_token": csrf_token, **{key: str(value) for key, value in values.items()}}


def test_supervisores_page_reuses_settings_login_and_lists_local_records(
    supervisors_client: TestClient,
) -> None:
    client = supervisors_client
    assert client.get("/supervisores", follow_redirects=False).headers["location"] == (
        "/settings/login"
    )

    csrf_token = _login(client)
    client.app.state.integral_mix_supervisor_service.create_supervisor(
        SupervisorFields(
            name="Ana Local",
            phone="+55 85 90000-0000",
            category="AGRO",
            region="CE",
            leads_received=2,
            is_active=True,
            minimum_order=None,
        )
    )

    page = client.get("/supervisores")

    assert page.status_code == 200
    assert "Ana Local" in page.text
    assert "Banco local" in page.text
    assert "Leads distribuídos" in page.text
    assert "Sem cobertura ativa:" in page.text
    assert "MS" in page.text
    assert csrf_token in page.text
    assert page.headers["cache-control"] == "no-store"


def test_supervisores_create_and_update_are_csrf_protected(
    supervisors_client: TestClient,
) -> None:
    client = supervisors_client
    csrf_token = _login(client)
    invalid = client.post("/supervisores", data=_form("invalid"))
    assert invalid.status_code == 403
    assert client.app.state.integral_mix_supervisor_service.list_supervisors() == []

    created = client.post(
        "/supervisores",
        data=_form(csrf_token),
        follow_redirects=False,
    )
    assert created.status_code == 303
    assert created.headers["location"] == "/supervisores?notice=created"
    supervisor = client.app.state.integral_mix_supervisor_service.list_supervisors()[0]

    updated = client.post(
        f"/supervisores/{supervisor.supervisor_id}",
        data=_form(csrf_token, name="Nome Ajustado", is_active=""),
        follow_redirects=False,
    )

    assert updated.status_code == 303
    saved = client.app.state.integral_mix_supervisor_service.list_supervisors()[0]
    assert saved.name == "Nome Ajustado"
    assert saved.is_active is False
    assert saved.leads_received == 4


def test_supervisores_mutations_redirect_unauthenticated_users_and_reject_bad_forms(
    supervisors_client: TestClient,
) -> None:
    client = supervisors_client
    assert (
        client.post("/supervisores", data=_form("missing"), follow_redirects=False).headers[
            "location"
        ]
        == "/settings/login"
    )
    assert (
        client.post("/supervisores/1", data=_form("missing"), follow_redirects=False).headers[
            "location"
        ]
        == "/settings/login"
    )

    csrf_token = _login(client)
    missing_csrf = client.post("/supervisores/1", data=_form(""), follow_redirects=False)
    invalid_integer = client.post(
        "/supervisores",
        data=_form(csrf_token, leads_received="quatro"),
    )
    invalid_update = client.post(
        "/supervisores/1",
        data=_form(csrf_token, leads_received="quatro"),
    )
    blank_name = client.post("/supervisores", data=_form(csrf_token, name="   "))

    assert missing_csrf.status_code == 403
    assert invalid_integer.status_code == 422
    assert invalid_update.status_code == 422
    assert "Revise os campos" in invalid_integer.text
    assert blank_name.status_code == 422
    assert "Este campo é obrigatório." in blank_name.text


def test_supervisores_form_rejects_negative_counters_and_unknown_records(
    supervisors_client: TestClient,
) -> None:
    client = supervisors_client
    csrf_token = _login(client)

    invalid = client.post(
        "/supervisores",
        data=_form(csrf_token, leads_received=-1),
    )
    missing = client.post(
        "/supervisores/99999",
        data=_form(csrf_token),
    )

    assert invalid.status_code == 422
    assert "Valor inválido" in invalid.text
    assert missing.status_code == 404
    assert "Supervisor não encontrado." in missing.text
