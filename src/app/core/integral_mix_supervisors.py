from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol


class SupervisorRoutingCategory(StrEnum):
    AQUAMIX = "AQUAMIX"
    PECUARIAS = "PECUARIAS"
    AGRO = "AGRO"
    PETSHOP = "PETSHOP"
    CANAL_PET_ALIMENTAR = "CANAL_PET_ALIMENTAR"
    AGROPECUARIAS = "AGROPECUARIAS"
    FALLBACK_AGROPECUARIAS = "FALLBACK_AGROPECUARIAS"


SupervisorMatchMode = Literal["region_and_category", "region_only"]
SupervisorHandoffStep = Literal["crm_synced", "supervisor_notified", "lead_notified"]


@dataclass(frozen=True, slots=True)
class SupervisorFields:
    name: str
    phone: str | None
    category: str
    region: str
    leads_received: int
    is_active: bool
    minimum_order: str | None


@dataclass(frozen=True, slots=True)
class Supervisor:
    supervisor_id: int
    nocodb_record_id: int | None
    name: str
    phone: str | None
    category: str
    region: str
    leads_received: int
    is_active: bool
    minimum_order: str | None
    last_assigned_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SupervisorAssignmentSelection:
    supervisor_id: int
    lead_state: str
    route_categories: tuple[str, ...]
    match_mode: SupervisorMatchMode


@dataclass(frozen=True, slots=True)
class SupervisorAssignment:
    supervisor: Supervisor
    lead_state: str
    route_categories: tuple[str, ...]
    match_mode: SupervisorMatchMode
    already_assigned: bool = False


@dataclass(frozen=True, slots=True)
class SupervisorHandoffOperation:
    request_key: str
    deal_seq: int
    supervisor: Supervisor
    lead_payload: dict[str, Any]
    crm_synced: bool
    supervisor_notified: bool
    lead_notified: bool
    attempts: int
    last_error_code: str | None


class NoEligibleSupervisorError(LookupError):
    """Raised when no active supervisor covers the lead's state."""


class SupervisorStore(Protocol):
    def list_supervisors(self) -> list[Supervisor]: ...

    def create_supervisor(self, fields: SupervisorFields) -> Supervisor: ...

    def update_supervisor(
        self,
        supervisor_id: int,
        fields: SupervisorFields,
    ) -> Supervisor | None: ...

    def set_supervisor_active(
        self,
        supervisor_id: int,
        *,
        is_active: bool,
    ) -> Supervisor | None: ...

    def claim_supervisor(
        self,
        *,
        request_key: str,
        lead_state: str,
        route_categories: tuple[SupervisorRoutingCategory, ...],
        deal_seq: int | None = None,
        handoff_payload: dict[str, Any] | None = None,
        selector: Callable[
            [Sequence[Supervisor], str, tuple[SupervisorRoutingCategory, ...]],
            SupervisorAssignmentSelection,
        ],
    ) -> SupervisorAssignment: ...

    def claim_pending_handoffs(
        self,
        *,
        request_key: str | None = None,
        limit: int = 20,
    ) -> list[SupervisorHandoffOperation]: ...

    def get_handoff_operation(
        self,
        *,
        request_key: str,
    ) -> SupervisorHandoffOperation | None: ...

    def mark_handoff_step(
        self,
        *,
        request_key: str,
        step: SupervisorHandoffStep,
        receipt: dict[str, Any],
    ) -> None: ...

    def record_handoff_failure(
        self,
        *,
        request_key: str,
        error_code: str,
    ) -> None: ...
