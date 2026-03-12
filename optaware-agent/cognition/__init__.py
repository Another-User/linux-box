"""
OptAware Cognition Layer — LLM integration, prompt engineering, and response parsing.
"""

from cognition.prompt_engine import PromptEngine
from cognition.response_parser import ResponseParser
from cognition.context_manager import ContextManager
from cognition.llm_provider import LLMProvider, AnthropicProvider, OpenAIProvider, LocalProvider, get_provider
from cognition.cost_tracker import CostTracker
from cognition.hardware_detector import HardwareProfile, detect_hardware
from cognition.provider_resolver import resolve_provider, get_hardware_profile, get_provider_status

__all__ = [
    "PromptEngine",
    "ResponseParser",
    "ContextManager",
    "LLMProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "LocalProvider",
    "get_provider",
    "CostTracker",
    "HardwareProfile",
    "detect_hardware",
    "resolve_provider",
    "get_hardware_profile",
    "get_provider_status",
]
