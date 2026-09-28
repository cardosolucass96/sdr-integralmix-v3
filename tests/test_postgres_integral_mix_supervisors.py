from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

import pytest

from app.core.integral_mix_supervisors import (
    SupervisorAssignmentSelection,
    SupervisorFields,
    SupervisorRoutingCategory,
)
from app.integrations.postgres_integral_mix_supervisors import (
    PostgresIntegralMixSupervisorStore,
)


def _row(supervisor_id: int = 11, **changes: Any) -> dict[str, Any]:
    row = {
        "supervisor_id": supervisor_id,
        "nocodb_record_id": 101,
        "name": "Ana",
        "phone": "+55 85 90000-0000",
        "category": "AGRO",
        "region": "CE/RN",
        "leads_received": 4,
        "is_active": True,
        "minimum_order": "10 sacos",
        "last_assigned_at": None,
        "updated_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    row.update(changes)
    return row


class _FakeCursor:
    def __init__(self, pool: _FakePool) -> None:
        self.pool = pool
        self._one: Any = None
        self._all: list[Any] = []

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: Any, params: Any = None) -> None:
        rendered = query.as_string(None) if hasattr(query, "as_string") else query
        self.pool.executed.append((rendered, params))
        self._one = None
        self._all = []
        if "SELECT constraint_row.conname" in rendered:
            self._all = list(self.pool.primary_key_rows)
        elif "SELECT supervisor.*, assignment.lead_state" in rendered:
            self._one = (
                self.pool.existing_assignments.popleft() if self.pool.existing_assignments else None
            )
        elif "RETURNING operation.request_key" in rendered:
            self._all = list(self.pool.claimed_handoff_keys)
        elif "WHERE operation.request_key = %s" in rendered:
            self._one = self.pool.handoff_rows[0] if self.pool.handoff_rows else None
        elif "SELECT operation.*, supervisor.*" in rendered:
            self._all = list(self.pool.handoff_rows)
        elif "WHERE is_active ORDER BY supervisor_id FOR UPDATE" in rendered:
            self._all = list(self.pool.active_rows)
        elif "RETURNING *" in rendered:
            self._one = self.pool.returning_row
        elif "SELECT * FROM" in rendered:
            self._all = list(self.pool.list_rows)

    def fetchone(self) -> Any:
        return self._one

    def fetchall(self) -> list[Any]:
        return self._all


class _FakeConnection:
    def __init__(self, pool: _FakePool) -> None:
        self.pool = pool

    def __enter__(self) -> _FakeConnection:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    @contextmanager
    def transaction(self):
        yield

    def cursor(self, *, row_factory: Any = None) -> _FakeCursor:
        del row_factory
        return _FakeCursor(self.pool)


class _FakePool:
    def __init__(self) -> None:
        self.primary_key_rows: list[tuple[str, str]] = []
        self.existing_assignments: deque[dict[str, Any] | None] = deque()
        self.active_rows = [_row()]
        self.list_rows = [_row()]
        self.returning_row = _row()
        self.executed: list[tuple[str, Any]] = []
        self.claimed_handoff_keys: list[dict[str, str]] = []
        self.handoff_rows: list[dict[str, Any]] = []

    @contextmanager
    def connection(self):
        yield _FakeConnection(self)


def _store(pool: _FakePool) -> PostgresIntegralMixSupervisorStore:
    return PostgresIntegralMixSupervisorStore(pool, schema="sdr_test")  # type: ignore[arg-type]


def _fields() -> SupervisorFields:
    return SupervisorFields(
        name="Ana",
        phone=None,
        category="AGRO",
        region="CE",
        leads_received=4,
        is_active=True,
        minimum_order=None,
    )


@pytest.mark.parametrize(
    ("primary_key_rows", "expected_fragments"),
    [
        ([], ["ADD CONSTRAINT", "PRIMARY KEY (supervisor_id)"]),
        (
            [("integral_mix_supervisors_pkey", "nocodb_record_id")],
            ["DROP CONSTRAINT", "ADD CONSTRAINT"],
        ),
        (
            [("integral_mix_supervisors_pkey", "supervisor_id")],
            ["CREATE UNIQUE INDEX", "integral_mix_supervisor_assignments"],
        ),
    ],
)
def test_setup_creates_schema_and_migrates_primary_key(
    primary_key_rows: list[tuple[str, str]], expected_fragments: list[str]
) -> None:
    pool = _FakePool()
    pool.primary_key_rows = primary_key_rows

    _store(pool).setup()

    statements = "\n".join(query for query, _ in pool.executed)
    assert 'CREATE SCHEMA IF NOT EXISTS "sdr_test"' in statements
    assert "CREATE TABLE IF NOT EXISTS" in statements
    assert (
        'CREATE TABLE IF NOT EXISTS "sdr_test"."integral_mix_supervisor_assignments"' in statements
    )
    for fragment in expected_fragments:
        assert fragment in statements


def test_list_and_create_map_database_rows_to_supervisor_objects() -> None:
    pool = _FakePool()
    pool.list_rows = [_row(nocodb_record_id=None, is_active=False)]
    store = _store(pool)

    listed = store.list_supervisors()
    created = store.create_supervisor(_fields())

    assert listed[0].nocodb_record_id is None
    assert listed[0].is_active is False
    assert listed[0].updated_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert created.supervisor_id == 11
    assert pool.executed[0][1] is None
    assert "ORDER BY is_active DESC, name ASC, supervisor_id ASC" in pool.executed[0][0]


@pytest.mark.parametrize(
    ("method", "exists"),
    [("update", True), ("update", False), ("active", True), ("active", False)],
)
def test_update_methods_return_row_or_none(method: str, exists: bool) -> None:
    pool = _FakePool()
    pool.returning_row = _row(is_active=False)
    if not exists:
        pool.returning_row = None
    store = _store(pool)

    if method == "update":
        result = store.update_supervisor(11, _fields())
    else:
        result = store.set_supervisor_active(11, is_active=False)

    assert (result is not None) is exists
    if result is not None:
        assert result.is_active is False


def _assignment_row(**changes: Any) -> dict[str, Any]:
    return {
        **_row(),
        "assignment_lead_state": "CE ",
        "assignment_route_categories": ["AGRO"],
        "assignment_match_mode": "region_and_category",
        **changes,
    }


def _selector(
    supervisors: Any,
    lead_state: str,
    route_categories: tuple[SupervisorRoutingCategory, ...],
) -> SupervisorAssignmentSelection:
    assert supervisors
    assert lead_state == "CE"
    assert route_categories == (SupervisorRoutingCategory.AGRO,)
    return SupervisorAssignmentSelection(
        supervisor_id=11,
        lead_state="CE",
        route_categories=("AGRO",),
        match_mode="region_and_category",
    )


def test_claim_locks_roster_updates_counter_and_persists_idempotency_receipt() -> None:
    pool = _FakePool()
    pool.returning_row = _row(leads_received=5, last_assigned_at=datetime.now(UTC))
    store = _store(pool)

    assignment = store.claim_supervisor(
        request_key="conversation-1",
        lead_state="CE",
        route_categories=(SupervisorRoutingCategory.AGRO,),
        selector=_selector,
    )

    assert assignment.supervisor.leads_received == 5
    assert assignment.already_assigned is False
    statements = [query for query, _ in pool.executed]
    assert any("FOR UPDATE" in query for query in statements)
    assert any("leads_received = leads_received + 1" in query for query in statements)
    insert = next((query, params) for query, params in pool.executed if "INSERT INTO" in query)
    assert "request_key" in insert[0]
    assert insert[1] == ("conversation-1", 11, "CE", ["AGRO"], "region_and_category")


def test_claim_returns_existing_receipt_without_locking_or_incrementing() -> None:
    pool = _FakePool()
    pool.existing_assignments.append(_assignment_row())

    assignment = _store(pool).claim_supervisor(
        request_key="conversation-1",
        lead_state="CE",
        route_categories=(SupervisorRoutingCategory.AGRO,),
        selector=_selector,
    )

    assert assignment.already_assigned is True
    assert assignment.lead_state == "CE"
    assert assignment.route_categories == ("AGRO",)
    assert len(pool.executed) == 1


def test_claim_rechecks_receipt_after_roster_lock_is_acquired() -> None:
    pool = _FakePool()
    pool.existing_assignments.extend([None, _assignment_row()])

    assignment = _store(pool).claim_supervisor(
        request_key="conversation-1",
        lead_state="CE",
        route_categories=(SupervisorRoutingCategory.AGRO,),
        selector=_selector,
    )

    assert assignment.already_assigned is True
    assert any("FOR UPDATE" in query for query, _ in pool.executed)
    assert not any("leads_received = leads_received + 1" in query for query, _ in pool.executed)


def test_claim_rejects_a_selector_result_outside_locked_roster() -> None:
    pool = _FakePool()

    def invalid_selector(*_: Any) -> SupervisorAssignmentSelection:
        return SupervisorAssignmentSelection(
            supervisor_id=99,
            lead_state="CE",
            route_categories=("AGRO",),
            match_mode="region_only",
        )

    with pytest.raises(RuntimeError, match="not in the locked roster"):
        _store(pool).claim_supervisor(
            request_key="conversation-1",
            lead_state="CE",
            route_categories=(SupervisorRoutingCategory.AGRO,),
            selector=invalid_selector,
        )

    assert not any("leads_received = leads_received + 1" in query for query, _ in pool.executed)


def test_existing_assignment_creates_missing_handoff_outbox_record() -> None:
    pool = _FakePool()
    pool.existing_assignments.append(_assignment_row())
    payload = {"profile": {"name": "Maria"}}

    assignment = _store(pool).claim_supervisor(
        request_key="pipefacil-deal-100",
        lead_state="CE",
        route_categories=(SupervisorRoutingCategory.AGRO,),
        deal_seq=100,
        handoff_payload=payload,
        selector=_selector,
    )

    assert assignment.already_assigned is True
    outbox_insert = next(
        (query, params)
        for query, params in pool.executed
        if "integral_mix_supervisor_handoffs" in query
    )
    assert "ON CONFLICT (request_key) DO NOTHING" in outbox_insert[0]
    assert outbox_insert[1][0:2] == ("pipefacil-deal-100", 100)


def test_claim_pending_handoffs_handles_empty_roster_and_maps_claimed_operations() -> None:
    pool = _FakePool()
    store = _store(pool)

    assert store.claim_pending_handoffs(limit=0) == []
    assert store.claim_pending_handoffs(request_key="missing", limit=1) == []

    pool.claimed_handoff_keys = [{"request_key": "pipefacil-deal-100"}]
    pool.handoff_rows = [
        {
            **_row(),
            "request_key": "pipefacil-deal-100",
            "deal_seq": 100,
            "lead_payload": {"profile": {"name": "Maria"}},
            "crm_receipt": {"status_code": 200},
            "supervisor_message_receipt": None,
            "lead_message_receipt": None,
            "attempts": 2,
            "last_error_code": "temporary_error",
        }
    ]
    claimed = store.claim_pending_handoffs(request_key="pipefacil-deal-100", limit=3)

    assert len(claimed) == 1
    assert claimed[0].deal_seq == 100
    assert claimed[0].crm_synced is True
    assert claimed[0].supervisor_notified is False
    assert claimed[0].lead_notified is False
    assert claimed[0].attempts == 2
    claim_query, claim_params = pool.executed[-2]
    assert "FOR UPDATE SKIP LOCKED" in claim_query
    assert claim_params == (False, "pipefacil-deal-100", 3)


def test_get_handoff_operation_reads_durable_receipts_without_claiming() -> None:
    pool = _FakePool()
    pool.handoff_rows = [
        {
            **_row(),
            "request_key": "pipefacil-deal-100",
            "deal_seq": 100,
            "lead_payload": {"profile": {"name": "Maria"}},
            "crm_receipt": {"status_code": 200},
            "supervisor_message_receipt": {"message_id": "supervisor-message"},
            "lead_message_receipt": {"message_id": "lead-message"},
            "attempts": 1,
            "last_error_code": None,
        }
    ]

    operation = _store(pool).get_handoff_operation(request_key="pipefacil-deal-100")

    assert operation is not None
    assert operation.crm_synced is True
    assert operation.supervisor_notified is True
    assert operation.lead_notified is True
    query, params = pool.executed[0]
    assert "WHERE operation.request_key = %s" in query
    assert params == ("pipefacil-deal-100",)


@pytest.mark.parametrize(
    ("step", "receipt_column"),
    [
        ("crm_synced", '"crm_receipt"'),
        ("supervisor_notified", '"supervisor_message_receipt"'),
        ("lead_notified", '"lead_message_receipt"'),
    ],
)
def test_handoff_step_persists_receipt_and_delivers_only_after_all_steps(
    step: str,
    receipt_column: str,
) -> None:
    pool = _FakePool()
    _store(pool).mark_handoff_step(
        request_key="pipefacil-deal-100",
        step=step,  # type: ignore[arg-type]
        receipt={"status_code": 200},
    )

    statements = [query for query, _ in pool.executed]
    assert receipt_column in statements[0]
    assert "status = 'processing'" in statements[0]
    assert "status = 'delivered'" in statements[1]
    assert "lead_message_receipt IS NOT NULL" in statements[1]


def test_handoff_failure_schedules_backoff_and_clears_the_lease() -> None:
    pool = _FakePool()
    _store(pool).record_handoff_failure(
        request_key="pipefacil-deal-100",
        error_code="x" * 140,
    )

    query, params = pool.executed[0]
    assert "LEAST(POWER(2, LEAST(attempts, 8))" in query
    assert "lease_expires_at = NULL" in query
    assert params == ("x" * 120, "pipefacil-deal-100")


def test_schema_is_optional_for_listing_and_assignment_queries() -> None:
    pool = _FakePool()
    store = PostgresIntegralMixSupervisorStore(pool)  # type: ignore[arg-type]

    store.list_supervisors()
    store.claim_supervisor(
        request_key="conversation-1",
        lead_state="CE",
        route_categories=(SupervisorRoutingCategory.AGRO,),
        selector=_selector,
    )

    statements = "\n".join(query for query, _ in pool.executed)
    assert 'FROM "integral_mix_supervisors"' in statements
    assert 'FROM "integral_mix_supervisor_assignments" AS assignment' in statements
    assert 'INSERT INTO "integral_mix_supervisor_assignments"' in statements
