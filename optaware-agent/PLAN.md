# OptAware — Intelligent Linux Server Management Agent

## Overview
OptAware is an AI-powered autonomous agent that monitors, manages, and optimizes
Linux server infrastructure. It uses LLM reasoning to detect anomalies, plan
remediations, and execute corrective actions — all while maintaining a knowledge
base of past incidents and solutions.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                  Web Portal (Phase 9)           │
│         Dashboard · Events · Actions · Ask      │
├─────────────────────────────────────────────────┤
│              CLI & API Layer (Phase 8)          │
├──────────┬──────────┬──────────┬────────────────┤
│ Planning │ Cognition│Knowledge │  Perception    │
│ (Phase 4)│ (Phase 3)│(Phase 5) │  (Phase 2)     │
├──────────┴──────────┴──────────┴────────────────┤
│         Core Skeleton (Phase 1)                 │
│  Config · Models · Services · Daemon · Logging  │
├─────────────────────────────────────────────────┤
│       Docker & Persistent Volumes (Phase 7)     │
│       Service Implementations (Phase 6)         │
└─────────────────────────────────────────────────┘
```

## Phases

### Phase 0 — Plan & Scaffold
- Project structure, plan document, initial commit

### Phase 1 — Core Skeleton
- `config/` — YAML-based hierarchical config with validation
- `models/` — Pydantic data models (events, alerts, actions, services)
- `services/` — Service registry, dependency resolver, health checks
- `daemon.py` — Main daemon loop with signal handling
- `logging_setup.py` — Structured JSON logging with correlation IDs
- `scheduler.py` — Cron-like task scheduler
- `event_processor.py` — Central event bus
- `setup_wizard.py` — Interactive first-run configuration
- `optaware.yaml` — Default configuration
- `service_manifest.yaml` — Registry of all 30+ managed services
- `data_map.yaml` — LLM-readable index of data locations
- `pyproject.toml` — Package definition

### Phase 2 — Perception Layer
- Log watchers (journald, syslog, application logs)
- Metric collectors (CPU, memory, disk, network, processes)
- Anomaly detection (statistical + pattern-based)
- File integrity monitoring
- Network connection tracking

### Phase 3 — LLM/Cognition Layer
- Prompt engine with template management
- Response parser (structured output extraction)
- Context manager (conversation history, token budgets)
- Multi-provider support (Anthropic, OpenAI, local models)
- Cost tracking and rate limiting

### Phase 4 — Planning & Execution Layer
- Action planner (break LLM suggestions into executable steps)
- Executor with dry-run mode
- Rollback manager (snapshot + undo)
- Approval workflows (auto/manual/hybrid)
- Audit trail

### Phase 5 — Knowledge Layer
- Vector store integration (Qdrant)
- Document indexer (man pages, config docs, runbooks)
- Retrieval-augmented generation (RAG) pipeline
- Incident memory (past problems and solutions)

### Phase 6 — Service Implementations
Core services: DNS (bind/unbound), DHCP (kea/isc), Firewall (nftables/iptables),
VPN (WireGuard/OpenVPN), Web (nginx/apache), Mail (postfix/dovecot),
Database (PostgreSQL/MySQL/MariaDB), Monitoring (prometheus/grafana),
Backup (restic/borgmatic), NTP, SSH, Certificates (Let's Encrypt)

Elective services: Samba, NFS, LDAP, Redis, Docker registry,
Load balancer (HAProxy), Reverse proxy, FTP, Print (CUPS), Cron management

### Phase 7 — Docker Integration
- Dockerfile and docker-compose.yml
- Persistent volume mappings to /data/
- Health checks and restart policies
- Multi-stage build for minimal image

### Phase 8 — CLI & API Layer
- Rich CLI with Click
- REST API with FastAPI
- Authentication (API keys + JWT)
- WebSocket for real-time events

### Phase 9 — Web Portal
- Dashboard with system overview
- Event timeline with filtering
- Action history and approval queue
- "Ask OptAware" chat interface
- Settings management

## Core Principles
1. **Modular** — No monolithic files; everything is a package
2. **Resumable** — Each phase committed independently
3. **Observable** — Structured logging with correlation IDs everywhere
4. **Safe** — Dry-run by default, approval workflows, rollback capability
5. **Extensible** — New services added via manifest + plugin pattern
