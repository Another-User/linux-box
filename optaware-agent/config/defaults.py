"""Default configuration values for OptAware agent."""

DEFAULT_CONFIG: dict = {
    "general": {
        "hostname": "localhost",
        "environment": "dev",
        "data_dir": "/data",
        "log_level": "INFO",
    },
    "services": {
        "core_services": [],
        "elective_services": [],
        "auto_start": False,
    },
    "llm": {
        "provider": "auto",
        "model": "",
        "api_key": "",
        "max_tokens": 4096,
        "temperature": 0.2,
        "cost_limit_daily": 10.0,
        "local_url": "",
        "fallback_provider": "anthropic",
        "fallback_api_key": "",
    },
    "perception": {
        "log_watch_enabled": True,
        "metric_interval_sec": 30,
        "anomaly_sensitivity": 0.5,
    },
    "planning": {
        "approval_mode": "manual",
        "dry_run_default": True,
        "max_concurrent_actions": 3,
    },
    "knowledge": {
        "vector_store_url": "http://localhost:6333",
        "embedding_model": "text-embedding-3-small",
        "index_name": "optaware",
    },
    "docker": {
        "socket_path": "/var/run/docker.sock",
        "compose_file": "docker-compose.yml",
        "persistent_volumes": {},
    },
    "portal": {
        "enabled": False,
        "host": "0.0.0.0",
        "port": 8080,
        "secret_key": "",
    },
    "agents": {
        "enabled": False,
        "socket_dir": "/run/optaware",
        "signing_secret": "",
        "coordinator": {"enabled": True, "user": "optaware", "group": "optaware"},
        "observer": {"enabled": True, "user": "optaware-observer", "group": "optaware"},
        "planner": {"enabled": True, "user": "optaware-planner", "group": "optaware"},
        "executor": {"enabled": True, "user": "optaware-executor", "group": "optaware", "allowed_commands": [
            "systemctl start *",
            "systemctl stop *",
            "systemctl restart *",
            "systemctl reload *",
            "systemctl status *",
        ]},
        "auditor": {"enabled": True, "user": "optaware-auditor", "group": "optaware"},
    },
}
