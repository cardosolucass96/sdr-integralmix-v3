from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

import app.main as main_module
from app.application.supervisores import InMemoryIntegralMixSupervisorStore


def test_supervisor_service_uses_in_memory_store_without_database() -> None:
    service = main_module._build_integral_mix_supervisor_service(
        SimpleNamespace(database_pool=None, database_schema=None)
    )

    assert isinstance(service._store, InMemoryIntegralMixSupervisorStore)


def test_supervisor_service_sets_up_postgres_store_when_database_is_configured(
    monkeypatch,
) -> None:
    pool = object()
    calls: dict[str, Any] = {}

    class StubPostgresStore:
        def __init__(self, received_pool: Any, *, schema: str | None) -> None:
            calls["pool"] = received_pool
            calls["schema"] = schema
            calls["store"] = self

        def setup(self) -> None:
            calls["setup"] = True

    monkeypatch.setattr(main_module, "PostgresIntegralMixSupervisorStore", StubPostgresStore)
    service = main_module._build_integral_mix_supervisor_service(
        SimpleNamespace(database_pool=pool, database_schema="sdr")
    )

    assert service._store is calls["store"]
    assert calls == {"pool": pool, "schema": "sdr", "store": service._store, "setup": True}


def test_supervisor_handoff_batch_reads_settings_inside_worker_thread(monkeypatch) -> None:
    calls: list[tuple[Any, Any]] = []

    def process(service, *, settings):
        calls.append((service, settings))
        return 1

    monkeypatch.setattr(main_module, "process_pending_integral_mix_handoffs", process)
    runtime_settings_service = SimpleNamespace(get_execution_settings=lambda: "settings")

    assert (
        main_module._process_integral_mix_supervisor_handoff_batch(
            "service",
            runtime_settings_service,
        )
        == 1
    )
    assert calls == [("service", "settings")]


@pytest.mark.parametrize("scenario", ["processed", "empty", "error", "timeout"])
def test_supervisor_handoff_worker_processes_retries_and_stops_cleanly(
    monkeypatch,
    scenario: str,
) -> None:
    calls: list[str] = []

    async def run_worker() -> None:
        stop_event = asyncio.Event()

        def process(_service, _runtime_settings_service):
            calls.append(scenario)
            if scenario == "timeout" and len(calls) == 1:
                return 0
            stop_event.set()
            if scenario == "error":
                raise RuntimeError("worker batch failed")
            return 1 if scenario == "processed" else 0

        async def wait_for(awaitable, timeout):
            del timeout
            if scenario == "timeout" and len(calls) == 1:
                awaitable.close()
                raise TimeoutError
            return await awaitable

        monkeypatch.setattr(
            main_module, "asyncio", SimpleNamespace(wait_for=wait_for, to_thread=asyncio.to_thread)
        )
        monkeypatch.setattr(main_module, "_process_integral_mix_supervisor_handoff_batch", process)
        await main_module._run_integral_mix_supervisor_handoff_worker(
            stop_event=stop_event,
            service="service",
            runtime_settings_service=SimpleNamespace(get_execution_settings=lambda: "settings"),
        )

    asyncio.run(run_worker())

    assert len(calls) == (2 if scenario == "timeout" else 1)
