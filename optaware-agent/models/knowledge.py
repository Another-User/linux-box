"""Knowledge base data models for OptAware agent."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class DocumentType(str, Enum):
    man_page = "man_page"
    config_doc = "config_doc"
    runbook = "runbook"
    incident_report = "incident_report"
    solution = "solution"


class KnowledgeDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    doc_type: DocumentType
    title: str
    content: str = ""
    source_path: Optional[str] = None
    embedding: Optional[list[float]] = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
