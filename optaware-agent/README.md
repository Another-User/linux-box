# OptAware

AI-powered autonomous agent for monitoring, managing, and optimizing Linux server infrastructure. OptAware watches your services, detects anomalies, reasons about issues using LLMs, and takes corrective action — with configurable approval workflows and full audit trails.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        OptAware Agent                           │
├──────────┬──────────┬──────────┬──────────┬─────────────────────┤
│ Observer │ Planner  │ Executor │ Auditor  │   Coordinator       │
│ (R/O)   │ (no sh)  │ (sudo)   │ (append) │   (orchestrator)    │
├──────────┴──────────┴──────────┴──────────┴─────────────────────┤
│                    IPC (Unix Domain Sockets)                    │
├─────────────────────────────────────────────────────────────────┤
│  Perception  │  Cognition  │  Planning  │  Knowledge  │ Services│
│  - metrics   │  - LLM API  │  - planner │  - Qdrant   │ - 18    │
│  - logs      │  - prompts  │  - approve │  - RAG      │   plugins│
│  - anomaly   │  - parse    │  - execute │  - incident │ - health │
│  - integrity │  - cost     │  - rollback│    memory   │   checks │
├─────────────────────────────────────────────────────────────────┤
│  Config (YAML + Pydantic v2)  │  REST API (FastAPI)  │  Portal  │
└─────────────────────────────────────────────────────────────────┘
```

## Quick Start

```bash
# Install
pip install -e .

# Interactive setup wizard
python setup_wizard.py

# Run in standalone (monolithic) mode
optaware --config config/optaware.yaml --foreground

# Or use Docker
docker-compose -f docker/docker-compose.yml up
```

## Multi-Agent Mode

OptAware can run each role as a separate OS process under dedicated service accounts for security isolation.

### Agent Roles

| Agent | Account | Access | Purpose |
|-------|---------|--------|---------|
| **Coordinator** | `optaware` | Medium | Orchestrates agents, API/portal |
| **Observer** | `optaware-observer` | Read-only | Metrics, logs, anomaly detection |
| **Planner** | `optaware-planner` | No shell | LLM reasoning, action plans |
| **Executor** | `optaware-executor` | Scoped sudo | Runs approved commands only |
| **Auditor** | `optaware-auditor` | Append-only | Immutable audit trail |

### Setup

```bash
# Create service accounts, directories, systemd units
sudo bash deploy/setup-agents.sh

# Enable multi-agent mode in config
# Set agents.enabled: true and agents.signing_secret in optaware.yaml

# Start all agents
sudo systemctl start optaware-coordinator
```

### Security Model

- **OS-enforced isolation**: Each agent runs as a separate Linux user
- **Signed action plans**: Executor only accepts HMAC-SHA256 signed plans from the coordinator
- **Sudoers allowlist**: Executor can only run commands explicitly permitted in `/etc/sudoers.d/optaware-executor`
- **Systemd hardening**: `NoNewPrivileges`, `ProtectSystem`, `CapabilityBoundingSet` per unit
- **Append-only audit**: Auditor writes immutable JSON-lines log

## API Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/api/health` | No | Liveness probe |
| GET | `/api/status` | Yes | System overview |
| GET | `/api/services` | Yes | List services |
| GET | `/api/services/{name}` | Yes | Service detail |
| POST | `/api/services/{name}/restart` | Yes | Restart service |
| GET | `/api/events` | Yes | Event list (filterable) |
| GET | `/api/actions` | Yes | Action history |
| POST | `/api/actions/{id}/approve` | Yes | Approve action |
| POST | `/api/actions/{id}/deny` | Yes | Deny action |
| POST | `/api/ask` | Yes | Ask LLM a question |
| GET | `/api/metrics` | Yes | System metrics |
| GET | `/api/config` | Yes | Config (redacted) |
| WS | `/ws/events` | No | Real-time event stream |

Authentication: `X-API-Key` header or JWT Bearer token.

## CLI

```bash
optaware status                     # System overview
optaware services list              # List managed services
optaware services restart nginx     # Restart a service
optaware events --severity error    # View error events
optaware actions list               # Pending actions
optaware actions approve <id>       # Approve an action
optaware ask "Why is nginx down?"   # Ask the AI
optaware config validate            # Validate config
```

## Configuration

Config file: `/etc/optaware/optaware.yaml` (or `config/optaware.yaml` for development).

Key sections:
- **general**: hostname, environment, data_dir, log_level
- **services**: core and elective service lists
- **llm**: provider (anthropic/openai/local), model, API key, cost limits
- **perception**: metric interval, anomaly sensitivity, log watching
- **planning**: approval mode (auto/manual/hybrid), dry-run default
- **knowledge**: Qdrant URL, embedding model
- **agents**: multi-agent mode enable, per-role config, signing secret

## Project Structure

```
optaware-agent/
├── agents/          # Multi-agent IPC, identity, roles
├── cli/             # Click CLI + FastAPI REST API
├── cognition/       # LLM providers, prompts, cost tracking
├── config/          # Pydantic schemas, YAML loader
├── deploy/          # systemd units, sudoers, setup script
├── knowledge/       # Qdrant vector store, RAG pipeline
├── models/          # Event, Alert, Action, Service models
├── perception/      # Metrics, logs, anomaly, integrity
├── planning/        # Action planner, executor, rollback, audit
├── portal/          # Web dashboard (Jinja2 + CSS + JS)
├── services/        # Service registry, health, 18 plugins
├── tests/           # Unit tests
├── daemon.py        # Main daemon entry point
├── scheduler.py     # Async task scheduler
├── event_processor.py  # Pub/sub event bus
└── logging_setup.py # Structured JSON logging
```

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
python -m pytest tests/ -v

# Syntax check
python -c "import compileall; compileall.compile_dir('.', quiet=1)"
```

## License

See LICENSE file.
