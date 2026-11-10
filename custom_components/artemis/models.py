"""Shared normalized data models for ARTEMIS backends."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class StatusValue:
    """An ARTEMIS planning status."""

    code: str
    name: str


@dataclass(slots=True, frozen=True)
class PlanningInterval:
    """A normalized ARTEMIS planning interval."""

    start: datetime
    end: datetime
    status: StatusValue


@dataclass(slots=True, frozen=True)
class PlanningSnapshot:
    """Current personal planning state shared by WebEvo and Smartemis."""

    current: StatusValue | None
    current_since: datetime | None
    current_period_end: datetime | None
    next_status: StatusValue | None
    next_change: datetime | None
    staff_name: str
    staff_id: str
    unit_id: str
    lookahead_weeks: int
    available_personnel: int | None = None
    personnel_in_operation: int | None = None


@dataclass(slots=True, frozen=True)
class CenterAvailabilitySnapshot:
    """Current native WebEvo centre availability counters."""

    available: int
    in_operation: int
    unit_id: str


@dataclass(slots=True, frozen=True)
class OperationsSnapshot:
    """Current active ARTEMIS operations, normalized for Home Assistant."""

    count: int
    operations: tuple[dict[str, Any], ...]
