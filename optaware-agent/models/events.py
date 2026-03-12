"""Event data models for OptAware agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class EventSeverity(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class EventSource(str, Enum):
    log_watcher = "log_watcher"
    metric_collector = "metric_collector"
    anomaly_detector = "anomaly_detector"
    user = "user"
    system = "system"


class Event(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: EventSeverity = EventSeverity.info
    source: EventSource = EventSource.system
    service_name: Optional[str] = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[uuid.UUID] = None
