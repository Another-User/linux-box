"""
ContextManager — Manage LLM conversation history and token budgets.

Maintains an ordered message history and provides utilities for trimming
that history to fit within a provider's token limit while preserving the
system message and most recent exchanges.
"""

from __future__ import annotations

import logging
import math
from typing import Literal

logger = logging.getLogger(__name__)

# Rough average characters-per-token observed across GPT-4 / Claude models.
_CHARS_PER_TOKEN: float = 4.0

# Multiplier applied to raw word count for a quick token estimate.
_WORDS_TO_TOKENS: float = 1.3

MessageRole = Literal["system", "user", "assistant"]


class ContextManager:
    """
    Manage conversation history and token budget for LLM interactions.

    The manager stores messages in chronological order and provides methods
    to trim history to stay within a token budget. The system message (if
    present) is always preserved; the most recent messages take priority
    over older ones when trimming is required.
    """

    def __init__(self, max_tokens: int = 8192) -> None:
        """
        Args:
            max_tokens: Default token budget for get_messages trimming.
        """
        self._messages: list[dict] = []
        self._max_tokens: int = max_tokens

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_message(self, role: str, content: str) -> None:
        """Append a message to the conversation history.

        Args:
            role: Message role — "system", "user", or "assistant".
            content: Text content of the message.
        """
        if not content or not content.strip():
            logger.debug("Skipping empty message for role '%s'.", role)
            return
        self._messages.append({"role": role, "content": content})

    def get_messages(self, max_tokens: int | None = None) -> list[dict]:
        """Return the conversation history, trimmed to fit the token budget.

        Args:
            max_tokens: Token budget. Defaults to the instance's _max_tokens.

        Returns:
            List of message dicts (role, content) that fit within the budget.
        """
        budget = max_tokens if max_tokens is not None else self._max_tokens
        return self.trim_to_budget(self._messages, budget)

    def clear(self) -> None:
        """Remove all messages from the conversation history."""
        self._messages.clear()
        logger.debug("Conversation history cleared.")

    def get_summary(self) -> str:
        """Return a human-readable summary of the current conversation state.

        Returns:
            Multi-line string describing message counts, roles, and token usage.
        """
        if not self._messages:
            return "Conversation history is empty."

        total_tokens = sum(self.estimate_tokens(m["content"]) for m in self._messages)
        role_counts: dict[str, int] = {}
        for msg in self._messages:
            role_counts[msg["role"]] = role_counts.get(msg["role"], 0) + 1

        role_summary = ", ".join(
            f"{role}: {count}" for role, count in sorted(role_counts.items())
        )
        budget_pct = (total_tokens / self._max_tokens * 100) if self._max_tokens else 0.0

        lines = [
            f"Messages: {len(self._messages)} ({role_summary})",
            f"Estimated tokens: {total_tokens:,} / {self._max_tokens:,} "
            f"({budget_pct:.1f}% of budget)",
        ]

        # Show first and last message timestamps if available
        if self._messages:
            first_role = self._messages[0]["role"]
            last_role = self._messages[-1]["role"]
            first_preview = self._messages[0]["content"][:60].replace("\n", " ")
            last_preview = self._messages[-1]["content"][:60].replace("\n", " ")
            lines.append(f"First: [{first_role}] {first_preview}…")
            lines.append(f"Last:  [{last_role}]  {last_preview}…")

        return "\n".join(lines)

    def estimate_tokens(self, text: str) -> int:
        """Rough token count estimate for a text string.

        Uses word count multiplied by 1.3 as a fast approximation.
        Adequate for budget enforcement; not a substitute for the
        provider's tokeniser.

        Args:
            text: Input string to estimate.

        Returns:
            Estimated integer token count (minimum 1 for non-empty strings).
        """
        if not text:
            return 0
        word_count = len(text.split())
        return max(1, math.ceil(word_count * _WORDS_TO_TOKENS))

    def trim_to_budget(self, messages: list[dict], budget: int) -> list[dict]:
        """Return a subset of messages that fits within the token budget.

        Strategy:
        1. The system message (first message with role "system") is always
           included and counts against the budget.
        2. Remaining messages are considered from newest to oldest; messages
           are included as long as they fit.
        3. Returns messages in their original chronological order.

        Args:
            messages: Full list of message dicts (role, content).
            budget: Maximum total estimated tokens to include.

        Returns:
            Trimmed list of messages within the token budget.
        """
        if not messages:
            return []

        # Separate system message from the rest
        system_msgs: list[dict] = [m for m in messages if m.get("role") == "system"]
        non_system: list[dict] = [m for m in messages if m.get("role") != "system"]

        remaining_budget = budget
        for msg in system_msgs:
            remaining_budget -= self.estimate_tokens(msg.get("content", ""))
            # Ensure at least 0
            remaining_budget = max(0, remaining_budget)

        # Include as many recent non-system messages as possible
        included: list[dict] = []
        for msg in reversed(non_system):
            cost = self.estimate_tokens(msg.get("content", ""))
            if cost <= remaining_budget:
                included.append(msg)
                remaining_budget -= cost
            else:
                # Skip this message; continue in case smaller messages fit
                if remaining_budget <= 0:
                    break

        # Restore chronological order
        included.reverse()

        if len(included) < len(non_system):
            dropped = len(non_system) - len(included)
            logger.debug(
                "Trimmed %d message(s) from conversation history to fit %d token budget.",
                dropped,
                budget,
            )

        return system_msgs + included

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def message_count(self) -> int:
        """Total number of messages in history."""
        return len(self._messages)

    @property
    def total_tokens(self) -> int:
        """Estimated total tokens across all messages in history."""
        return sum(self.estimate_tokens(m.get("content", "")) for m in self._messages)

    @property
    def max_tokens(self) -> int:
        """Configured token budget."""
        return self._max_tokens

    @max_tokens.setter
    def max_tokens(self, value: int) -> None:
        if value < 1:
            raise ValueError("max_tokens must be a positive integer.")
        self._max_tokens = value
