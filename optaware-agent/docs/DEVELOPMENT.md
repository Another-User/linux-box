# OptAware Development Guide

This document is the primary reference for developers working on the OptAware
agent. It covers the project layout, architecture, how to extend the system
with new services, configuration loading rules, and how to run tests.

---

## Table of Contents

1. [Project Structure](#project-structure)
2. [Architecture Overview](#architecture-overview)
3. [How to Add a New Service](#how-to-add-a-new-service)
4. [Configuration Guide](#configuration-guide)
5. [Testing Instructions](#testing-instructions)
6. [Development Environment Setup](#development-environment-setup)
7. [Coding Standards](#coding-standards)

---

## Project Structure

```
optaware-agent/
│
├── config/                      # Configuration management
│   ├── __init__.py              # Exports: OptAwareConfig, load_config, get_config
│   ├── schema.py                # Pydantic models for the config schema
│   ├── loader.py                # YAML loader with env-var overlay support
│   ├── validator.py             # Cross-field validation logic
│   ├── defaults.py              # Fallback default values
│   ├── optaware.yaml            # Default config template (shipped with the agent)
│   ├── service_manifest.yaml    # Registry of all 30+ managed services
│   └── data_map.yaml            # LLM-readable index of every data location
│
├── models/                      # Pydantic domain models (shared across layers)
│   ├── __init__.py              # Re-exports all model classes
│   ├── events.py                # Event, EventSeverity, EventSource
│   ├── alerts.py                # Alert, AlertStatus, AlertRule
│   ├── actions.py               # Action, ActionStep, ActionType, ActionStatus
│   ├── services.py              # ServiceInfo, ServiceStatus, ServiceType
│   └── knowledge.py             # KnowledgeDocument, DocumentType, SearchResult
│
├── services/                    # Service lifecycle management
│   ├── __init__.py              # Exports: ServiceRegistry, DependencyResolver, etc.
│   ├── registry.py              # Singleton registry — loads service_manifest.yaml
│   ├── dependency.py            # Topological dependency resolver
│   ├── health.py                # Health checkers: systemd, docker, TCP port, command
│   ├── manager.py               # start / stop / restart / reload via systemctl
│   └── manifest_loader.py       # Parses service_manifest.yaml into ServiceInfo objects
│
├── perception/                  # Phase 2 — Observability
│   ├── __init__.py
│   ├── log_watcher.py           # inotify-based log tail with pattern matching
│   ├── metric_collector.py      # Prometheus/node_exporter metric scraper
│   ├── event_emitter.py         # Normalises observations into Event objects
│   └── anomaly_detector.py      # Threshold and ML-based anomaly detection
│
├── cognition/                   # Phase 3 — LLM reasoning engine
│   ├── __init__.py
│   ├── llm_client.py            # Unified client for Anthropic / OpenAI / Ollama
│   ├── prompt_builder.py        # Constructs structured prompts with context
│   ├── response_parser.py       # Parses LLM output into Action / Analysis objects
│   ├── context_loader.py        # Injects data_map + service_manifest into context
│   └── cost_tracker.py          # Token counting and daily cost enforcement
│
├── planning/                    # Phase 4 — Action planning and execution
│   ├── __init__.py
│   ├── planner.py               # Selects and sequences actions from LLM proposals
│   ├── executor.py              # Executes approved actions with rollback support
│   ├── approval_gate.py         # Approval workflow (auto / manual / hybrid)
│   ├── risk_scorer.py           # Assigns a risk score to each proposed action
│   └── rollback.py              # Stores pre-change snapshots and restores them
│
├── knowledge/                   # Phase 5 — RAG knowledge base
│   ├── __init__.py
│   ├── vector_store.py          # Qdrant client wrapper (upsert, search, delete)
│   ├── embedder.py              # Sentence-transformer embedding pipeline
│   ├── indexer.py               # Crawls config files and man pages for indexing
│   └── retriever.py             # Retrieves relevant documents for LLM context
│
├── portal/                      # Phase 9 — FastAPI web dashboard
│   ├── __init__.py
│   ├── app.py                   # FastAPI application factory
│   ├── routes/
│   │   ├── events.py            # GET /api/events
│   │   ├── actions.py           # GET/POST /api/actions
│   │   ├── services.py          # GET /api/services
│   │   └── ask.py               # POST /api/ask (natural-language queries)
│   └── static/                  # Frontend assets
│
├── cli/                         # Phase 8 — Typer CLI
│   ├── __init__.py
│   ├── app.py                   # Root Typer application
│   ├── commands/
│   │   ├── start.py             # optaware start
│   │   ├── stop.py              # optaware stop
│   │   ├── status.py            # optaware status
│   │   ├── ask.py               # optaware ask "<question>"
│   │   ├── run.py               # optaware run <action>
│   │   └── config.py            # optaware config show/set/validate
│
├── docker/                      # Container deployment
│   ├── Dockerfile               # Multi-stage build for the agent
│   └── docker-compose.yml       # Compose stack: agent + qdrant + prometheus
│
├── tests/                       # Test suite (pytest)
│   ├── unit/                    # Unit tests (no external dependencies)
│   │   ├── test_config.py
│   │   ├── test_models.py
│   │   ├── test_services.py
│   │   └── test_risk_scorer.py
│   ├── integration/             # Integration tests (requires running services)
│   │   ├── test_llm_client.py
│   │   ├── test_vector_store.py
│   │   └── test_service_manager.py
│   ├── fixtures/                # Shared pytest fixtures and factory helpers
│   └── conftest.py              # Root conftest — shared fixtures and mocks
│
├── docs/
│   └── DEVELOPMENT.md           # This file
│
├── daemon.py                    # Main async daemon entry point
├── logging_setup.py             # Structured JSON logging (structlog)
├── scheduler.py                 # Async task scheduler (APScheduler)
├── event_processor.py           # Central event bus (asyncio queue)
├── setup_wizard.py              # Interactive first-run configuration wizard
├── pyproject.toml               # Package metadata and dependency declaration
├── CHANGELOG.md                 # Version history
└── PLAN.md                      # Phase-by-phase implementation roadmap
```

---

## Architecture Overview

OptAware is structured as a layered agent. Each layer has a clearly defined
role and communicates with adjacent layers through typed interfaces.

```
  ╔═══════════════════════════════════════════════════════════════╗
  ║                    WEB PORTAL  (Phase 9)                      ║
  ║         Dashboard · Events · Action Log · Ask Interface       ║
  ╠═══════════════════════════════════════════════════════════════╣
  ║                  CLI & REST API  (Phase 8)                    ║
  ║      optaware start/stop/ask/run/status  ·  /api/*            ║
  ╠═══════════════╦═══════════════╦═══════════════════════════════╣
  ║   PLANNING    ║   COGNITION   ║         KNOWLEDGE             ║
  ║  (Phase 4)    ║   (Phase 3)   ║          (Phase 5)            ║
  ║               ║               ║                               ║
  ║  Planner      ║  LLM Client   ║  Vector Store (Qdrant)        ║
  ║  Executor     ║  Prompt Build ║  Embedder                     ║
  ║  Approval     ║  Response     ║  Indexer                      ║
  ║  Risk Scorer  ║  Parser       ║  Retriever                    ║
  ║  Rollback     ║  Cost Track   ║                               ║
  ╠═══════════════╩═══════════════╩═══════════════════════════════╣
  ║                    PERCEPTION  (Phase 2)                      ║
  ║          Log Watcher · Metric Collector · Anomaly Detector    ║
  ╠═══════════════════════════════════════════════════════════════╣
  ║                 CORE SKELETON  (Phase 1)                      ║
  ║     Config · Models · Service Registry · Daemon · Logging     ║
  ╠═══════════════════════════════════════════════════════════════╣
  ║             CONTAINER RUNTIME  (Phase 7)                      ║
  ║          Docker Compose · Named Volumes · Networking          ║
  ╠═══════════════════════════════════════════════════════════════╣
  ║              LINUX HOST  (managed services)                   ║
  ║  DNS · DHCP · SSH · Firewall · NTP · Certs · Web · Mail · …   ║
  ╚═══════════════════════════════════════════════════════════════╝
```

### Data flow for an automated remediation

```
Linux host emits a log line
        │
        ▼
  LogWatcher (perception)
        │  Event object
        ▼
  EventProcessor (event bus)
        │  Event
        ▼
  AnomalyDetector ──► no anomaly ──► discard
        │  anomaly detected
        ▼
  PromptBuilder (cognition)
        │  builds context: event + relevant docs from vector store
        ▼
  LLMClient.complete()
        │  structured response (proposed actions)
        ▼
  ResponseParser ──► extract Action list
        │
        ▼
  RiskScorer ──► assign risk score to each action
        │
        ▼
  ApprovalGate
    ├── low risk + auto mode ──► Executor.run()
    └── high risk / manual mode ──► notify admin, wait for approval
                                         │
                                         ▼
                                   Executor.run()
                                         │
                                         ▼
                                   Rollback.snapshot() ── on failure ──► restore
```

---

## How to Add a New Service

Adding a new service to OptAware requires changes in two files. No Python
code needs to be written unless you need custom health-check or remediation
logic.

### Step 1 — Add the service to `config/service_manifest.yaml`

Each service entry must include the following fields:

```yaml
- name: my_service                          # unique snake_case identifier
  display_name: "My Service (daemon name)"  # human-readable label
  type: elective                            # core | elective
  systemd_unit: my-daemon.service           # exact unit file name
  package: my-package                       # apt/yum package name
  default_port: 9999                        # primary listening port, or null
  protocols: [tcp]                          # tcp | udp | []
  config_paths:
    main: /etc/my-service/config.yaml       # key: description, value: path
    extra_dir: /etc/my-service/conf.d
  log_paths:
    default: /var/log/my-service/daemon.log
    syslog: /var/log/syslog
  data_dirs:
    data: /var/lib/my-service
    run: /var/run/my-service
  pid_file: /var/run/my-service/my-service.pid   # or null
  socket_paths:
    control: /var/run/my-service/control.sock     # or empty list []
  dependencies: [firewall]                # list of service names this depends on
  health_check_cmd: "my-service status"   # command to verify the service is healthy
  reload_cmd: "systemctl reload my-daemon"
  description: >
    One-paragraph description of what this service does, why it exists,
    and any key operational considerations.
  risk_level: medium                      # low | medium | high | critical
  tags: [networking, example]
```

### Step 2 — Add data locations to `config/data_map.yaml`

Under the `by_service` section, add an entry keyed by your service name:

```yaml
by_service:
  my_service:
    description: "Brief description of the service"
    config:
      - path: /etc/my-service/config.yaml
        description: "Main configuration file"
        format: yaml
        writable: true
    logs:
      - path: /var/log/my-service/daemon.log
        description: "Primary log — startup and errors"
        format: text
        rotation: daily
    data:
      - path: /var/lib/my-service
        description: "Persistent data directory"
        format: directory
    runtime:
      - path: /var/run/my-service/my-service.pid
        description: "PID file"
        format: pid
```

If the service has security-sensitive paths (keys, passwords), add them to
the `by_data_type.security_sensitive_paths` list as well.

### Step 3 — Reload the service registry

No code changes are needed. The `ServiceRegistry` reads `service_manifest.yaml`
at startup and whenever `optaware config reload` is called:

```bash
optaware config reload
```

### Step 4 — (Optional) Custom health-check or remediation logic

For services with non-standard health checks, subclass `BaseHealthChecker`
in `services/health.py`:

```python
# services/health.py
class MyServiceHealthChecker(CommandHealthChecker):
    """Custom health check for My Service."""

    service_name = "my_service"

    def interpret_output(self, returncode: int, output: str) -> HealthStatus:
        if "OK" in output and returncode == 0:
            return HealthStatus.HEALTHY
        return HealthStatus.UNHEALTHY
```

Register it in `services/registry.py` by adding it to the
`CUSTOM_HEALTH_CHECKERS` dict.

---

## Configuration Guide

### Loading precedence (highest to lowest)

1. **Environment variables** — any `OPTAWARE_*` variable overrides the
   corresponding config key. Dots in keys are replaced with underscores:
   `OPTAWARE_LLM_MODEL` overrides `optaware.llm.model`.

2. **CLI flag** — `optaware --config /path/to/custom.yaml` loads that file
   instead of the default path.

3. **System config file** — `/etc/optaware/optaware.yaml` is the standard
   runtime location. Created by `setup_wizard.py`.

4. **Built-in defaults** — defined in `config/defaults.py` and mirrored in
   `config/optaware.yaml`. Always present as a fallback.

### Key configuration sections

| Section | Purpose |
|---------|---------|
| `general` | Hostname, environment tag, data directory, log level |
| `services` | Which core/elective services are managed; auto_start flag |
| `llm` | Provider, model, API key (prefer env var), cost limits |
| `perception` | Log watching, metric interval, anomaly sensitivity |
| `planning` | Approval mode, dry-run default, max concurrent actions |
| `knowledge` | Qdrant URL, embedding model, index name |
| `docker` | Socket path, compose file, persistent volume mounts |
| `portal` | Enable/disable, host, port, auto-generated secret key |

### Sensitive values

Never commit API keys or secrets to version control. Use one of:

- **Environment variable**: `export OPTAWARE_LLM_API_KEY=sk-…`
- **Secrets file**: `/etc/optaware/secrets.env` (chmod 600, root-owned),
  loaded automatically by the daemon on startup.

The `api_key` field in `optaware.yaml` should be left empty (`""`).

### Validating the configuration

```bash
optaware config validate
```

This runs the Pydantic schema validation and reports any errors without
starting the daemon.

---

## Testing Instructions

### Prerequisites

```bash
pip install -e ".[dev]"
# installs: pytest, pytest-asyncio, pytest-cov, pytest-mock, httpx, factory-boy
```

### Run the full test suite

```bash
pytest tests/
```

### Run only unit tests (fast, no external dependencies)

```bash
pytest tests/unit/
```

### Run only integration tests

Integration tests require a running Qdrant instance and optionally a local
LLM (Ollama). The recommended way is to spin up the test stack first:

```bash
docker compose -f docker/docker-compose.yml up -d qdrant
pytest tests/integration/ -v
```

### Run with coverage report

```bash
pytest tests/ --cov=. --cov-report=html --cov-report=term-missing
open htmlcov/index.html
```

### Run a single test file

```bash
pytest tests/unit/test_risk_scorer.py -v
```

### Run tests matching a keyword

```bash
pytest -k "health_check" -v
```

### Linting and type checking

```bash
# Formatting (black)
black .

# Import sorting (isort)
isort .

# Type checking (mypy)
mypy . --ignore-missing-imports

# Linting (ruff)
ruff check .
```

### Pre-commit hooks

The repository ships a `.pre-commit-config.yaml` that runs black, isort, ruff,
and mypy automatically on every commit:

```bash
pre-commit install        # install hooks once
pre-commit run --all-files  # run manually
```

---

## Development Environment Setup

### 1. Clone and create a virtual environment

```bash
git clone <repo-url> optaware-agent
cd optaware-agent
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install the package in editable mode with dev dependencies

```bash
pip install -e ".[dev]"
```

### 3. Run the setup wizard (creates /etc/optaware/optaware.yaml)

```bash
sudo python3 setup_wizard.py
# or for local testing without root:
python3 setup_wizard.py   # will offer to save to ./optaware.yaml
```

### 4. Start supporting services via Docker Compose

```bash
docker compose -f docker/docker-compose.yml up -d
```

This starts:
- **Qdrant** on port 6333 (vector knowledge base)
- **Prometheus** on port 9090 (metrics)
- **Grafana** on port 3000 (dashboards)

### 5. Start the daemon in foreground mode

```bash
export OPTAWARE_LLM_API_KEY=sk-…
python3 daemon.py --foreground --config ./optaware.yaml
```

### 6. Open the web portal

```
http://localhost:8080
```

---

## Coding Standards

- **Python version**: 3.11+
- **Type hints**: required on all public functions and methods
- **Docstrings**: Google-style docstrings for all public classes and functions
- **Models**: use Pydantic v2 for all data models
- **Async**: use `asyncio` throughout the daemon; avoid blocking calls in
  async contexts (use `asyncio.to_thread` for blocking I/O)
- **Logging**: use `structlog` with JSON output; never use `print()` in
  production code
- **Error handling**: raise typed exceptions (defined in `exceptions.py`);
  never swallow exceptions silently
- **Testing**: every public function must have at least one unit test;
  aim for 80%+ coverage
- **Commits**: follow Conventional Commits (`feat:`, `fix:`, `docs:`, etc.)
