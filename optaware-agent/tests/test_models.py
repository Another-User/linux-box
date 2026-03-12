"""Tests for data models."""

import uuid
from datetime import datetime, timezone

from models.events import Event, EventSeverity, EventSource
from models.alerts import Alert, AlertStatus
from models.actions import Action, ActionStep, ActionType, ActionStatus
from models.services import ServiceInfo, ServiceStatus, ServiceType, ServiceDependency
from models.knowledge import KnowledgeDocument, DocumentType


class TestEventModel:
    def test_create_event(self):
        event = Event(
            severity=EventSeverity.WARNING,
            source=EventSource.LOG_WATCHER,
            message="High CPU usage detected",
            details={"cpu_percent": 95.2},
        )
        assert event.severity == EventSeverity.WARNING
        assert event.source == EventSource.LOG_WATCHER
        assert event.message == "High CPU usage detected"
        assert event.id is not None
        assert event.timestamp is not None

    def test_event_with_service(self):
        event = Event(
            severity=EventSeverity.ERROR,
            source=EventSource.METRIC_COLLECTOR,
            service_name="nginx",
            message="Service unresponsive",
        )
        assert event.service_name == "nginx"


class TestAlertModel:
    def test_create_alert(self):
        alert = Alert(
            event_id=uuid.uuid4(),
            severity=EventSeverity.CRITICAL,
            title="Disk space critical",
            description="Root partition at 95% capacity",
        )
        assert alert.status == AlertStatus.ACTIVE
        assert alert.title == "Disk space critical"


class TestActionModel:
    def test_create_action(self):
        step = ActionStep(
            step_number=1,
            description="Restart nginx",
            command="systemctl restart nginx",
            expected_outcome="nginx running",
            rollback_command="systemctl start nginx",
        )
        action = Action(
            action_type=ActionType.RESTART_SERVICE,
            steps=[step],
            requires_approval=True,
        )
        assert action.status == ActionStatus.PENDING
        assert len(action.steps) == 1
        assert action.requires_approval is True


class TestServiceModel:
    def test_create_service(self):
        svc = ServiceInfo(
            name="nginx",
            display_name="Nginx Web Server",
            service_type=ServiceType.CORE,
            status=ServiceStatus.RUNNING,
            systemd_unit="nginx",
            port=80,
        )
        assert svc.name == "nginx"
        assert svc.service_type == ServiceType.CORE

    def test_service_with_dependencies(self):
        dep = ServiceDependency(service_name="dns", required=True)
        svc = ServiceInfo(
            name="web",
            display_name="Web Server",
            service_type=ServiceType.CORE,
            status=ServiceStatus.STOPPED,
            dependencies=[dep],
        )
        assert len(svc.dependencies) == 1
        assert svc.dependencies[0].service_name == "dns"


class TestKnowledgeModel:
    def test_create_document(self):
        doc = KnowledgeDocument(
            doc_type=DocumentType.RUNBOOK,
            title="Nginx Restart Procedure",
            content="1. Check config syntax\n2. Restart service",
            tags=["nginx", "restart", "web"],
        )
        assert doc.doc_type == DocumentType.RUNBOOK
        assert len(doc.tags) == 3
