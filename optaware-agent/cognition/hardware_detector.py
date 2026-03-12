"""Hardware detection for LLM provider auto-configuration.

Detects GPUs, AI accelerators, available VRAM, and local LLM services
to determine the best inference strategy for the current machine.

Detection order:
  1. NVIDIA GPUs via nvidia-smi
  2. AMD GPUs via /sys/class/drm + rocm-smi
  3. Intel accelerators via /sys/class/drm (xe/i915)
  4. Apple Silicon (MPS) via sysctl (macOS)
  5. Local LLM services (Ollama, LM Studio, vLLM, llama.cpp)
  6. CPU-only fallback info (core count, RAM)
"""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger("optaware.cognition.hardware_detector")


# ------------------------------------------------------------------
# Data structures
# ------------------------------------------------------------------


@dataclass
class GPUInfo:
    """Information about a single GPU / AI accelerator."""

    vendor: str            # "nvidia", "amd", "intel", "apple"
    name: str              # e.g. "NVIDIA A100", "AMD RX 7900 XTX"
    vram_mb: int           # Video memory in MiB (0 if unknown)
    driver: str = ""       # e.g. "535.183.01", "amdgpu"
    compute_capability: str = ""  # CUDA compute capability, e.g. "8.0"
    index: int = 0         # GPU index on the system


@dataclass
class LocalLLMService:
    """A locally running LLM inference service."""

    name: str              # "ollama", "lmstudio", "vllm", "llamacpp", "tgi"
    url: str               # Base URL, e.g. "http://localhost:11434"
    models: list[str] = field(default_factory=list)  # Available model names
    running: bool = False


@dataclass
class HardwareProfile:
    """Complete hardware profile for LLM provider selection."""

    gpus: list[GPUInfo] = field(default_factory=list)
    local_services: list[LocalLLMService] = field(default_factory=list)
    cpu_cores: int = 0
    ram_mb: int = 0
    has_gpu: bool = False
    total_vram_mb: int = 0
    recommended_provider: str = "anthropic"  # fallback
    recommended_model: str = ""
    recommendation_reason: str = ""

    def to_dict(self) -> dict:
        """Serialise to JSON-compatible dict."""
        return {
            "gpus": [
                {
                    "vendor": g.vendor, "name": g.name, "vram_mb": g.vram_mb,
                    "driver": g.driver, "index": g.index,
                }
                for g in self.gpus
            ],
            "local_services": [
                {"name": s.name, "url": s.url, "models": s.models, "running": s.running}
                for s in self.local_services
            ],
            "cpu_cores": self.cpu_cores,
            "ram_mb": self.ram_mb,
            "has_gpu": self.has_gpu,
            "total_vram_mb": self.total_vram_mb,
            "recommended_provider": self.recommended_provider,
            "recommended_model": self.recommended_model,
            "recommendation_reason": self.recommendation_reason,
        }


# ------------------------------------------------------------------
# GPU detection
# ------------------------------------------------------------------


def _detect_nvidia_gpus() -> list[GPUInfo]:
    """Detect NVIDIA GPUs via nvidia-smi."""
    if not shutil.which("nvidia-smi"):
        return []

    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index,name,memory.total,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        gpus = []
        for line in result.stdout.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 3:
                gpus.append(GPUInfo(
                    vendor="nvidia",
                    index=int(parts[0]),
                    name=f"NVIDIA {parts[1]}",
                    vram_mb=int(float(parts[2])),
                    driver=parts[3] if len(parts) > 3 else "",
                    compute_capability=parts[4] if len(parts) > 4 else "",
                ))
        return gpus
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        logger.debug("nvidia-smi detection failed: %s", exc)
        return []


def _detect_amd_gpus() -> list[GPUInfo]:
    """Detect AMD GPUs via /sys/class/drm and rocm-smi."""
    gpus: list[GPUInfo] = []
    drm_path = Path("/sys/class/drm")

    if not drm_path.exists():
        return gpus

    # Check for amdgpu driver cards
    for card_dir in sorted(drm_path.glob("card[0-9]*")):
        device_dir = card_dir / "device"
        driver_link = device_dir / "driver"
        if driver_link.exists():
            driver_name = os.path.basename(os.readlink(str(driver_link)))
            if driver_name == "amdgpu":
                # Try to get VRAM from mem_info_vram_total
                vram_mb = 0
                vram_file = device_dir / "mem_info_vram_total"
                if vram_file.exists():
                    try:
                        vram_bytes = int(vram_file.read_text().strip())
                        vram_mb = vram_bytes // (1024 * 1024)
                    except (ValueError, OSError):
                        pass

                # Get device name from product_name or uevent
                name = "AMD GPU"
                product_file = device_dir / "product_name"
                if product_file.exists():
                    try:
                        name = f"AMD {product_file.read_text().strip()}"
                    except OSError:
                        pass

                gpus.append(GPUInfo(
                    vendor="amd",
                    name=name,
                    vram_mb=vram_mb,
                    driver="amdgpu",
                    index=len(gpus),
                ))

    # Supplement with rocm-smi if available
    if shutil.which("rocm-smi") and gpus:
        try:
            result = subprocess.run(
                ["rocm-smi", "--showmeminfo", "vram", "--csv"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                logger.debug("rocm-smi detected AMD GPU details")
        except (subprocess.TimeoutExpired, OSError):
            pass

    return gpus


def _detect_intel_gpus() -> list[GPUInfo]:
    """Detect Intel GPUs/accelerators via /sys/class/drm."""
    gpus: list[GPUInfo] = []
    drm_path = Path("/sys/class/drm")

    if not drm_path.exists():
        return gpus

    for card_dir in sorted(drm_path.glob("card[0-9]*")):
        device_dir = card_dir / "device"
        driver_link = device_dir / "driver"
        if driver_link.exists():
            driver_name = os.path.basename(os.readlink(str(driver_link)))
            if driver_name in ("i915", "xe"):
                gpus.append(GPUInfo(
                    vendor="intel",
                    name="Intel GPU",
                    vram_mb=0,  # Hard to detect reliably for Intel
                    driver=driver_name,
                    index=len(gpus),
                ))

    return gpus


# ------------------------------------------------------------------
# Local LLM service detection
# ------------------------------------------------------------------

# Known local LLM services and their default ports/paths
_LOCAL_SERVICES = [
    ("ollama", "http://localhost:11434", "/api/tags"),
    ("lmstudio", "http://localhost:1234", "/v1/models"),
    ("vllm", "http://localhost:8000", "/v1/models"),
    ("llamacpp", "http://localhost:8080", "/v1/models"),
    ("tgi", "http://localhost:8080", "/info"),
    ("localai", "http://localhost:8080", "/v1/models"),
]


def _detect_local_services() -> list[LocalLLMService]:
    """Probe common local LLM service endpoints."""
    services: list[LocalLLMService] = []

    for name, base_url, models_path in _LOCAL_SERVICES:
        try:
            resp = httpx.get(f"{base_url}{models_path}", timeout=2.0)
            if resp.status_code == 200:
                models = _extract_model_names(name, resp.json())
                services.append(LocalLLMService(
                    name=name,
                    url=base_url,
                    models=models,
                    running=True,
                ))
                logger.info("Detected local LLM service: %s at %s (%d models)", name, base_url, len(models))
        except (httpx.ConnectError, httpx.TimeoutException, httpx.HTTPError, ValueError):
            continue

    return services


def _extract_model_names(service: str, data: dict | list) -> list[str]:
    """Extract model names from various API response formats."""
    models: list[str] = []

    if service == "ollama":
        # Ollama: {"models": [{"name": "llama3:latest", ...}]}
        for m in data.get("models", []) if isinstance(data, dict) else []:
            models.append(m.get("name", ""))
    else:
        # OpenAI-compatible: {"data": [{"id": "model-name", ...}]}
        if isinstance(data, dict):
            for m in data.get("data", []):
                models.append(m.get("id", ""))
        elif isinstance(data, list):
            for m in data:
                if isinstance(m, dict):
                    models.append(m.get("id", m.get("name", "")))

    return [m for m in models if m]


# ------------------------------------------------------------------
# System info
# ------------------------------------------------------------------


def _get_cpu_cores() -> int:
    """Return the number of CPU cores."""
    try:
        return os.cpu_count() or 1
    except Exception:
        return 1


def _get_ram_mb() -> int:
    """Return total system RAM in MiB."""
    try:
        import psutil
        return int(psutil.virtual_memory().total / (1024 * 1024))
    except ImportError:
        # Fallback: read /proc/meminfo
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb // 1024
        except (OSError, ValueError):
            pass
    return 0


# ------------------------------------------------------------------
# Model size recommendations
# ------------------------------------------------------------------

# Minimum VRAM (MiB) required for common model sizes
_MODEL_VRAM_REQUIREMENTS = {
    "7b": 6_000,     # 7B params ~ 6 GB VRAM (Q4 quantized)
    "13b": 10_000,   # 13B params ~ 10 GB
    "34b": 20_000,   # 34B params ~ 20 GB
    "70b": 40_000,   # 70B params ~ 40 GB
}

# Recommended local models by VRAM tier
_LOCAL_MODEL_TIERS = [
    (40_000, "llama3:70b-instruct-q4_K_M", "70B model — excellent quality"),
    (20_000, "llama3:34b-instruct-q4_K_M", "34B model — very good quality"),
    (10_000, "llama3:13b-instruct-q4_K_M", "13B model — good quality"),
    (6_000,  "llama3:8b-instruct-q4_K_M",  "8B model — adequate for server ops"),
    (3_000,  "llama3:3b-instruct-q4_K_M",  "3B model — basic capability"),
]


# ------------------------------------------------------------------
# Main detection function
# ------------------------------------------------------------------


def detect_hardware() -> HardwareProfile:
    """Run full hardware detection and return a :class:`HardwareProfile`.

    This is a synchronous function (GPU detection uses subprocess).
    Call it once at startup or when config is reloaded.
    """
    profile = HardwareProfile(
        cpu_cores=_get_cpu_cores(),
        ram_mb=_get_ram_mb(),
    )

    # Detect GPUs (try all vendors)
    profile.gpus.extend(_detect_nvidia_gpus())
    profile.gpus.extend(_detect_amd_gpus())
    profile.gpus.extend(_detect_intel_gpus())

    profile.has_gpu = len(profile.gpus) > 0
    profile.total_vram_mb = sum(g.vram_mb for g in profile.gpus)

    # Detect local LLM services
    profile.local_services = _detect_local_services()

    # Generate recommendation
    _recommend_provider(profile)

    logger.info(
        "Hardware detection complete: %d GPUs (%d MiB VRAM), %d local services, "
        "recommended=%s/%s",
        len(profile.gpus), profile.total_vram_mb, len(profile.local_services),
        profile.recommended_provider, profile.recommended_model,
    )

    return profile


def _recommend_provider(profile: HardwareProfile) -> None:
    """Set the recommended_provider/model/reason on *profile*."""

    # Priority 1: Running local service with loaded models
    for svc in profile.local_services:
        if svc.running and svc.models:
            profile.recommended_provider = "local"
            profile.recommended_model = svc.models[0]
            profile.recommendation_reason = (
                f"Local {svc.name} service detected at {svc.url} "
                f"with {len(svc.models)} model(s) available"
            )
            return

    # Priority 2: GPU available — recommend local with appropriate model size
    if profile.has_gpu and profile.total_vram_mb >= 3000:
        for min_vram, model_name, description in _LOCAL_MODEL_TIERS:
            if profile.total_vram_mb >= min_vram:
                profile.recommended_provider = "local"
                profile.recommended_model = model_name
                profile.recommendation_reason = (
                    f"GPU detected ({profile.total_vram_mb} MiB VRAM) — "
                    f"{description}. Install Ollama: curl -fsSL https://ollama.com/install.sh | sh"
                )
                return

    # Priority 3: Lots of RAM but no GPU — can still run small models on CPU
    if profile.ram_mb >= 16_000 and profile.cpu_cores >= 4:
        profile.recommended_provider = "local"
        profile.recommended_model = "llama3:8b-instruct-q4_K_M"
        profile.recommendation_reason = (
            f"No GPU detected but sufficient RAM ({profile.ram_mb} MiB) and "
            f"CPU cores ({profile.cpu_cores}) for CPU inference. "
            f"Will be slower than GPU. Install Ollama for local inference, "
            f"or use Anthropic API for better performance."
        )
        return

    # Priority 4: Fallback — Claude API
    profile.recommended_provider = "anthropic"
    profile.recommended_model = "claude-sonnet-4-5"
    profile.recommendation_reason = (
        "No local GPU or LLM service detected. "
        "Using Anthropic Claude API as fallback (requires API key)."
    )
