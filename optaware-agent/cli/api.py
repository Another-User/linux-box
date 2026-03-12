"""OptAware REST API — Phase 8.

FastAPI application exposing the full OptAware management surface over HTTP.

Routes
------
GET  /api/health                   — Liveness probe (no auth required).
GET  /api/status                   — System overview: services + live metrics.
GET  /api/services                 — List all registered services.
GET  /api/services/{name}          — Detailed info for one service.
POST /api/services/{name}/start    — Start a service (body: {dry_run: bool}).
POST /api/services/{name}/stop     — Stop a service  (body: {dry_run: bool}).
POST /api/services/{name}/restart  — Restart a service.
GET  /api/events                   — Recent events (qs: severity, service, limit).
GET  /api/actions                  — Action history (qs: status, limit).
POST /api/actions/{id}/approve     — Approve a pending action.
POST /api/actions/{id}/deny        — Deny a pending action (body: {reason: str}).
POST /api/ask                      — Ask the LLM a question (body: {question: str}).
GET  /api/metrics                  — Current system metrics via psutil.
GET  /api/knowledge/search         — Search the knowledge base (qs: q).
GET  /api/config                   — Current config, secrets redacted.
POST /api/config/validate          — Validate current config against schema.
POST /api/config/reload            — Signal daemon to reload config from disk.

WebSocket
---------
WS /ws/events                      — Real-time event stream (see websocket.py).

Authentication
--------------
All routes except ``GET /api/health`` require the ``X-API-Key`` header.
The key is validated by :class:`~cli.auth.APIKeyAuth`.
"""

from __future__ import annotations

import logging
import socket
import time
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from cli.auth import APIKeyAuth

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

app = FastAPI(
    title="OptAware API",
    description=(
        "AI-powered Linux server management agent — REST API.\n\n"
        "All protected endpoints require the `X-API-Key` header.\n"
        "Set the key via the `OPTAWARE_API_KEY` environment variable or "
        "`portal.secret_key` in `optaware.yaml`."
    ),
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Shared auth dependency instance.
_auth = APIKeyAuth(auto_error=True)

# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """Liveness probe response."""

    status: str = "healthy"
    hostname: str
    timestamp: str
    version: str = "0.1.0"
    uptime_seconds: Optional[float] = None


class ServiceDependencyModel(BaseModel):
    service_name: str
    required: bool = True


class ServiceModel(BaseModel):
    """Full service descriptor."""

    name: str
    display_name: str = ""
    service_type: str = "elective"
    status: str = "unknown"
    systemd_unit: Optional[str] = None
    docker_container: Optional[str] = None
    config_paths: list[str] = Field(default_factory=list)
    log_paths: list[str] = Field(default_factory=list)
    dependencies: list[ServiceDependencyModel] = Field(default_factory=list)
    health_check_cmd: Optional[str] = None
    port: Optional[int] = None


class MetricsCPU(BaseModel):
    usage_percent: float = 0.0
    load_avg_1m: float = 0.0
    load_avg_5m: float = 0.0
    load_avg_15m: float = 0.0
    core_count_logical: Optional[int] = None
    core_count_physical: Optional[int] = None
    per_core_percent: list[float] = Field(default_factory=list)


class MetricsMemory(BaseModel):
    total_bytes: int = 0
    available_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    percent: float = 0.0
    swap_total_bytes: int = 0
    swap_used_bytes: int = 0
    swap_percent: float = 0.0


class DiskPartition(BaseModel):
    device: str = ""
    mountpoint: str = ""
    fstype: str = ""
    total_bytes: int = 0
    used_bytes: int = 0
    free_bytes: int = 0
    percent: float = 0.0


class MetricsDisk(BaseModel):
    partitions: list[DiskPartition] = Field(default_factory=list)
    io_counters: dict[str, Any] = Field(default_factory=dict)


class MetricsNetworkAggregate(BaseModel):
    bytes_sent: int = 0
    bytes_recv: int = 0
    packets_sent: int = 0
    packets_recv: int = 0


class MetricsNetwork(BaseModel):
    aggregate: MetricsNetworkAggregate = Field(default_factory=MetricsNetworkAggregate)
    interfaces: dict[str, Any] = Field(default_factory=dict)
    connection_count: int = 0


class MetricsResponse(BaseModel):
    timestamp: float = Field(default_factory=time.time)
    cpu: MetricsCPU = Field(default_factory=MetricsCPU)
    memory: MetricsMemory = Field(default_factory=MetricsMemory)
    disk: MetricsDisk = Field(default_factory=MetricsDisk)
    network: MetricsNetwork = Field(default_factory=MetricsNetwork)


class StatusResponse(BaseModel):
    """System overview returned by ``GET /api/status``."""

    hostname: str
    timestamp: str
    services: list[ServiceModel] = Field(default_factory=list)
    services_running: int = 0
    services_total: int = 0
    metrics: dict[str, Any] = Field(default_factory=dict)


class EventModel(BaseModel):
    id: str
    timestamp: str
    severity: str
    source: str
    service_name: Optional[str] = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ActionStepModel(BaseModel):
    step_number: int
    description: str
    command: Optional[str] = None
    expected_outcome: str = ""
    rollback_command: Optional[str] = None


class ActionModel(BaseModel):
    id: str
    alert_id: Optional[str] = None
    action_type: str
    status: str
    steps: list[ActionStepModel] = Field(default_factory=list)
    created_at: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    result: Optional[Any] = None
    requires_approval: bool = True


class ServiceActionRequest(BaseModel):
    dry_run: bool = True


class ApproveActionResponse(BaseModel):
    action_id: str
    status: str
    message: str


class DenyActionRequest(BaseModel):
    reason: str = ""


class DenyActionResponse(BaseModel):
    action_id: str
    status: str
    reason: str
    message: str


class AskRequest(BaseModel):
    question: str = Field(..., min_length=1)


class AskResponse(BaseModel):
    question: str
    answer: str
    sources: list[dict[str, Any]] = Field(default_factory=list)
    timestamp: str
    context_used: bool = False


class KnowledgeSearchResult(BaseModel):
    id: Optional[str] = None
    title: str = "Untitled"
    doc_type: str = "unknown"
    source_path: Optional[str] = None
    score: float = 0.0
    text: str = ""


class KnowledgeSearchResponse(BaseModel):
    query: str
    results: list[KnowledgeSearchResult] = Field(default_factory=list)
    total: int = 0


class ConfigValidateResponse(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ConfigReloadResponse(BaseModel):
    success: bool
    message: str
    reloaded: bool


class ServiceLifecycleResponse(BaseModel):
    success: bool
    message: str
    service: str
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Process start time for uptime calculation.
_START_TIME = time.time()


def _load_registry():
    """Return the service registry, populating it from the manifest if needed."""
    from services.registry import get_registry  # noqa: PLC0415

    registry = get_registry()
    if len(registry) == 0:
        import os  # noqa: PLC0415

        manifest_candidates = [
            "/etc/optaware/service_manifest.yaml",
            os.path.join(os.path.dirname(__file__), "..", "config", "service_manifest.yaml"),
        ]
        for path in manifest_candidates:
            if os.path.isfile(path):
                try:
                    registry.load_from_manifest(path)
                    break
                except Exception as exc:
                    logger.warning("Failed to load manifest from %s: %s", path, exc)
    return registry


def _collect_metrics() -> dict[str, Any]:
    """Return a raw metrics dict from psutil (graceful on import failure)."""
    try:
        from perception.metric_collector import MetricCollector  # noqa: PLC0415

        return MetricCollector().collect_all()
    except Exception as exc:
        logger.debug("_collect_metrics: %s", exc)
        return {}


def _service_to_model(svc) -> ServiceModel:
    """Convert a ServiceInfo Pydantic model to a ServiceModel response."""
    return ServiceModel(
        name=svc.name,
        display_name=svc.display_name,
        service_type=svc.service_type.value if hasattr(svc.service_type, "value") else str(svc.service_type),
        status=svc.status.value if hasattr(svc.status, "value") else str(svc.status),
        systemd_unit=svc.systemd_unit,
        docker_container=svc.docker_container,
        config_paths=list(svc.config_paths),
        log_paths=list(svc.log_paths),
        dependencies=[
            ServiceDependencyModel(
                service_name=d.service_name,
                required=d.required,
            )
            for d in svc.dependencies
        ],
        health_check_cmd=svc.health_check_cmd,
        port=svc.port,
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

# -- Health ------------------------------------------------------------------


@app.get(
    "/api/health",
    response_model=HealthResponse,
    summary="Liveness probe",
    tags=["System"],
)
async def health_check() -> HealthResponse:
    """Return the API liveness status.  No authentication required."""
    return HealthResponse(
        status="healthy",
        hostname=socket.gethostname(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        uptime_seconds=round(time.time() - _START_TIME, 1),
    )


# -- Status ------------------------------------------------------------------


@app.get(
    "/api/status",
    response_model=StatusResponse,
    summary="System overview",
    tags=["System"],
)
async def system_status(_: str = Depends(_auth)) -> StatusResponse:
    """Return a combined view of service statuses and current system metrics."""
    registry = _load_registry()
    all_services = registry.get_all()
    service_models = [_service_to_model(s) for s in all_services]
    running_count = sum(1 for s in all_services if s.status.value == "running")  # type: ignore[union-attr]

    raw_metrics = _collect_metrics()

    return StatusResponse(
        hostname=socket.gethostname(),
        timestamp=datetime.now(timezone.utc).isoformat(),
        services=service_models,
        services_running=running_count,
        services_total=len(all_services),
        metrics=raw_metrics,
    )


# -- Services ----------------------------------------------------------------


@app.get(
    "/api/services",
    response_model=list[ServiceModel],
    summary="List all services",
    tags=["Services"],
)
async def list_services(_: str = Depends(_auth)) -> list[ServiceModel]:
    """Return every service registered in the service registry."""
    registry = _load_registry()
    return [_service_to_model(s) for s in registry.get_all()]


@app.get(
    "/api/services/{name}",
    response_model=ServiceModel,
    summary="Service detail",
    tags=["Services"],
)
async def get_service(name: str, _: str = Depends(_auth)) -> ServiceModel:
    """Return detailed information for the named service."""
    registry = _load_registry()
    svc = registry.get(name)
    if svc is None:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    return _service_to_model(svc)


@app.post(
    "/api/services/{name}/start",
    response_model=ServiceLifecycleResponse,
    summary="Start a service",
    tags=["Services"],
)
async def start_service(
    name: str,
    body: ServiceActionRequest = ServiceActionRequest(),
    _: str = Depends(_auth),
) -> ServiceLifecycleResponse:
    """Start the named service, respecting its dependency order."""
    try:
        from services.dependency import DependencyResolver  # noqa: PLC0415
        from services.health import HealthChecker  # noqa: PLC0415
        from services.manager import ServiceManager  # noqa: PLC0415

        registry = _load_registry()
        manager = ServiceManager(registry, DependencyResolver(), HealthChecker())
        result = manager.start_service(name, dry_run=body.dry_run)
        return ServiceLifecycleResponse(
            success=result.success,
            message=result.message,
            service=name,
            details=result.details,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    except Exception as exc:
        logger.exception("start_service error for %s", name)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/api/services/{name}/stop",
    response_model=ServiceLifecycleResponse,
    summary="Stop a service",
    tags=["Services"],
)
async def stop_service(
    name: str,
    body: ServiceActionRequest = ServiceActionRequest(),
    _: str = Depends(_auth),
) -> ServiceLifecycleResponse:
    """Stop the named service."""
    try:
        from services.dependency import DependencyResolver  # noqa: PLC0415
        from services.health import HealthChecker  # noqa: PLC0415
        from services.manager import ServiceManager  # noqa: PLC0415

        registry = _load_registry()
        manager = ServiceManager(registry, DependencyResolver(), HealthChecker())
        result = manager.stop_service(name, dry_run=body.dry_run)
        return ServiceLifecycleResponse(
            success=result.success,
            message=result.message,
            service=name,
            details=result.details,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    except Exception as exc:
        logger.exception("stop_service error for %s", name)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/api/services/{name}/restart",
    response_model=ServiceLifecycleResponse,
    summary="Restart a service",
    tags=["Services"],
)
async def restart_service(
    name: str,
    body: ServiceActionRequest = ServiceActionRequest(),
    _: str = Depends(_auth),
) -> ServiceLifecycleResponse:
    """Restart the named service."""
    try:
        from services.dependency import DependencyResolver  # noqa: PLC0415
        from services.health import HealthChecker  # noqa: PLC0415
        from services.manager import ServiceManager  # noqa: PLC0415

        registry = _load_registry()
        manager = ServiceManager(registry, DependencyResolver(), HealthChecker())
        result = manager.restart_service(name, dry_run=body.dry_run)
        return ServiceLifecycleResponse(
            success=result.success,
            message=result.message,
            service=name,
            details=result.details,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found.")
    except Exception as exc:
        logger.exception("restart_service error for %s", name)
        raise HTTPException(status_code=500, detail=str(exc))


# -- Events ------------------------------------------------------------------


@app.get(
    "/api/events",
    response_model=list[EventModel],
    summary="Recent events",
    tags=["Events"],
)
async def list_events(
    severity: Optional[str] = Query(
        default=None,
        description="Filter by severity: info | warning | error | critical",
    ),
    service: Optional[str] = Query(default=None, description="Filter by service name."),
    limit: int = Query(default=50, ge=1, le=500, description="Maximum number of events."),
    _: str = Depends(_auth),
) -> list[EventModel]:
    """Return recent events from the in-memory event store.

    Results are filtered by *severity* and/or *service* when those query
    parameters are provided, then limited to *limit* entries (newest first).
    """
    try:
        from event_processor import get_event_store  # noqa: PLC0415

        store = get_event_store()
        raw_events = store.get_recent(limit=limit * 4)  # fetch extra before filtering
    except Exception:
        # Fall back to an empty list when the event store is unavailable.
        raw_events = []

    results: list[EventModel] = []
    for ev in raw_events:
        ev_severity = ev.severity.value if hasattr(ev.severity, "value") else str(ev.severity)
        ev_source = ev.source.value if hasattr(ev.source, "value") else str(ev.source)

        if severity and ev_severity != severity:
            continue
        if service and ev.service_name != service:
            continue

        results.append(
            EventModel(
                id=str(ev.id),
                timestamp=ev.timestamp.isoformat(),
                severity=ev_severity,
                source=ev_source,
                service_name=ev.service_name,
                message=ev.message,
                details=ev.details,
            )
        )
        if len(results) >= limit:
            break

    return results


# -- Actions -----------------------------------------------------------------


@app.get(
    "/api/actions",
    response_model=list[ActionModel],
    summary="Action history",
    tags=["Actions"],
)
async def list_actions(
    status_filter: Optional[str] = Query(
        default=None,
        alias="status",
        description="Filter by action status.",
    ),
    limit: int = Query(default=50, ge=1, le=500),
    _: str = Depends(_auth),
) -> list[ActionModel]:
    """Return the action history, optionally filtered by status."""
    try:
        from planning.audit import get_audit_log  # noqa: PLC0415

        audit = get_audit_log()
        raw_actions = audit.get_all(limit=limit * 2)
    except Exception:
        raw_actions = []

    results: list[ActionModel] = []
    for action in raw_actions:
        act_status = action.status.value if hasattr(action.status, "value") else str(action.status)
        if status_filter and act_status != status_filter:
            continue

        act_type = (
            action.action_type.value
            if hasattr(action.action_type, "value")
            else str(action.action_type)
        )
        results.append(
            ActionModel(
                id=str(action.id),
                alert_id=str(action.alert_id) if action.alert_id else None,
                action_type=act_type,
                status=act_status,
                steps=[
                    ActionStepModel(
                        step_number=s.step_number,
                        description=s.description,
                        command=s.command,
                        expected_outcome=s.expected_outcome,
                        rollback_command=s.rollback_command,
                    )
                    for s in action.steps
                ],
                created_at=action.created_at.isoformat(),
                started_at=action.started_at.isoformat() if action.started_at else None,
                completed_at=action.completed_at.isoformat() if action.completed_at else None,
                result=action.result,
                requires_approval=action.requires_approval,
            )
        )
        if len(results) >= limit:
            break

    return results


@app.post(
    "/api/actions/{action_id}/approve",
    response_model=ApproveActionResponse,
    summary="Approve a pending action",
    tags=["Actions"],
)
async def approve_action(
    action_id: str,
    _: str = Depends(_auth),
) -> ApproveActionResponse:
    """Approve the pending action identified by *action_id*."""
    try:
        from planning.approval import get_approval_workflow  # noqa: PLC0415

        workflow = get_approval_workflow()
        action = workflow.approve(action_id)
        if action is None:
            raise HTTPException(
                status_code=404,
                detail=f"No pending action with ID '{action_id}'.",
            )
        return ApproveActionResponse(
            action_id=action_id,
            status="approved",
            message=f"Action {action_id} approved and queued for execution.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("approve_action error for %s", action_id)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/api/actions/{action_id}/deny",
    response_model=DenyActionResponse,
    summary="Deny a pending action",
    tags=["Actions"],
)
async def deny_action(
    action_id: str,
    body: DenyActionRequest = DenyActionRequest(),
    _: str = Depends(_auth),
) -> DenyActionResponse:
    """Deny the pending action identified by *action_id*."""
    try:
        from planning.approval import get_approval_workflow  # noqa: PLC0415

        workflow = get_approval_workflow()
        action = workflow.deny(action_id, reason=body.reason)
        if action is None:
            raise HTTPException(
                status_code=404,
                detail=f"No pending action with ID '{action_id}'.",
            )
        return DenyActionResponse(
            action_id=action_id,
            status="denied",
            reason=body.reason,
            message=f"Action {action_id} denied.",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("deny_action error for %s", action_id)
        raise HTTPException(status_code=500, detail=str(exc))


# -- Ask ---------------------------------------------------------------------


@app.post(
    "/api/ask",
    response_model=AskResponse,
    summary="Ask OptAware a question",
    tags=["LLM"],
)
async def ask_question(
    body: AskRequest,
    _: str = Depends(_auth),
) -> AskResponse:
    """Send a natural-language question to the LLM and return the answer.

    The endpoint retrieves relevant knowledge-base context via the RAG pipeline
    before forwarding the enriched prompt to the configured LLM provider.
    """
    question = body.question
    answer = ""
    sources: list[dict[str, Any]] = []
    context_used = False

    try:
        from cognition.llm_provider import get_llm_provider  # noqa: PLC0415
        from cognition.prompt_engine import PromptEngine  # noqa: PLC0415

        llm = get_llm_provider()
        engine = PromptEngine()

        # Attempt RAG enrichment.
        rag_context = ""
        try:
            from knowledge.rag_pipeline import RAGPipeline  # noqa: PLC0415
            from knowledge.vector_store import get_vector_store  # noqa: PLC0415
            from knowledge.document_indexer import DocumentIndexer  # noqa: PLC0415

            vs = get_vector_store()
            indexer = DocumentIndexer(vs)
            rag = RAGPipeline(vs, indexer)
            rag_result = await rag.query(question, top_k=5)
            rag_context = rag.format_for_llm(rag_result)
            sources = rag_result.get("sources", [])
            context_used = bool(sources)
        except Exception as rag_exc:
            logger.debug("RAG pipeline unavailable: %s", rag_exc)

        prompt = engine.build_ask_prompt(question, context=rag_context) if rag_context else question
        response = await llm.complete(prompt)
        answer = response.content if hasattr(response, "content") else str(response)

    except Exception as exc:
        logger.warning("ask_question: LLM unavailable: %s", exc)
        answer = (
            "The LLM provider is currently unavailable.  "
            "Ensure your API key is configured (OPTAWARE_API_KEY or llm.api_key in optaware.yaml) "
            "and that the provider endpoint is reachable."
        )

    return AskResponse(
        question=question,
        answer=answer,
        sources=sources,
        timestamp=datetime.now(timezone.utc).isoformat(),
        context_used=context_used,
    )


# -- Metrics -----------------------------------------------------------------


@app.get(
    "/api/metrics",
    response_model=MetricsResponse,
    summary="Current system metrics",
    tags=["System"],
)
async def get_metrics(_: str = Depends(_auth)) -> MetricsResponse:
    """Return a snapshot of current system metrics (CPU, memory, disk, network)."""
    raw = _collect_metrics()

    cpu_raw = raw.get("cpu", {})
    mem_raw = raw.get("memory", {})
    disk_raw = raw.get("disk", {})
    net_raw = raw.get("network", {})

    agg_raw = net_raw.get("aggregate", {})

    return MetricsResponse(
        timestamp=raw.get("timestamp", time.time()),
        cpu=MetricsCPU(
            usage_percent=cpu_raw.get("usage_percent", 0.0),
            load_avg_1m=cpu_raw.get("load_avg_1m", 0.0),
            load_avg_5m=cpu_raw.get("load_avg_5m", 0.0),
            load_avg_15m=cpu_raw.get("load_avg_15m", 0.0),
            core_count_logical=cpu_raw.get("core_count_logical"),
            core_count_physical=cpu_raw.get("core_count_physical"),
            per_core_percent=cpu_raw.get("per_core_percent", []),
        ),
        memory=MetricsMemory(
            total_bytes=mem_raw.get("total_bytes", 0),
            available_bytes=mem_raw.get("available_bytes", 0),
            used_bytes=mem_raw.get("used_bytes", 0),
            free_bytes=mem_raw.get("free_bytes", 0),
            percent=mem_raw.get("percent", 0.0),
            swap_total_bytes=mem_raw.get("swap_total_bytes", 0),
            swap_used_bytes=mem_raw.get("swap_used_bytes", 0),
            swap_percent=mem_raw.get("swap_percent", 0.0),
        ),
        disk=MetricsDisk(
            partitions=[
                DiskPartition(
                    device=p.get("device", ""),
                    mountpoint=p.get("mountpoint", ""),
                    fstype=p.get("fstype", ""),
                    total_bytes=p.get("total_bytes", 0),
                    used_bytes=p.get("used_bytes", 0),
                    free_bytes=p.get("free_bytes", 0),
                    percent=p.get("percent", 0.0),
                )
                for p in disk_raw.get("partitions", [])
            ],
            io_counters=disk_raw.get("io_counters", {}),
        ),
        network=MetricsNetwork(
            aggregate=MetricsNetworkAggregate(
                bytes_sent=agg_raw.get("bytes_sent", 0),
                bytes_recv=agg_raw.get("bytes_recv", 0),
                packets_sent=agg_raw.get("packets_sent", 0),
                packets_recv=agg_raw.get("packets_recv", 0),
            ),
            interfaces=net_raw.get("interfaces", {}),
            connection_count=net_raw.get("connection_count", 0),
        ),
    )


# -- Knowledge ---------------------------------------------------------------


@app.get(
    "/api/knowledge/search",
    response_model=KnowledgeSearchResponse,
    summary="Search the knowledge base",
    tags=["Knowledge"],
)
async def search_knowledge(
    q: str = Query(..., min_length=1, description="Search query string."),
    top_k: int = Query(default=10, ge=1, le=50, description="Number of results."),
    _: str = Depends(_auth),
) -> KnowledgeSearchResponse:
    """Perform a semantic search of the knowledge base via the RAG pipeline."""
    try:
        from knowledge.document_indexer import DocumentIndexer  # noqa: PLC0415
        from knowledge.rag_pipeline import RAGPipeline  # noqa: PLC0415
        from knowledge.vector_store import get_vector_store  # noqa: PLC0415

        vs = get_vector_store()
        indexer = DocumentIndexer(vs)
        rag = RAGPipeline(vs, indexer)
        docs = await rag.retrieve(q, top_k=top_k)

        results = [
            KnowledgeSearchResult(
                id=str(doc.get("id", "")),
                title=doc.get("payload", {}).get("title", "Untitled"),
                doc_type=doc.get("payload", {}).get("doc_type", "unknown"),
                source_path=doc.get("payload", {}).get("source_path"),
                score=doc.get("score", 0.0),
                text=doc.get("payload", {}).get("text", ""),
            )
            for doc in docs
        ]

        return KnowledgeSearchResponse(query=q, results=results, total=len(results))

    except Exception as exc:
        logger.warning("knowledge/search: %s", exc)
        return KnowledgeSearchResponse(query=q, results=[], total=0)


# -- Config ------------------------------------------------------------------


@app.get(
    "/api/config",
    summary="Current configuration (secrets redacted)",
    tags=["Configuration"],
)
async def get_config(_: str = Depends(_auth)) -> dict[str, Any]:
    """Return the active OptAware configuration with all secrets redacted."""
    try:
        from config.loader import get_config as _get_cfg  # noqa: PLC0415

        cfg = _get_cfg()
        data: dict[str, Any] = cfg.model_dump()

        # Redact all secret / key fields.
        if "llm" in data and "api_key" in data["llm"]:
            data["llm"]["api_key"] = "***" if data["llm"]["api_key"] else "(not set)"
        if "portal" in data and "secret_key" in data["portal"]:
            data["portal"]["secret_key"] = "***" if data["portal"]["secret_key"] else "(not set)"

        return data

    except RuntimeError:
        # Config not loaded yet; return a helpful error payload.
        return {
            "error": "Configuration not loaded.",
            "hint": "Start the OptAware daemon or run 'optaware setup' first.",
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Config error: {exc}")


@app.post(
    "/api/config/validate",
    response_model=ConfigValidateResponse,
    summary="Validate current configuration",
    tags=["Configuration"],
)
async def validate_config(_: str = Depends(_auth)) -> ConfigValidateResponse:
    """Validate the current in-memory configuration against the Pydantic schema."""
    errors: list[str] = []
    warnings: list[str] = []

    try:
        from config.loader import get_config as _get_cfg  # noqa: PLC0415
        from config.validator import validate_config as _validate  # noqa: PLC0415

        cfg = _get_cfg()
        issues = _validate(cfg)

        for issue in issues:
            if isinstance(issue, str):
                if "warn" in issue.lower():
                    warnings.append(issue)
                else:
                    errors.append(issue)
            elif isinstance(issue, dict):
                msg = issue.get("message", str(issue))
                severity = issue.get("severity", "error")
                if severity == "warning":
                    warnings.append(msg)
                else:
                    errors.append(msg)

        return ConfigValidateResponse(valid=not errors, errors=errors, warnings=warnings)

    except RuntimeError:
        return ConfigValidateResponse(
            valid=False,
            errors=["Configuration not loaded. Run 'optaware setup' first."],
        )
    except Exception as exc:
        return ConfigValidateResponse(valid=False, errors=[str(exc)])


@app.post(
    "/api/config/reload",
    response_model=ConfigReloadResponse,
    summary="Reload configuration from disk",
    tags=["Configuration"],
)
async def reload_config(_: str = Depends(_auth)) -> ConfigReloadResponse:
    """Signal the daemon to reload the configuration file from disk.

    Attempts to locate and load the config from the standard paths.
    """
    import os  # noqa: PLC0415

    candidates = [
        "/etc/optaware/optaware.yaml",
        os.path.join(os.path.dirname(__file__), "..", "config", "optaware.yaml"),
    ]

    for path in candidates:
        if os.path.isfile(path):
            try:
                from config.loader import load_config  # noqa: PLC0415

                load_config(path)
                logger.info("Configuration reloaded from %s", path)
                return ConfigReloadResponse(
                    success=True,
                    message=f"Configuration reloaded from {path}.",
                    reloaded=True,
                )
            except Exception as exc:
                return ConfigReloadResponse(
                    success=False,
                    message=f"Failed to reload configuration from {path}: {exc}",
                    reloaded=False,
                )

    return ConfigReloadResponse(
        success=False,
        message="No configuration file found. Run 'optaware setup' first.",
        reloaded=False,
    )


# -- WebSocket ---------------------------------------------------------------


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    """Real-time event stream.  Clients receive JSON event messages as they occur.

    Connection URL: ``ws://host:8080/ws/events``

    Protocol
    --------
    * On connect the server sends a ``{"type": "connected", …}`` message.
    * The server pushes ``{"type": "event", …}`` messages whenever a new event
      is emitted by the agent.
    * Clients may send ``{"type": "ping"}`` frames; the server replies with
      ``{"type": "pong"}``.
    * The connection remains open until the client disconnects.
    """
    from cli.websocket import ws_manager  # noqa: PLC0415

    await ws_manager.connect(websocket)
    try:
        while True:
            import json as _json  # noqa: PLC0415

            raw = await websocket.receive_text()
            try:
                msg = _json.loads(raw)
                if msg.get("type") == "ping":
                    await ws_manager.send_to(
                        websocket,
                        {"type": "pong", "timestamp": datetime.now(timezone.utc).isoformat()},
                    )
            except Exception:
                pass  # Ignore malformed frames.
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
