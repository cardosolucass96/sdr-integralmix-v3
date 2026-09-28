from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import asdict, replace
from datetime import UTC, datetime
from threading import RLock

from app.core.integral_mix_supervisors import (
    NoEligibleSupervisorError,
    Supervisor,
    SupervisorAssignment,
    SupervisorAssignmentSelection,
    SupervisorFields,
    SupervisorHandoffOperation,
    SupervisorHandoffStep,
    SupervisorRoutingCategory,
    SupervisorStore,
)

STATE_NAMES = {
    "ACRE": "AC",
    "ALAGOAS": "AL",
    "AMAPA": "AP",
    "AMAZONAS": "AM",
    "BAHIA": "BA",
    "CEARA": "CE",
    "DISTRITO FEDERAL": "DF",
    "ESPIRITO SANTO": "ES",
    "GOIAS": "GO",
    "MARANHAO": "MA",
    "MATO GROSSO": "MT",
    "MATO GROSSO DO SUL": "MS",
    "MINAS GERAIS": "MG",
    "PARA": "PA",
    "PARAIBA": "PB",
    "PARANA": "PR",
    "PERNAMBUCO": "PE",
    "PIAUI": "PI",
    "RIO DE JANEIRO": "RJ",
    "RIO GRANDE DO NORTE": "RN",
    "RIO GRANDE DO SUL": "RS",
    "RONDONIA": "RO",
    "RORAIMA": "RR",
    "SANTA CATARINA": "SC",
    "SAO PAULO": "SP",
    "SERGIPE": "SE",
    "TOCANTINS": "TO",
}
STATE_CODES = frozenset(STATE_NAMES.values())
REGION_ALIASES = {
    "NORTE": frozenset({"AC", "AP", "AM", "PA", "RO", "RR", "TO"}),
    "NORDESTE": frozenset({"AL", "BA", "CE", "MA", "PB", "PE", "PI", "RN", "SE"}),
    "NORTE E NORDESTE": frozenset(
        {
            "AC",
            "AP",
            "AM",
            "PA",
            "RO",
            "RR",
            "TO",
            "AL",
            "BA",
            "CE",
            "MA",
            "PB",
            "PE",
            "PI",
            "RN",
            "SE",
        }
    ),
    "SUDESTE": frozenset({"ES", "MG", "RJ", "SP"}),
    "SUL": frozenset({"PR", "RS", "SC"}),
    "CENTRO-OESTE": frozenset({"DF", "GO", "MS", "MT"}),
}
SUPERVISOR_CATEGORY_CODES = {
    "AQUAMIX": SupervisorRoutingCategory.AQUAMIX,
    "PECUARIAS": SupervisorRoutingCategory.PECUARIAS,
    "AGRO": SupervisorRoutingCategory.AGRO,
    "CRIADORES DE EQUINO": SupervisorRoutingCategory.AGRO,
    "PETSHOP": SupervisorRoutingCategory.PETSHOP,
    "CANAL PET ALIMENTAR": SupervisorRoutingCategory.CANAL_PET_ALIMENTAR,
    "AGROPECUARIAS": SupervisorRoutingCategory.AGROPECUARIAS,
}


def _normalize_label(value: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value)
    without_marks = "".join(char for char in ascii_value if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", without_marks).strip().upper()


def normalize_brazilian_state(value: str) -> str | None:
    normalized = _normalize_label(value)
    if normalized in STATE_CODES:
        return normalized
    return STATE_NAMES.get(normalized)


def states_in_supervisor_region(region: str) -> frozenset[str]:
    normalized = _normalize_label(region)
    alias_states = REGION_ALIASES.get(normalized)
    if alias_states is not None:
        return alias_states

    states: set[str] = set()
    for part in re.split(r"[/,;|]+", normalized):
        part = part.strip()
        if part in STATE_CODES:
            states.add(part)
        elif part in STATE_NAMES:
            states.add(STATE_NAMES[part])
        else:
            for token in part.split():
                if token in STATE_CODES:
                    states.add(token)
    return frozenset(states)


def uncovered_brazilian_states(supervisors: Sequence[Supervisor]) -> tuple[str, ...]:
    covered_states = {
        state
        for supervisor in supervisors
        if supervisor.is_active
        for state in states_in_supervisor_region(supervisor.region)
    }
    return tuple(sorted(STATE_CODES - covered_states))


def supervisor_categories(category: str) -> frozenset[SupervisorRoutingCategory]:
    categories: set[SupervisorRoutingCategory] = set()
    for part in category.split(","):
        code = SUPERVISOR_CATEGORY_CODES.get(_normalize_label(part))
        if code is not None:
            categories.add(code)
    return frozenset(categories)


def supervisor_display_phone(supervisor: Supervisor) -> str | None:
    if _normalize_label(supervisor.name) == "CHRISTIAN BEZERRA":
        return "99 91503634"
    return supervisor.phone


def _matching_region(
    supervisors: Sequence[Supervisor],
    normalized_state: str,
) -> list[Supervisor]:
    return [
        supervisor
        for supervisor in supervisors
        if supervisor.is_active
        and normalized_state in states_in_supervisor_region(supervisor.region)
    ]


def _matching_categories(
    supervisors: Sequence[Supervisor],
    route_categories: tuple[SupervisorRoutingCategory, ...],
) -> list[Supervisor]:
    direct_categories = set(route_categories) - {SupervisorRoutingCategory.FALLBACK_AGROPECUARIAS}
    return [
        supervisor
        for supervisor in supervisors
        if direct_categories.intersection(supervisor_categories(supervisor.category))
    ]


def _least_loaded(supervisors: Sequence[Supervisor]) -> Supervisor:
    lowest_load = min(supervisor.leads_received for supervisor in supervisors)
    least_loaded = [
        supervisor for supervisor in supervisors if supervisor.leads_received == lowest_load
    ]
    return min(
        least_loaded,
        key=lambda supervisor: (
            supervisor.last_assigned_at is not None,
            supervisor.last_assigned_at.isoformat() if supervisor.last_assigned_at else "",
            supervisor.supervisor_id,
        ),
    )


def select_supervisor_for_lead(
    supervisors: Sequence[Supervisor],
    lead_state: str,
    route_categories: tuple[SupervisorRoutingCategory, ...],
) -> SupervisorAssignmentSelection:
    normalized_state = normalize_brazilian_state(lead_state)
    if normalized_state is None:
        raise NoEligibleSupervisorError("Lead state must be a Brazilian state or state code.")

    matching_region = _matching_region(supervisors, normalized_state)
    if not matching_region:
        raise NoEligibleSupervisorError(f"No active supervisor covers state {normalized_state}.")

    exact_matches = _matching_categories(matching_region, route_categories)
    match_mode = "region_and_category" if exact_matches else "region_only"
    eligible = exact_matches or matching_region
    selected = _least_loaded(eligible)
    return SupervisorAssignmentSelection(
        supervisor_id=selected.supervisor_id,
        lead_state=normalized_state,
        route_categories=tuple(sorted(category.value for category in route_categories)),
        match_mode=match_mode,
    )


class IntegralMixSupervisorService:
    def __init__(self, store: SupervisorStore) -> None:
        self._store = store

    def list_supervisors(self) -> list[Supervisor]:
        return self._store.list_supervisors()

    def create_supervisor(self, fields: SupervisorFields) -> Supervisor:
        return self._store.create_supervisor(fields)

    def update_supervisor(
        self,
        supervisor_id: int,
        fields: SupervisorFields,
    ) -> Supervisor | None:
        return self._store.update_supervisor(supervisor_id, fields)

    def set_supervisor_active(
        self,
        supervisor_id: int,
        *,
        is_active: bool,
    ) -> Supervisor | None:
        return self._store.set_supervisor_active(supervisor_id, is_active=is_active)

    def assign_supervisor(
        self,
        *,
        request_key: str,
        lead_state: str,
        route_categories: Sequence[SupervisorRoutingCategory | str],
        deal_seq: int | None = None,
        handoff_payload: dict[str, object] | None = None,
    ) -> SupervisorAssignment:
        if not request_key.strip() or len(request_key) > 200:
            raise ValueError("request_key must contain between 1 and 200 characters.")
        normalized_categories = tuple(
            category
            if isinstance(category, SupervisorRoutingCategory)
            else SupervisorRoutingCategory(category)
            for category in route_categories
        )
        return self._store.claim_supervisor(
            request_key=request_key.strip(),
            lead_state=lead_state,
            route_categories=normalized_categories,
            deal_seq=deal_seq,
            handoff_payload=handoff_payload,
            selector=select_supervisor_for_lead,
        )

    def claim_pending_handoffs(
        self,
        *,
        request_key: str | None = None,
        limit: int = 20,
    ) -> list[SupervisorHandoffOperation]:
        return self._store.claim_pending_handoffs(request_key=request_key, limit=limit)

    def get_handoff_operation(
        self,
        *,
        request_key: str,
    ) -> SupervisorHandoffOperation | None:
        return self._store.get_handoff_operation(request_key=request_key)

    def mark_handoff_step(
        self,
        *,
        request_key: str,
        step: SupervisorHandoffStep,
        receipt: dict[str, object],
    ) -> None:
        self._store.mark_handoff_step(request_key=request_key, step=step, receipt=receipt)

    def record_handoff_failure(self, *, request_key: str, error_code: str) -> None:
        self._store.record_handoff_failure(request_key=request_key, error_code=error_code)


class InMemoryIntegralMixSupervisorStore:
    def __init__(self) -> None:
        self._supervisors: dict[int, Supervisor] = {}
        self._assignments: dict[str, SupervisorAssignment] = {}
        self._handoffs: dict[str, SupervisorHandoffOperation] = {}
        self._leased_handoffs: set[str] = set()
        self._next_id = 1
        self._lock = RLock()

    def list_supervisors(self) -> list[Supervisor]:
        with self._lock:
            return sorted(
                self._supervisors.values(),
                key=lambda supervisor: (
                    not supervisor.is_active,
                    supervisor.name.casefold(),
                    supervisor.supervisor_id,
                ),
            )

    def create_supervisor(self, fields: SupervisorFields) -> Supervisor:
        with self._lock:
            supervisor = Supervisor(
                supervisor_id=self._next_id,
                nocodb_record_id=None,
                **asdict(fields),
                updated_at=datetime.now(UTC),
            )
            self._next_id += 1
            self._supervisors[supervisor.supervisor_id] = supervisor
            return supervisor

    def update_supervisor(
        self,
        supervisor_id: int,
        fields: SupervisorFields,
    ) -> Supervisor | None:
        with self._lock:
            current = self._supervisors.get(supervisor_id)
            if current is None:
                return None
            updated = replace(current, **asdict(fields), updated_at=datetime.now(UTC))
            self._supervisors[supervisor_id] = updated
            return updated

    def set_supervisor_active(
        self,
        supervisor_id: int,
        *,
        is_active: bool,
    ) -> Supervisor | None:
        with self._lock:
            current = self._supervisors.get(supervisor_id)
            if current is None:
                return None
            updated = replace(current, is_active=is_active, updated_at=datetime.now(UTC))
            self._supervisors[supervisor_id] = updated
            return updated

    def claim_supervisor(
        self,
        *,
        request_key: str,
        lead_state: str,
        route_categories: tuple[SupervisorRoutingCategory, ...],
        deal_seq: int | None = None,
        handoff_payload: dict[str, object] | None = None,
        selector: Callable[
            [Sequence[Supervisor], str, tuple[SupervisorRoutingCategory, ...]],
            SupervisorAssignmentSelection,
        ],
    ) -> SupervisorAssignment:
        with self._lock:
            existing = self._assignments.get(request_key)
            if existing is not None:
                self._ensure_handoff_operation(
                    request_key=request_key,
                    assignment=existing,
                    deal_seq=deal_seq,
                    handoff_payload=handoff_payload,
                )
                return replace(existing, already_assigned=True)
            selection = selector(tuple(self._supervisors.values()), lead_state, route_categories)
            selected = self._supervisors[selection.supervisor_id]
            updated = replace(
                selected,
                leads_received=selected.leads_received + 1,
                last_assigned_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self._supervisors[updated.supervisor_id] = updated
            assignment = SupervisorAssignment(
                supervisor=updated,
                lead_state=selection.lead_state,
                route_categories=selection.route_categories,
                match_mode=selection.match_mode,
            )
            self._assignments[request_key] = assignment
            self._ensure_handoff_operation(
                request_key=request_key,
                assignment=assignment,
                deal_seq=deal_seq,
                handoff_payload=handoff_payload,
            )
            return assignment

    def _ensure_handoff_operation(
        self,
        *,
        request_key: str,
        assignment: SupervisorAssignment,
        deal_seq: int | None,
        handoff_payload: dict[str, object] | None,
    ) -> None:
        if deal_seq is None or handoff_payload is None or request_key in self._handoffs:
            return
        self._handoffs[request_key] = SupervisorHandoffOperation(
            request_key=request_key,
            deal_seq=deal_seq,
            supervisor=assignment.supervisor,
            lead_payload=dict(handoff_payload),
            crm_synced=False,
            supervisor_notified=False,
            lead_notified=False,
            attempts=0,
            last_error_code=None,
        )

    def claim_pending_handoffs(
        self,
        *,
        request_key: str | None = None,
        limit: int = 20,
    ) -> list[SupervisorHandoffOperation]:
        if limit < 1:
            return []
        with self._lock:
            keys = [request_key] if request_key else list(self._handoffs)
            claimed: list[SupervisorHandoffOperation] = []
            for key in keys:
                operation = self._handoffs.get(key)
                if operation is None or key in self._leased_handoffs:
                    continue
                if (
                    operation.crm_synced
                    and operation.supervisor_notified
                    and operation.lead_notified
                ):
                    continue
                self._leased_handoffs.add(key)
                claimed.append(replace(operation, attempts=operation.attempts + 1))
                self._handoffs[key] = replace(operation, attempts=operation.attempts + 1)
                if len(claimed) >= limit:
                    break
            return claimed

    def get_handoff_operation(
        self,
        *,
        request_key: str,
    ) -> SupervisorHandoffOperation | None:
        with self._lock:
            operation = self._handoffs.get(request_key)
            return replace(operation) if operation is not None else None

    def mark_handoff_step(
        self,
        *,
        request_key: str,
        step: SupervisorHandoffStep,
        receipt: dict[str, object],
    ) -> None:
        del receipt
        with self._lock:
            operation = self._handoffs.get(request_key)
            if operation is None:
                return
            updated = replace(
                operation,
                crm_synced=operation.crm_synced or step == "crm_synced",
                supervisor_notified=(
                    operation.supervisor_notified or step == "supervisor_notified"
                ),
                lead_notified=operation.lead_notified or step == "lead_notified",
                last_error_code=None,
            )
            self._handoffs[request_key] = updated
            if updated.crm_synced and updated.supervisor_notified and updated.lead_notified:
                self._leased_handoffs.discard(request_key)

    def record_handoff_failure(self, *, request_key: str, error_code: str) -> None:
        with self._lock:
            operation = self._handoffs.get(request_key)
            if operation is not None:
                self._handoffs[request_key] = replace(
                    operation,
                    last_error_code=error_code[:120],
                )
            self._leased_handoffs.discard(request_key)
