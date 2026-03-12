"""Tests for configuration loading and validation."""

from pathlib import Path

from config.defaults import DEFAULT_CONFIG
from config.schema import OptAwareConfig, GeneralConfig, LLMConfig


class TestDefaults:
    def test_default_config_has_all_sections(self):
        assert "general" in DEFAULT_CONFIG
        assert "services" in DEFAULT_CONFIG
        assert "llm" in DEFAULT_CONFIG
        assert "perception" in DEFAULT_CONFIG
        assert "planning" in DEFAULT_CONFIG
        assert "knowledge" in DEFAULT_CONFIG
        assert "docker" in DEFAULT_CONFIG
        assert "portal" in DEFAULT_CONFIG

    def test_default_general(self):
        general = DEFAULT_CONFIG["general"]
        assert general["data_dir"] == "/data"
        assert general["log_level"] == "INFO"


class TestSchema:
    def test_general_config(self):
        config = GeneralConfig(
            hostname="testhost",
            environment="dev",
            data_dir="/data",
            log_level="DEBUG",
        )
        assert config.hostname == "testhost"
        assert config.environment == "dev"

    def test_llm_config(self):
        config = LLMConfig(
            provider="anthropic",
            model="claude-sonnet-4-20250514",
            api_key="test-key",
        )
        assert config.provider == "anthropic"
        assert config.max_tokens == 4096  # default
