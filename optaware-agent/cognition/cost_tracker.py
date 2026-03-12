"""
CostTracker — Track and persist LLM API usage costs.

Records token usage per call, maps it to USD cost using a pricing table,
enforces a daily spending limit, and persists state to a JSON file so costs
survive process restarts.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, timezone, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pricing table (USD per 1,000 tokens)
# Prices as of early 2025; update as providers change their rates.
# Keys are normalised to lowercase for matching.
# ---------------------------------------------------------------------------

_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-opus-4-5": {"input": 0.015, "output": 0.075},
    "claude-sonnet-4-5": {"input": 0.003, "output": 0.015},
    "claude-haiku-3-5": {"input": 0.00025, "output": 0.00125},
    "claude-3-opus-20240229": {"input": 0.015, "output": 0.075},
    "claude-3-sonnet-20240229": {"input": 0.003, "output": 0.015},
    "claude-3-haiku-20240307": {"input": 0.00025, "output": 0.00125},
    # OpenAI
    "gpt-4o": {"input": 0.005, "output": 0.015},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4-turbo": {"input": 0.010, "output": 0.030},
    "gpt-4": {"input": 0.030, "output": 0.060},
    "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
    # Google (Vertex / Gemini)
    "gemini-1.5-pro": {"input": 0.00125, "output": 0.005},
    "gemini-1.5-flash": {"input": 0.000075, "output": 0.0003},
    # Fallback for unknown models — assume mid-tier pricing
    "__default__": {"input": 0.002, "output": 0.008},
}

# How long (days) to retain daily cost records before pruning old entries
_RETENTION_DAYS: int = 90

_DEFAULT_PERSIST_PATH: Path = Path(
    os.environ.get("OPTAWARE_DATA_DIR", "/var/lib/optaware")
) / "cost_tracker.json"


def _today_str() -> str:
    """Return today's date as an ISO-8601 string (YYYY-MM-DD) in UTC."""
    return date.today().isoformat()


def _calculate_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    """Calculate USD cost for a single LLM call.

    Args:
        input_tokens: Number of prompt/input tokens consumed.
        output_tokens: Number of completion/output tokens generated.
        model: Model identifier string.

    Returns:
        Cost in USD as a float.
    """
    key = model.lower().strip()
    pricing = _PRICING.get(key)
    if pricing is None:
        # Try prefix matching (e.g. "claude-sonnet-4-5-20250219" -> "claude-sonnet-4-5")
        for known_key in _PRICING:
            if known_key != "__default__" and key.startswith(known_key):
                pricing = _PRICING[known_key]
                break
    if pricing is None:
        logger.debug("No pricing found for model '%s'; using __default__.", model)
        pricing = _PRICING["__default__"]

    cost = (input_tokens / 1000.0) * pricing["input"] + (
        output_tokens / 1000.0
    ) * pricing["output"]
    return round(cost, 8)


class CostTracker:
    """Track, persist, and report LLM API usage costs.

    Daily costs are accumulated in memory and persisted to a JSON file after
    every `record_usage` call. The tracker can enforce a configurable daily
    spending limit via `is_within_budget`.
    """

    def __init__(
        self,
        daily_limit: float = 10.0,
        persist_path: str | Path | None = None,
    ) -> None:
        """
        Args:
            daily_limit: Maximum allowed USD spend per calendar day.
            persist_path: Path to the JSON file used for persistence.
                          Defaults to OPTAWARE_DATA_DIR/cost_tracker.json.
        """
        self._daily_limit: float = daily_limit
        self._persist_path: Path = (
            Path(persist_path) if persist_path is not None else _DEFAULT_PERSIST_PATH
        )

        # date string -> total cost (USD)
        self._daily_costs: dict[str, float] = {}
        # model -> {"input_tokens": int, "output_tokens": int, "cost": float, "calls": int}
        self._model_stats: dict[str, dict[str, Any]] = {}
        # Full call log for auditing
        self._call_log: list[dict[str, Any]] = []

        self._load()

    # ------------------------------------------------------------------
    # Core recording
    # ------------------------------------------------------------------

    def record_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        model: str,
        *,
        context: str | None = None,
    ) -> float:
        """Record token usage for a completed LLM call.

        Calculates the cost, adds it to today's daily total, updates per-model
        statistics, and persists the updated state to disk.

        Args:
            input_tokens: Number of input/prompt tokens consumed.
            output_tokens: Number of output/completion tokens generated.
            model: Model identifier string (e.g. "claude-sonnet-4-5").
            context: Optional short label for this call (e.g. "diagnose_issue").

        Returns:
            Cost in USD for this specific call.
        """
        cost = _calculate_cost(input_tokens, output_tokens, model)
        today = _today_str()

        # Update daily total
        self._daily_costs[today] = round(
            self._daily_costs.get(today, 0.0) + cost, 8
        )

        # Update per-model stats
        stats = self._model_stats.setdefault(
            model,
            {"input_tokens": 0, "output_tokens": 0, "cost": 0.0, "calls": 0},
        )
        stats["input_tokens"] += input_tokens
        stats["output_tokens"] += output_tokens
        stats["cost"] = round(stats["cost"] + cost, 8)
        stats["calls"] += 1

        # Append to call log
        self._call_log.append(
            {
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost": cost,
                "context": context,
            }
        )

        logger.debug(
            "Usage recorded: model=%s input=%d output=%d cost=$%.6f daily_total=$%.6f",
            model,
            input_tokens,
            output_tokens,
            cost,
            self._daily_costs[today],
        )

        self._prune_old_records()
        self._save()
        return cost

    # ------------------------------------------------------------------
    # Budget queries
    # ------------------------------------------------------------------

    def get_daily_cost(self, date_str: str | None = None) -> float:
        """Return the total USD cost for a specific calendar day.

        Args:
            date_str: ISO-8601 date string (YYYY-MM-DD). Defaults to today.

        Returns:
            Total cost in USD for that day (0.0 if no records exist).
        """
        key = date_str if date_str is not None else _today_str()
        return self._daily_costs.get(key, 0.0)

    def is_within_budget(self) -> bool:
        """Return True if today's spending has not exceeded the daily limit."""
        return self.get_daily_cost() < self._daily_limit

    def remaining_budget(self) -> float:
        """Return the remaining USD budget for today."""
        return max(0.0, self._daily_limit - self.get_daily_cost())

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def get_usage_report(self) -> dict:
        """Generate a summary usage and cost report.

        Returns:
            Dict containing:
            - daily_limit: configured daily spend limit
            - today: today's date string
            - today_cost: total cost today
            - remaining_budget: budget left for today
            - within_budget: whether still within budget
            - total_cost: all-time total cost
            - total_calls: all-time number of LLM calls
            - total_input_tokens: all-time input token count
            - total_output_tokens: all-time output token count
            - by_model: per-model breakdown dict
            - daily_history: list of {date, cost} sorted by date descending
            - average_cost_per_call: all-time average
            - average_daily_cost: average over days with recorded activity
        """
        total_cost = sum(self._daily_costs.values())
        total_calls = sum(s["calls"] for s in self._model_stats.values())
        total_input = sum(s["input_tokens"] for s in self._model_stats.values())
        total_output = sum(s["output_tokens"] for s in self._model_stats.values())
        active_days = len([v for v in self._daily_costs.values() if v > 0])

        daily_history = [
            {"date": d, "cost": round(c, 6)}
            for d, c in sorted(self._daily_costs.items(), reverse=True)
            if c > 0
        ]

        return {
            "daily_limit": self._daily_limit,
            "today": _today_str(),
            "today_cost": round(self.get_daily_cost(), 6),
            "remaining_budget": round(self.remaining_budget(), 6),
            "within_budget": self.is_within_budget(),
            "total_cost": round(total_cost, 6),
            "total_calls": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "by_model": {
                model: {
                    "calls": s["calls"],
                    "input_tokens": s["input_tokens"],
                    "output_tokens": s["output_tokens"],
                    "cost": round(s["cost"], 6),
                }
                for model, s in self._model_stats.items()
            },
            "daily_history": daily_history,
            "average_cost_per_call": (
                round(total_cost / total_calls, 6) if total_calls else 0.0
            ),
            "average_daily_cost": (
                round(total_cost / active_days, 6) if active_days else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def set_daily_limit(self, limit: float) -> None:
        """Update the daily spending limit.

        Args:
            limit: New limit in USD. Must be positive.
        """
        if limit <= 0:
            raise ValueError("Daily limit must be a positive number.")
        self._daily_limit = limit
        self._save()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save(self) -> None:
        """Persist current state to the JSON file."""
        try:
            self._persist_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "daily_limit": self._daily_limit,
                "daily_costs": self._daily_costs,
                "model_stats": self._model_stats,
                "call_log": self._call_log[-10_000:],  # cap to last 10k entries
            }
            tmp_path = self._persist_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, indent=2))
            tmp_path.replace(self._persist_path)
        except OSError as exc:
            logger.warning("Failed to persist cost tracker state: %s", exc)

    def _load(self) -> None:
        """Load persisted state from the JSON file if it exists."""
        if not self._persist_path.exists():
            logger.debug(
                "Cost tracker persistence file not found at %s; starting fresh.",
                self._persist_path,
            )
            return
        try:
            payload = json.loads(self._persist_path.read_text())
            self._daily_limit = float(payload.get("daily_limit", self._daily_limit))
            self._daily_costs = {
                k: float(v) for k, v in payload.get("daily_costs", {}).items()
            }
            self._model_stats = payload.get("model_stats", {})
            self._call_log = payload.get("call_log", [])
            logger.debug(
                "Cost tracker loaded from %s: %d days of history, %d model(s).",
                self._persist_path,
                len(self._daily_costs),
                len(self._model_stats),
            )
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning(
                "Failed to load cost tracker state from %s: %s. Starting fresh.",
                self._persist_path,
                exc,
            )

    def _prune_old_records(self) -> None:
        """Remove daily cost records older than _RETENTION_DAYS."""
        cutoff = date.today().toordinal() - _RETENTION_DAYS
        to_delete = [
            d for d in self._daily_costs
            if _date_ordinal(d) < cutoff
        ]
        for d in to_delete:
            del self._daily_costs[d]
        if to_delete:
            logger.debug("Pruned %d old daily cost records.", len(to_delete))


def _date_ordinal(date_str: str) -> int:
    """Convert an ISO date string to its ordinal for age comparisons.

    Returns 0 on parse failure so malformed entries are treated as very old.
    """
    try:
        return date.fromisoformat(date_str).toordinal()
    except ValueError:
        return 0
