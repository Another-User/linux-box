"""OptAware REST API — FastAPI application."""

from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from cli.auth import verify_api_key

logger = logging.getLogger("optaware.cli.api")

app = FastAPI(
    title="OptAware API",
    description="Intelligent Linux Server Management Agent API",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Auth dependency ---

async def require_api_key(x_api_key: str = Header(default="")) -> str:
    if not verify_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid API key")
    return x_api_key


# --- Response models ---

class HealthResponse(BaseModel):
    status: str
    hostname: str
    timestamp: str
    version: str = "0.1.0"


class StatusResponse(BaseModel):
    hostname: str
    uptime: str
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    services_running: int
    services_total: int
    active_alerts: int
    pending_actions: int


class ServiceResponse(BaseModel):
    name: str
    display_name: str
    service_type: str
    status: str
    systemd_unit: Optional[str] = None
    port: Optional[int] = None


class EventResponse(BaseModel):
    id: str
    timestamp: str
    severity: str
    source: str
    service_name: Optional[str] = None
    message: str


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    question: str
    answer: str
    sources: list[str] = []
    timestamp: str


class ActionResponse(BaseModel):
    id: str
    action_type: str
    status: str
    created_at: str
    requires_approval: bool


# --- Routes ---

@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(
        status="healthy",
        hostname=socket.gethostname(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/status", response_model=StatusResponse, dependencies=[Depends(require_api_key)])
async def system_status():
    try:
        import psutil

        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        return StatusResponse(
            hostname=socket.gethostname(),
            uptime=str(datetime.now(timezone.utc)),
            cpu_percent=cpu,
            memory_percent=mem.percent,
            disk_percent=disk.percent,
            services_running=0,
            services_total=0,
            active_alerts=0,
            pending_actions=0,
        )
    except ImportError:
        return StatusResponse(
            hostname=socket.gethostname(),
            uptime="unknown",
            cpu_percent=0,
            memory_percent=0,
            disk_percent=0,
            services_running=0,
            services_total=0,
            active_alerts=0,
            pending_actions=0,
        )


@app.get("/api/services", response_model=list[ServiceResponse], dependencies=[Depends(require_api_key)])
async def list_services():
    try:
        from services.manifest_loader import load_manifest

        services = load_manifest("config/service_manifest.yaml")
        return [
            ServiceResponse(
                name=s.name,
                display_name=s.display_name,
                service_type=s.service_type.value,
                status=s.status.value,
                systemd_unit=s.systemd_unit,
                port=s.port,
            )
            for s in services
        ]
    except Exception as e:
        logger.error("Error loading services: %s", e)
        return []


@app.get("/api/services/{name}", response_model=ServiceResponse, dependencies=[Depends(require_api_key)])
async def get_service(name: str):
    try:
        from services.manifest_loader import load_manifest

        services = load_manifest("config/service_manifest.yaml")
        for s in services:
            if s.name == name:
                return ServiceResponse(
                    name=s.name,
                    display_name=s.display_name,
                    service_type=s.service_type.value,
                    status=s.status.value,
                    systemd_unit=s.systemd_unit,
                    port=s.port,
                )
        raise HTTPException(status_code=404, detail=f"Service '{name}' not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/services/{name}/restart", dependencies=[Depends(require_api_key)])
async def restart_service(name: str):
    return {
        "status": "queued",
        "message": f"Restart of '{name}' has been queued",
        "dry_run": True,
    }


@app.get("/api/events", response_model=list[EventResponse], dependencies=[Depends(require_api_key)])
async def list_events(
    severity: str = Query(default="all"),
    service: str = Query(default="all"),
    limit: int = Query(default=50, le=200),
):
    return []


@app.get("/api/actions", response_model=list[ActionResponse], dependencies=[Depends(require_api_key)])
async def list_actions():
    return []


@app.post("/api/actions/{action_id}/approve", dependencies=[Depends(require_api_key)])
async def approve_action(action_id: str):
    return {"status": "approved", "action_id": action_id}


@app.post("/api/actions/{action_id}/deny", dependencies=[Depends(require_api_key)])
async def deny_action(action_id: str, reason: str = ""):
    return {"status": "denied", "action_id": action_id, "reason": reason}


@app.post("/api/ask", response_model=AskResponse, dependencies=[Depends(require_api_key)])
async def ask_question(request: AskRequest):
    return AskResponse(
        question=request.question,
        answer="LLM integration requires API key configuration. Please configure your LLM provider in optaware.yaml.",
        sources=[],
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/api/metrics", dependencies=[Depends(require_api_key)])
async def get_metrics():
    try:
        import psutil

        return {
            "cpu": {
                "percent": psutil.cpu_percent(interval=0.5),
                "count": psutil.cpu_count(),
                "load_avg": list(psutil.getloadavg()),
            },
            "memory": {
                "total": psutil.virtual_memory().total,
                "available": psutil.virtual_memory().available,
                "percent": psutil.virtual_memory().percent,
            },
            "disk": {
                "total": psutil.disk_usage("/").total,
                "used": psutil.disk_usage("/").used,
                "percent": psutil.disk_usage("/").percent,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    except ImportError:
        return {"error": "psutil not available"}


@app.get("/api/knowledge/search", dependencies=[Depends(require_api_key)])
async def search_knowledge(q: str = Query(..., min_length=1)):
    return {"query": q, "results": [], "message": "Knowledge base search requires Qdrant connection"}


@app.get("/api/config", dependencies=[Depends(require_api_key)])
async def get_config():
    try:
        from config.loader import load_config

        cfg = load_config("config/optaware.yaml")
        data = cfg.model_dump() if hasattr(cfg, "model_dump") else {}
        # Redact secrets
        if "llm" in data:
            data["llm"]["api_key"] = "***" if data["llm"].get("api_key") else ""
        if "portal" in data:
            data["portal"]["secret_key"] = "***" if data["portal"].get("secret_key") else ""
        return data
    except Exception as e:
        return {"error": str(e)}
