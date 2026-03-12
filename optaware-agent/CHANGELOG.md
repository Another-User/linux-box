# Changelog

All notable changes to OptAware will be documented in this file.

## [0.1.0] - 2026-03-12

### Phase 0 — Plan & Scaffold
- Created project plan document (PLAN.md)
- Established directory structure
- Initial repository setup

### Phase 1 — Core Skeleton
- Added `config/` package — YAML-based hierarchical configuration with Pydantic validation
- Added `models/` package — Pydantic data models for events, alerts, actions, services, knowledge
- Added `services/` package — service registry, dependency resolver, health checks, service manager
- Added `daemon.py` — main daemon loop with signal handling and asyncio
- Added `logging_setup.py` — structured JSON logging with correlation IDs
- Added `scheduler.py` — cron-like async task scheduler
- Added `event_processor.py` — central event bus with severity-based routing
- Added `setup_wizard.py` — interactive first-run configuration wizard
- Added `optaware.yaml` — default configuration template
- Added `service_manifest.yaml` — registry of 30+ managed services
- Added `data_map.yaml` — LLM-readable index of all data locations
- Added `pyproject.toml` — Python package definition
