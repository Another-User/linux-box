"""Action data models for OptAware agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class ActionType(str, Enum):
    restart_service = "restart_service"
    modify_config = "modify_config"
    run_command = "run_command"
    scale_resource = "scale_resource"
    rollback = "rollback"
    notify = "notify"


class ActionStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    rolled_back = "rolled_back"


class ActionStep(BaseModel):
    model_config = ConfigDict(extra="ignore")

    step_number: int = Field(ge=1)
    description: str
    command: Optional[str] = None
    expected_outcome: str = ""
    rollback_command: Optional[str] = None


class Action(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    alert_id: Optional[uuid.UUID] = None
    action_type: ActionType
    status: ActionStatus = ActionStatus.pending
    steps: list[ActionStep] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    result: Optional[Any] = None
    requires_approval: bool = True
