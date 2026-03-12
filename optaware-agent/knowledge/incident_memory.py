"""Persistent incident memory for the OptAware knowledge layer."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default storage path (relative to the project root when running normally).
_DEFAULT_STORAGE = Path("/var/lib/optaware/incident_memory.json")

# Common English stop-words stripped before similarity calculation.
_STOP_WORDS: frozenset[str] = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "as", "is", "was", "are", "were", "be",
        "been", "have", "has", "had", "do", "does", "did", "will", "would",
        "could", "should", "this", "that", "it", "not", "if", "so", "up",
        "out", "about", "into", "than", "more", "also", "after", "before",
        "while", "when", "where", "what", "which", "who", "how", "all",
        "each", "any", "some", "such", "only", "same",
    }
)


class IncidentMemory:
    """In-memory + JSON-persisted store for past incidents and their resolutions."""

    def __init__(self, storage_path: str | Path = _DEFAULT_STORAGE) -> None:
        self._storage_path = Path(storage_path)
        self._incidents: list[dict] = []
        # Attempt to load any existing data from disk.
        self.load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_incident(
        self,
        event: dict,
        diagnosis: dict,
        resolution: dict,
        outcome: str,
    ) -> str:
        """Persist a new incident record and save to disk.

        Parameters
        ----------
        event:
            Raw event data that triggered the incident (e.g. alert payload).
        diagnosis:
            Structured diagnosis produced by the cognition layer.
        resolution:
            Actions taken or recommended to resolve the incident.
        outcome:
            Human-readable outcome string, e.g. ``"resolved"``, ``"failed"``,
            ``"escalated"``.

        Returns the generated incident ID.
        """
        incident_id = str(uuid.uuid4())
        issue_type = diagnosis.get("issue_type") or event.get("type") or "unknown"

        # Build a searchable description from the most useful fields.
        description_parts = [
            str(event.get("description", "")),
            str(event.get("message", "")),
            str(diagnosis.get("summary", "")),
            str(diagnosis.get("root_cause", "")),
            issue_type,
        ]
        description = " ".join(p for p in description_parts if p).strip()

        record: dict[str, Any] = {
            "id": incident_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "issue_type": issue_type,
            "description": description,
            "outcome": outcome,
            "event": event,
            "diagnosis": diagnosis,
            "resolution": resolution,
        }

        self._incidents.append(record)
        self.save()
        logger.info(
            "Recorded incident %s (type=%s, outcome=%s).",
            incident_id,
            issue_type,
            outcome,
        )
        return incident_id

    def search_similar(self, description: str, limit: int = 5) -> list[dict]:
        """Return up to *limit* past incidents ranked by textual similarity to *description*.

        Results are sorted by descending similarity score.  Each returned dict
        is the stored incident record with an extra ``_similarity`` key.
        """
        if not self._incidents:
            return []

        scored: list[tuple[float, dict]] = []
        for incident in self._incidents:
            sim = self._calculate_similarity(description, incident.get("description", ""))
            if sim > 0.0:
                scored.append((sim, incident))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for sim, incident in scored[:limit]:
            entry = dict(incident)
            entry["_similarity"] = round(sim, 4)
            results.append(entry)

        return results

    def get_resolution_for(self, issue_type: str) -> dict | None:
        """Return the resolution from the most recent *resolved* incident of *issue_type*.

        Returns None when no matching resolved incident is found.
        """
        normalised = issue_type.lower().strip()
        candidates = [
            inc
            for inc in self._incidents
            if inc.get("issue_type", "").lower() == normalised
            and inc.get("outcome", "").lower() in {"resolved", "success", "fixed", "mitigated"}
        ]
        if not candidates:
            return None

        # Return the resolution from the most recent successful incident.
        latest = max(candidates, key=lambda inc: inc.get("timestamp", ""))
        return latest.get("resolution")

    def get_statistics(self) -> dict:
        """Return aggregate statistics over all recorded incidents."""
        if not self._incidents:
            return {
                "total_incidents": 0,
                "resolved": 0,
                "failed": 0,
                "other": 0,
                "resolution_rate": 0.0,
                "common_issue_types": [],
                "outcome_distribution": {},
            }

        total = len(self._incidents)
        outcome_counts: Counter[str] = Counter(
            inc.get("outcome", "unknown").lower() for inc in self._incidents
        )

        resolved_outcomes = {"resolved", "success", "fixed", "mitigated"}
        failed_outcomes = {"failed", "error", "timeout", "escalated"}

        resolved = sum(outcome_counts[k] for k in resolved_outcomes)
        failed = sum(outcome_counts[k] for k in failed_outcomes)
        other = total - resolved - failed

        resolution_rate = round(resolved / total, 4) if total > 0 else 0.0

        issue_type_counts: Counter[str] = Counter(
            inc.get("issue_type", "unknown") for inc in self._incidents
        )
        common_issue_types = [
            {"issue_type": t, "count": c}
            for t, c in issue_type_counts.most_common(10)
        ]

        # Per-issue-type resolution rates.
        issue_outcomes: dict[str, list[str]] = defaultdict(list)
        for inc in self._incidents:
            issue_outcomes[inc.get("issue_type", "unknown")].append(
                inc.get("outcome", "unknown").lower()
            )

        issue_resolution_rates = {
            issue: round(
                sum(1 for o in outcomes if o in resolved_outcomes) / len(outcomes), 4
            )
            for issue, outcomes in issue_outcomes.items()
        }

        return {
            "total_incidents": total,
            "resolved": resolved,
            "failed": failed,
            "other": other,
            "resolution_rate": resolution_rate,
            "common_issue_types": common_issue_types,
            "outcome_distribution": dict(outcome_counts),
            "issue_resolution_rates": issue_resolution_rates,
        }

    def load(self) -> None:
        """Load incidents from the JSON file on disk.

        Silently initialises an empty list when the file does not exist or
        cannot be parsed.
        """
        if not self._storage_path.exists():
            self._incidents = []
            return

        try:
            raw = self._storage_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, list):
                self._incidents = data
            else:
                logger.warning(
                    "Unexpected format in incident memory file '%s'; resetting.",
                    self._storage_path,
                )
                self._incidents = []
        except (json.JSONDecodeError, OSError) as exc:
            logger.error(
                "Failed to load incident memory from '%s': %s", self._storage_path, exc
            )
            self._incidents = []

    def save(self) -> None:
        """Persist all incidents to the JSON file on disk.

        Creates parent directories as needed.  Errors are logged but not raised
        so that a storage failure never disrupts the monitoring pipeline.
        """
        try:
            self._storage_path.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(self._incidents, indent=2, default=str)
            self._storage_path.write_text(payload, encoding="utf-8")
        except OSError as exc:
            logger.error(
                "Failed to save incident memory to '%s': %s", self._storage_path, exc
            )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _calculate_similarity(self, text1: str, text2: str) -> float:
        """Return a word-overlap (Jaccard) similarity between two strings.

        The score is in the range [0.0, 1.0].  Stop-words are excluded so that
        common filler words don't artificially inflate similarity scores.
        """

        def tokenise(text: str) -> set[str]:
            tokens = re.findall(r"[a-zA-Z0-9_\-]+", text.lower())
            return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}

        words1 = tokenise(text1)
        words2 = tokenise(text2)

        if not words1 or not words2:
            return 0.0

        intersection = words1 & words2
        union = words1 | words2

        return len(intersection) / len(union)
