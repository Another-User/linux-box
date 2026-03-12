"""Pydantic configuration schema models for OptAware agent."""

from pydantic import BaseModel, ConfigDict, Field


class GeneralConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    hostname: str = "localhost"
    environment: str = Field(default="dev", pattern="^(dev|staging|prod)$")
    data_dir: str = "/data"
    log_level: str = Field(default="INFO", pattern="^(DEBUG|INFO|WARNING|ERROR|CRITICAL)$")


class ServicesConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    core_services: list[str] = Field(default_factory=list)
    elective_services: list[str] = Field(default_factory=list)
    auto_start: bool = False


class LLMConfig(BaseModel):
    """LLM provider configuration.

    When ``provider`` is ``"auto"`` (default), OptAware detects available
    hardware (GPUs, local LLM services) and selects the best provider at
    startup.  The fallback chain is:
      local GPU service → local CPU → Anthropic API → OpenAI API.
    """

    model_config = ConfigDict(extra="ignore")

    provider: str = Field(
        default="auto",
        pattern="^(auto|anthropic|openai|local)$",
    )
    model: str = ""
    api_key: str = ""
    max_tokens: int = Field(default=4096, ge=1, le=200000)
    temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    cost_limit_daily: float = Field(default=10.0, ge=0.0)

    # Auto-detection settings
    local_url: str = ""
    fallback_provider: str = Field(
        default="anthropic",
        pattern="^(anthropic|openai)$",
    )
    fallback_api_key: str = ""


class PerceptionConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    log_watch_enabled: bool = True
    metric_interval_sec: int = Field(default=30, ge=1)
    anomaly_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)


class PlanningConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    approval_mode: str = Field(default="manual", pattern="^(auto|manual|hybrid)$")
    dry_run_default: bool = True
    max_concurrent_actions: int = Field(default=3, ge=1)


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    vector_store_url: str = "http://localhost:6333"
    embedding_model: str = "text-embedding-3-small"
    index_name: str = "optaware"


class DockerConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    socket_path: str = "/var/run/docker.sock"
    compose_file: str = "docker-compose.yml"
    persistent_volumes: dict[str, str] = Field(default_factory=dict)


class PortalConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    host: str = "0.0.0.0"
    port: int = Field(default=8080, ge=1, le=65535)
    secret_key: str = ""


class AgentRoleConfig(BaseModel):
    """Per-agent-role configuration."""

    model_config = ConfigDict(extra="ignore")

    enabled: bool = True
    socket_path: str = ""
    user: str = ""
    group: str = "optaware"
    allowed_commands: list[str] = Field(default_factory=list)


class AgentsConfig(BaseModel):
    """Multi-agent service account configuration.

    When ``enabled`` is ``False`` (default), OptAware runs in monolithic
    single-process mode.  Set to ``True`` to run each role as a separate
    process under a dedicated Linux service account.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    socket_dir: str = "/run/optaware"
    signing_secret: str = ""
    coordinator: AgentRoleConfig = Field(default_factory=AgentRoleConfig)
    observer: AgentRoleConfig = Field(default_factory=AgentRoleConfig)
    planner: AgentRoleConfig = Field(default_factory=AgentRoleConfig)
    executor: AgentRoleConfig = Field(default_factory=AgentRoleConfig)
    auditor: AgentRoleConfig = Field(default_factory=AgentRoleConfig)


class OptAwareConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    general: GeneralConfig = Field(default_factory=GeneralConfig)
    services: ServicesConfig = Field(default_factory=ServicesConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    perception: PerceptionConfig = Field(default_factory=PerceptionConfig)
    planning: PlanningConfig = Field(default_factory=PlanningConfig)
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)
    docker: DockerConfig = Field(default_factory=DockerConfig)
    portal: PortalConfig = Field(default_factory=PortalConfig)
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
