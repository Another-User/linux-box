# Changelog

All notable changes to OptAware are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Version numbers follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Phase 11 — Multi-Agent Service Account Architecture

This phase adds OS-enforced role separation so each agent component runs
under a dedicated Linux service account with least-privilege access.

#### Added

**Agent framework** (`agents/`)

- `agents/identity.py` — `AgentRole` enum (coordinator, observer, planner,
  executor, auditor), `AgentIdentity` frozen dataclass, `Permission` enum
  with 10 granular permissions, `ROLE_PERMISSIONS` matrix mapping each role
  to its allowed operations, `get_identity()` factory.

- `agents/protocol.py` — `MessageType` enum (14 IPC message types),
  `Message` dataclass with JSON serialisation, `sign_message()` and
  `verify_signature()` using HMAC-SHA256 for tamper-proof action plans.

- `agents/ipc.py` — `IPCServer` and `IPCClient` using async Unix domain
  sockets with 4-byte length-prefixed framing.  Server sets socket
  permissions to 0660.  Client supports auto-reconnect with exponential
  backoff (1s/2s/4s).  Async context manager interface.

- `agents/runner.py` — Per-role main loops: `run_coordinator` (IPC hub,
  message routing), `run_observer` (psutil metrics, forwards to coordinator),
  `run_planner` (listens for diagnosis requests), `run_executor` (validates
  signed plans against allowlist, delegates to `planning.executor`),
  `run_auditor` (append-only audit trail).  `drop_privileges()` for
  setuid/setgid after socket binding.  CLI entry point:
  `python -m agents.runner --role <role>`.

**Deployment** (`deploy/`)

- `deploy/systemd/optaware-{coordinator,observer,planner,executor,auditor}.service`
  — Five systemd unit files with security hardening: `NoNewPrivileges`,
  `ProtectSystem=strict`, `CapabilityBoundingSet`, `ReadOnlyPaths` /
  `ReadWritePaths` scoped per role.  Coordinator uses `Wants=` to pull in
  all agent units.

- `deploy/sudoers.d/optaware-executor` — Scoped sudo rules restricting the
  executor to systemctl start/stop/restart/reload/status, docker
  start/stop/restart, and read-only diagnostics.  Explicitly denies rm, dd,
  mkfs, chmod 777, iptables -F.

- `deploy/setup-agents.sh` — Bash script that creates the `optaware` system
  group, five service accounts, directories with correct ownership and
  permissions, installs sudoers and systemd units.  Supports `--dry-run`.

**Documentation**

- `README.md` — Project overview with ASCII architecture diagram, quick
  start guide, multi-agent deployment instructions, agent roles table, API
  endpoint reference, CLI usage, configuration reference, project structure.

**Tests**

- `tests/test_agents.py` — Tests for `AgentRole` values, `ROLE_PERMISSIONS`
  enforcement (observer read-only, planner no-execute, executor no-LLM,
  auditor append-only), `AgentIdentity` immutability and serialisation,
  `Message` round-trip serialisation, HMAC sign/verify (valid, wrong secret,
  tampered payload, unsigned), IPC server-client round-trip over temp Unix
  socket, command allowlist exact and wildcard matching.

#### Changed

- `config/schema.py` — Added `AgentRoleConfig` and `AgentsConfig` Pydantic
  models.  Added `agents: AgentsConfig` field to `OptAwareConfig` (defaults
  to `enabled: false` for backward compatibility).

- `config/defaults.py` — Added `agents` section with per-role defaults
  including user accounts and executor command allowlist.

- `logging_setup.py` — Added `_agent_role_var` context variable alongside
  existing `_correlation_id_var`.  Added `get_agent_role()` /
  `set_agent_role()`.  `CorrelationIdFilter` now injects both
  `correlation_id` and `agent_role` into every log record.

#### Technical notes

- Multi-agent mode is opt-in: set `agents.enabled: true` in config.  When
  disabled (default), the monolithic `OptAwareDaemon` runs unchanged.
- Action plans sent to the executor must carry a valid HMAC-SHA256 signature
  produced using `agents.signing_secret`.  Plans with invalid or missing
  signatures are rejected.
- The executor validates every command against its `allowed_commands`
  allowlist before execution.  Wildcard patterns (e.g. `systemctl restart *`)
  are supported.
- IPC uses no external dependencies — pure asyncio Unix domain sockets.

---

## [0.1.0] — 2026-03-12

### Phase 0 — Project Planning and Scaffold

This phase established the project layout, planning documents, and the
foundational package structure before any runtime code was written.

#### Added

- `PLAN.md` — Phase-by-phase implementation roadmap covering Phases 0–9,
  with goals, deliverables, and acceptance criteria for each phase.
- Base directory tree for all planned packages:
  `config/`, `models/`, `services/`, `perception/`, `cognition/`,
  `planning/`, `knowledge/`, `portal/`, `cli/`, `docker/`, `tests/`, `docs/`.
- Empty `__init__.py` stubs in every package directory so that Python
  treats them as importable packages from day one.
- `pyproject.toml` — PEP 517/518 package definition with all runtime and
  development dependencies declared. Defines the `optaware` console script
  entry point pointing at `cli.app:app`.

---

### Phase 1 — Core Skeleton

This phase built the complete runtime foundation: configuration loading,
shared data models, the service registry, the async daemon loop, logging
infrastructure, and all supporting configuration files.

#### Added

**Configuration system** (`config/`)

- `config/optaware.yaml` — Canonical default configuration template shipped
  with the agent. Covers all top-level sections: `general`, `services`, `llm`,
  `perception`, `planning`, `knowledge`, `docker`, and `portal`. Every field
  has a sensible production default with inline comments explaining each option.

- `config/service_manifest.yaml` — Complete registry of every service the
  agent can manage. Defines 13 core services and 18 elective services (31
  total). Each entry specifies: `name`, `display_name`, `type`,
  `systemd_unit`, `package`, `default_port`, `protocols`, `config_paths`,
  `log_paths`, `data_dirs`, `pid_file`, `socket_paths`, `dependencies`,
  `health_check_cmd`, `reload_cmd`, `description`, `risk_level`, and `tags`.

  Core services defined: dns (bind9), dhcp (kea-dhcp4), firewall (nftables),
  vpn (wg-quick/WireGuard), web (nginx), mail_smtp (postfix),
  mail_imap (dovecot), database_pg (postgresql), monitoring (prometheus),
  backup (restic timer), ntp (chronyd), ssh (sshd), certificates (certbot).

  Elective services defined: samba, nfs, ldap, redis, docker_registry,
  load_balancer (haproxy), reverse_proxy, ftp (vsftpd), printing (cups),
  cron_manager, grafana, mysql (mariadb), elasticsearch, kibana, logstash,
  rabbitmq, memcached, fail2ban.

- `config/data_map.yaml` — LLM-readable comprehensive filesystem index.
  Two top-level sections:
  - `by_service` — every config file, log file, data directory, PID file,
    socket path, and cache directory for each managed service, plus the
    Linux OS itself and systemd.
  - `by_data_type` — cross-service views: `all_config_files`,
    `all_log_directories`, `all_unix_sockets`, `all_pid_files`,
    `all_data_directories`, `security_sensitive_paths` (with required
    permissions and ownership for each sensitive path).

- `config/schema.py` — Pydantic v2 models for the full configuration schema.
  Nested models for every section with field validation and description
  metadata for IDE support.

- `config/loader.py` — YAML config loader that merges the default template
  with the system config at `/etc/optaware/optaware.yaml` and then applies
  `OPTAWARE_*` environment-variable overrides. Returns a validated
  `OptAwareConfig` instance.

- `config/validator.py` — Cross-field validation (e.g., confirm that all
  services listed in `core_services` exist in the service manifest).

- `config/defaults.py` — Python constants mirroring the YAML defaults, used
  as the last-resort fallback in unit tests and when no config file is present.

**Data models** (`models/`)

- `models/events.py` — `Event`, `EventSeverity` (debug/info/warning/error/
  critical), `EventSource` (log_watcher/metric_collector/user/api).

- `models/alerts.py` — `Alert`, `AlertStatus` (firing/resolved/silenced),
  `AlertRule` with condition expression and notification channels.

- `models/actions.py` — `Action`, `ActionStep`, `ActionType`
  (systemctl_restart/config_edit/shell_command/api_call), `ActionStatus`
  (pending/approved/running/completed/failed/rolled_back).

- `models/services.py` — `ServiceInfo` (populated from service_manifest),
  `ServiceStatus` (active/inactive/failed/unknown), `ServiceType`
  (core/elective).

- `models/knowledge.py` — `KnowledgeDocument`, `DocumentType`
  (config_file/log_sample/man_page/runbook), `SearchResult` with
  relevance score.

**Service management** (`services/`)

- `services/registry.py` — Singleton `ServiceRegistry` that loads
  `service_manifest.yaml` at startup and provides `get()`, `list()`,
  `list_core()`, `list_elective()`, and `by_tag()` query methods.

- `services/dependency.py` — `DependencyResolver` implementing Kahn's
  topological sort algorithm. Detects circular dependencies and raises
  `CircularDependencyError`. Used by the planner to sequence start/stop
  operations correctly.

- `services/health.py` — `BaseHealthChecker` with four concrete implementations:
  - `SystemdHealthChecker` — calls `systemctl is-active <unit>`
  - `PortHealthChecker` — attempts a TCP connection to `host:port`
  - `CommandHealthChecker` — runs the service's `health_check_cmd`
  - `DockerHealthChecker` — queries the Docker socket for container status

- `services/manager.py` — `ServiceManager` wrapping `systemctl`
  (start/stop/restart/reload/enable/disable). Logs every operation to the
  audit log. Supports dry-run mode.

- `services/manifest_loader.py` — `ManifestLoader` that parses
  `service_manifest.yaml` and returns a list of `ServiceInfo` Pydantic
  objects, with validation that all referenced dependency names exist.

**Runtime infrastructure**

- `daemon.py` — `OptAwareDaemon` — the main `asyncio` event loop. Registers
  `SIGTERM`/`SIGINT` handlers for graceful shutdown. On startup: loads config,
  initialises the service registry, starts the scheduler, and launches the
  event processor. Writes a PID file to `/var/run/optaware/optaware.pid`.

- `logging_setup.py` — Configures `structlog` for structured JSON log output.
  All log entries include `timestamp`, `level`, `service`, `correlation_id`,
  and `event` fields. Configures both the structlog pipeline and the standard
  `logging` module so that third-party libraries' log output is captured.

- `scheduler.py` — `OptAwareScheduler` built on APScheduler. Manages
  recurring tasks (metric collection, health checks, knowledge re-indexing)
  with configurable intervals. Tasks are registered via decorator:
  `@scheduler.job(interval_seconds=30)`.

- `event_processor.py` — `EventProcessor` — an `asyncio.Queue`-based event
  bus. Listeners subscribe by severity or source. The processor routes events
  to: the anomaly detector, the audit log, and the portal's WebSocket
  broadcaster.

**Setup wizard**

- `setup_wizard.py` — Fully interactive 9-step first-run CLI wizard using the
  `rich` library. Steps:

  1. Welcome banner and readiness confirmation
  2. Hostname detection (auto-detects via `socket.gethostname()`) and
     environment/log-level selection
  3. Systemd unit scan (calls `systemctl list-unit-files`) to detect which
     managed services are installed, then displays a full inventory table
  4. Core service selection — table with installed/default indicators,
     comma-separated name input, validates against manifest
  5. Elective service selection — offers to pre-select detected installed
     services, validates input
  6. LLM provider and model selection — detects `OPTAWARE_LLM_API_KEY` in
     environment, supports anthropic/openai/ollama/azure, prompts for API key
     with password masking if not set, configures max_tokens and daily cost
     limit
  7. Data directory configuration — validates absolute path, shows
     subdirectories to be created
  8. Approval mode selection — table explaining auto/manual/hybrid modes, dry-
     run toggle, max concurrent actions
  9. Advanced options (opt-in) — Qdrant URL, embedding model, portal port/host,
     metric interval, anomaly sensitivity
  10. Config preview, write confirmation, YAML serialisation to
      `/etc/optaware/optaware.yaml` (mode 0640). Falls back to
      `./optaware.yaml` with instructions if permission is denied. Backs up
      any existing config before overwriting.
  11. Final summary table and next-steps panel

  Entry point: `run_wizard()` function (also executable as `__main__`).

**Documentation**

- `docs/DEVELOPMENT.md` — Comprehensive development guide covering:
  - Full annotated project structure tree
  - ASCII architecture diagram showing all layers
  - Data-flow diagram for automated remediation
  - Step-by-step guide for adding a new managed service
  - Configuration loading precedence and sensitive-value handling
  - Testing instructions (unit, integration, coverage, linting)
  - Development environment setup from scratch
  - Coding standards (Python version, type hints, async rules, logging,
    error handling, commit conventions)

#### Changed

- `setup_wizard.py` — Replaced the Phase 0 stub implementation (bare prompt
  loop with no validation, limited service list, no advanced options) with the
  full Phase 1 implementation described above. The `run_wizard()` function
  signature remains compatible — it takes no required arguments and returns
  `None`.

- `docs/DEVELOPMENT.md` — Replaced the Phase 0 skeleton (four sections,
  ~124 lines) with the full Phase 1 guide (~320 lines) including the
  architecture diagram, data-flow diagram, and expanded testing and
  standards sections.

#### Technical notes

- All new YAML files pass `yamllint` with the default strict profile.
- `setup_wizard.py` is compatible with Python 3.11+ and requires `rich>=13`
  and `PyYAML>=6`.
- The `service_manifest.yaml` schema version is `1.0`; future schema changes
  will increment this version and the loader will validate it.
- The `data_map.yaml` schema version is `1.0`.
- No database migrations are needed in Phase 1 (PostgreSQL is not yet
  initialised by the agent itself).

---

## [0.0.1] — 2026-03-01

### Phase 0 — Initial commit

#### Added

- Empty repository with `.gitignore` and initial `README.md` placeholder.
- `PLAN.md` — initial project planning document with high-level phase
  descriptions.
