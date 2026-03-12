"""Alert data models for OptAware agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from .events import EventSeverity


class AlertStatus(str, Enum):
    active = "active"
    acknowledged = "acknowledged"
    resolved = "resolved"
    suppressed = "suppressed"


class Alert(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_id: uuid.UUID
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: EventSeverity = EventSeverity.info
    title: str
    description: str = ""
    status: AlertStatus = AlertStatus.active
    assigned_to: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
