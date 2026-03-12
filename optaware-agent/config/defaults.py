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
        "provider": "anthropic",
        "model": "claude-opus-4-5",
        "api_key": "",
        "max_tokens": 4096,
        "temperature": 0.2,
        "cost_limit_daily": 10.0,
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
}
