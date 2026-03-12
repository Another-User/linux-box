# OptAware Development Guide

## Project Structure

```
optaware-agent/
├── config/                 # Configuration management
│   ├── __init__.py         # Exports: OptAwareConfig, load_config, get_config
│   ├── schema.py           # Pydantic config models
│   ├── loader.py           # YAML config loader with overlay support
│   ├── validator.py        # Configuration validation
│   ├── defaults.py         # Default configuration values
│   └── optaware.yaml       # Default config template
├── models/                 # Pydantic data models
│   ├── __init__.py         # Exports all model classes
│   ├── events.py           # Event, EventSeverity, EventSource
│   ├── alerts.py           # Alert, AlertStatus
│   ├── actions.py          # Action, ActionStep, ActionType, ActionStatus
│   ├── services.py         # ServiceInfo, ServiceStatus, ServiceType
│   └── knowledge.py        # KnowledgeDocument, DocumentType
├── services/               # Service management
│   ├── __init__.py         # Exports: ServiceRegistry, DependencyResolver, etc.
│   ├── registry.py         # Service registry (singleton)
│   ├── dependency.py       # Dependency resolver (topological sort)
│   ├── health.py           # Health checker (systemd, docker, port, command)
│   ├── manager.py          # Service manager (start/stop/restart)
│   └── manifest_loader.py  # Load services from YAML manifest
├── perception/             # Phase 2: Log watchers, metric collectors
├── cognition/              # Phase 3: LLM prompt engine, response parser
├── planning/               # Phase 4: Action planner, executor, rollback
├── knowledge/              # Phase 5: Vector store, RAG pipeline
├── portal/                 # Phase 9: Web dashboard
├── cli/                    # Phase 8: CLI commands
├── docker/                 # Phase 7: Dockerfile, compose
├── tests/                  # Test suite
├── docs/                   # Documentation
├── daemon.py               # Main daemon entry point
├── logging_setup.py        # Structured JSON logging
├── scheduler.py            # Async task scheduler
├── event_processor.py      # Central event bus
├── setup_wizard.py         # First-run configuration wizard
├── data_map.yaml           # LLM-readable data location index
├── service_manifest.yaml   # Service definitions (in config/)
├── pyproject.toml          # Package definition
└── CHANGELOG.md            # Version history
```

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

## How to Add a New Service

1. Add the service definition to `config/service_manifest.yaml`:
   ```yaml
   - name: my_service
     display_name: "My Service"
     systemd_unit: my-service
     config_paths:
       - /etc/my-service/config.yaml
     log_paths:
       - /var/log/my-service/
     dependencies:
       - name: dns
         required: false
     health_check_cmd: "my-service --status"
     port: 9999
   ```

2. Add data locations to `data_map.yaml`

3. The service will be automatically discovered by the registry on next reload

## Configuration

Configuration is loaded from YAML with this precedence (highest to lowest):
1. Environment variables (`OPTAWARE_*`)
2. Config file specified via `--config` flag
3. `/etc/optaware/optaware.yaml`
4. Built-in defaults (`config/defaults.py`)

## Running

```bash
# First-run setup
python setup_wizard.py

# Start the daemon (foreground)
python daemon.py --foreground

# Start with custom config
python daemon.py --config /path/to/optaware.yaml

# Install as package
pip install -e .
optaware --help
```

## Testing

```bash
# Run all tests
pytest tests/

# Run with coverage
pytest tests/ --cov=. --cov-report=html
```
