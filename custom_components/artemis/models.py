"""Data models for ARTEMIS WebEvo."""

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
    """Current personal planning state."""

    current: StatusValue | None
    current_since: datetime | None
    next_status: StatusValue | None
    next_change: datetime | None
    staff_name: str
    staff_id: str
    unit_id: str
    lookahead_weeks: int


@dataclass(slots=True, frozen=True)
class CenterAvailabilitySnapshot:
    """Current centre availability counters."""

    available: int
    in_operation: int
    counters: tuple[dict[str, Any], ...]
    unit_id: str


@dataclass(slots=True, frozen=True)
class OperationsSnapshot:
    """Current operations state."""

    count: int
    operations: tuple[dict[str, Any], ...]
