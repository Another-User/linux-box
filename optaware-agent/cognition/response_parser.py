"""
ResponseParser — Parse structured output from LLM responses.

Handles extraction of JSON blocks, code blocks, and domain-specific
structured sections from free-form LLM text output.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class ResponseParser:
    """
    Parse and extract structured data from LLM responses.

    LLM outputs are often a mixture of free-form prose and structured sections.
    This class provides methods to reliably extract JSON payloads, code blocks,
    and domain-specific fields from that mixed output.
    """

    # Matches fenced JSON blocks: ```json ... ``` or ``` ... ```
    _JSON_FENCE_RE = re.compile(
        r"```(?:json)?\s*(\{[\s\S]*?\}|\[[\s\S]*?\])\s*```",
        re.IGNORECASE,
    )

    # Matches any fenced code block: ```[lang] ... ```
    _CODE_FENCE_RE = re.compile(
        r"```(?P<lang>\w+)?\s*(?P<body>[\s\S]*?)```",
        re.MULTILINE,
    )

    # Inline JSON object starting at the beginning of a line
    _INLINE_JSON_RE = re.compile(
        r"(?:^|\n)\s*(\{[\s\S]*?\})\s*(?:\n|$)",
    )

    # Section headers in markdown style
    _SECTION_RE = re.compile(
        r"^#{1,3}\s+(.+?)\s*$",
        re.MULTILINE,
    )

    def extract_json(self, response: str) -> dict | None:
        """Find and parse the first JSON object or array in the response.

        Searches fenced code blocks first, then falls back to scanning for
        bare JSON objects in the text.

        Args:
            response: Raw LLM response text.

        Returns:
            Parsed JSON as a Python dict (or list coerced to dict under key
            "items"), or None if no valid JSON is found.
        """
        # Priority 1: fenced JSON blocks
        for match in self._JSON_FENCE_RE.finditer(response):
            candidate = match.group(1).strip()
            parsed = self._try_parse_json(candidate)
            if parsed is not None:
                return parsed if isinstance(parsed, dict) else {"items": parsed}

        # Priority 2: largest JSON-like substring (greedy brace matching)
        candidate = self._extract_largest_json_substring(response)
        if candidate:
            parsed = self._try_parse_json(candidate)
            if parsed is not None:
                return parsed if isinstance(parsed, dict) else {"items": parsed}

        return None

    def extract_code_blocks(self, response: str) -> list[str]:
        """Extract all fenced code block bodies from the response.

        Args:
            response: Raw LLM response text.

        Returns:
            List of code block contents (without the fence markers or language tag).
        """
        blocks: list[str] = []
        for match in self._CODE_FENCE_RE.finditer(response):
            body = match.group("body")
            if body and body.strip():
                blocks.append(body.strip())
        return blocks

    def parse_diagnosis(self, response: str) -> dict:
        """Extract a structured diagnosis from an LLM response.

        Looks for a JSON block with diagnosis fields. Falls back to
        heuristic text extraction if JSON is absent or incomplete.

        Args:
            response: Raw LLM response text.

        Returns:
            Dict with keys: severity, root_cause, affected_services,
            confidence, explanation, immediate_actions.
        """
        defaults: dict[str, Any] = {
            "severity": "unknown",
            "root_cause": "",
            "affected_services": [],
            "confidence": 0.0,
            "explanation": "",
            "immediate_actions": [],
        }

        data = self.extract_json(response)
        if data:
            defaults.update({k: data[k] for k in defaults if k in data})
            # Normalise types
            if isinstance(defaults["affected_services"], str):
                defaults["affected_services"] = [
                    s.strip() for s in defaults["affected_services"].split(",") if s.strip()
                ]
            try:
                defaults["confidence"] = float(defaults["confidence"])
            except (TypeError, ValueError):
                defaults["confidence"] = 0.0
            return defaults

        # Heuristic fallback: scan for labelled lines
        defaults["root_cause"] = self._extract_field(response, r"root.cause[:\s]+(.+)")
        defaults["severity"] = self._extract_field(
            response, r"severity[:\s]+(critical|high|medium|low)", default="unknown"
        ).lower()
        defaults["explanation"] = self._extract_paragraph_after(response, "explanation")
        defaults["immediate_actions"] = self._extract_bullet_list(
            response, "immediate.actions"
        )
        services_raw = self._extract_field(response, r"affected.services[:\s]+(.+)")
        if services_raw:
            defaults["affected_services"] = [
                s.strip() for s in re.split(r"[,;]", services_raw) if s.strip()
            ]
        conf_raw = self._extract_field(response, r"confidence[:\s]+([\d.]+)")
        if conf_raw:
            try:
                defaults["confidence"] = float(conf_raw)
            except ValueError:
                pass

        return defaults

    def parse_action_plan(self, response: str) -> list[dict]:
        """Extract an ordered list of action steps from an LLM response.

        Attempts JSON extraction first; falls back to parsing numbered lists.

        Args:
            response: Raw LLM response text.

        Returns:
            List of step dicts, each containing at minimum:
            step, description, command, risk, rollback.
        """
        # Try JSON array extraction via fenced blocks
        for match in self._JSON_FENCE_RE.finditer(response):
            candidate = match.group(1).strip()
            parsed = self._try_parse_json(candidate)
            if isinstance(parsed, list):
                return self._normalise_steps(parsed)
            if isinstance(parsed, dict) and "items" in parsed and isinstance(parsed["items"], list):
                return self._normalise_steps(parsed["items"])

        # Try bare JSON array
        array_match = re.search(r"\[\s*\{[\s\S]*?\}\s*\]", response)
        if array_match:
            parsed = self._try_parse_json(array_match.group(0))
            if isinstance(parsed, list):
                return self._normalise_steps(parsed)

        # Fallback: parse numbered list items
        return self._parse_numbered_steps(response)

    def parse_explanation(self, response: str) -> dict:
        """Extract a structured explanation from an LLM response.

        Args:
            response: Raw LLM response text.

        Returns:
            Dict with keys: summary, details, recommendations, severity, anomalies.
        """
        defaults: dict[str, Any] = {
            "summary": "",
            "details": "",
            "recommendations": [],
            "severity": "unknown",
            "anomalies": [],
        }

        data = self.extract_json(response)
        if data:
            defaults.update({k: data[k] for k in defaults if k in data})
            if isinstance(defaults["recommendations"], str):
                defaults["recommendations"] = [
                    r.strip()
                    for r in re.split(r"[\n;]", defaults["recommendations"])
                    if r.strip()
                ]
            if isinstance(defaults["anomalies"], str):
                defaults["anomalies"] = [
                    a.strip()
                    for a in re.split(r"[\n;]", defaults["anomalies"])
                    if a.strip()
                ]
            return defaults

        # Heuristic fallback
        defaults["summary"] = self._extract_field(response, r"summary[:\s]+(.+)")
        defaults["details"] = self._extract_paragraph_after(response, "details")
        defaults["severity"] = self._extract_field(
            response, r"severity[:\s]+(critical|high|medium|low|nominal)", default="unknown"
        ).lower()
        defaults["recommendations"] = self._extract_bullet_list(response, "recommendations")
        defaults["anomalies"] = self._extract_bullet_list(response, "anomalies")

        # Final fallback: use the whole response as the summary if empty
        if not defaults["summary"]:
            first_para = response.strip().split("\n\n")[0].strip()
            defaults["summary"] = first_para[:500]

        return defaults

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _try_parse_json(text: str) -> Any:
        """Attempt to parse text as JSON, returning None on failure."""
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass
        # Try relaxing trailing commas (common LLM mistake)
        cleaned = re.sub(r",\s*([\}\]])", r"\1", text)
        try:
            return json.loads(cleaned)
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _extract_largest_json_substring(text: str) -> str | None:
        """Find the largest balanced JSON object substring in text."""
        best: str | None = None
        best_len = 0
        for start_char, end_char in [("{", "}"), ("[", "]")]:
            depth = 0
            start_idx: int | None = None
            for i, ch in enumerate(text):
                if ch == start_char:
                    if depth == 0:
                        start_idx = i
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0 and start_idx is not None:
                        candidate = text[start_idx: i + 1]
                        if len(candidate) > best_len:
                            best = candidate
                            best_len = len(candidate)
                        start_idx = None
        return best

    @staticmethod
    def _extract_field(
        text: str,
        pattern: str,
        default: str = "",
        flags: int = re.IGNORECASE,
    ) -> str:
        """Extract first capture group from pattern in text."""
        match = re.search(pattern, text, flags)
        if match:
            return match.group(1).strip()
        return default

    @staticmethod
    def _extract_paragraph_after(text: str, section_keyword: str) -> str:
        """Extract the first non-empty paragraph after a section keyword."""
        pattern = rf"(?:^|\n)#{'{1,3}'}\s*{section_keyword}.*?\n([\s\S]+?)(?=\n#|\Z)"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    @staticmethod
    def _extract_bullet_list(text: str, section_keyword: str) -> list[str]:
        """Extract bullet list items under a labelled section."""
        pattern = (
            rf"(?:^|\n)(?:#{'{1,3}'}\s*)?{section_keyword}[:\s]*\n"
            rf"((?:\s*[-*•]\s*.+\n?)+)"
        )
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return []
        block = match.group(1)
        items = re.findall(r"[-*•]\s*(.+)", block)
        return [item.strip() for item in items if item.strip()]

    @staticmethod
    def _normalise_steps(steps: list[Any]) -> list[dict]:
        """Ensure each step dict has expected keys with sensible defaults."""
        normalised = []
        for i, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            normalised.append(
                {
                    "step": step.get("step", i + 1),
                    "phase": step.get("phase", "execution"),
                    "description": step.get("description", ""),
                    "command": step.get("command"),
                    "expected_output": step.get("expected_output", ""),
                    "rollback": step.get("rollback", ""),
                    "risk": step.get("risk", "unknown"),
                    "requires_downtime": bool(step.get("requires_downtime", False)),
                    "estimated_duration_seconds": step.get("estimated_duration_seconds", 0),
                }
            )
        return normalised

    @staticmethod
    def _parse_numbered_steps(text: str) -> list[dict]:
        """Parse numbered list items from free-form text as action steps."""
        step_pattern = re.compile(
            r"(?:^|\n)\s*(\d+)[.)]\s+(.+?)(?=\n\s*\d+[.)]|\Z)",
            re.DOTALL,
        )
        steps = []
        for match in step_pattern.finditer(text):
            num = int(match.group(1))
            body = match.group(2).strip()
            # Try to extract a command from a code span or line starting with $
            cmd_match = re.search(r"`([^`]+)`|\$\s*(.+)", body)
            command = None
            if cmd_match:
                command = (cmd_match.group(1) or cmd_match.group(2) or "").strip() or None
            steps.append(
                {
                    "step": num,
                    "phase": "execution",
                    "description": body,
                    "command": command,
                    "expected_output": "",
                    "rollback": "",
                    "risk": "unknown",
                    "requires_downtime": False,
                    "estimated_duration_seconds": 0,
                }
            )
        return steps
