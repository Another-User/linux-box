"""Action planner for OptAware agent.

Converts LLM diagnostic suggestions into validated, executable action plans.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from models.actions import Action, ActionStep, ActionType, ActionStatus

logger = logging.getLogger(__name__)


class ActionPlanner:
    """Convert LLM suggestions into executable action plans with safety validation."""

    _dangerous_patterns: list[str] = [
        r"rm\s+-rf?\s+/",
        r"rm\s+--no-preserve-root",
        r"\bdd\b.*of=/dev/[sh]d",
        r"\bmkfs\b",
        r"\bformat\b.*[/\\]dev[/\\]",
        r"\bDROP\s+TABLE\b",
        r"\bDROP\s+DATABASE\b",
        r"\bTRUNCATE\b",
        r">\s*/dev/[sh]d[a-z]",
        r"\bfdisk\b",
        r"\bparted\b",
        r"shutdown\s+-[hP]",
        r"\binit\s+0\b",
        r"\bhalt\b",
        r"\bpoweroff\b",
        r"chmod\s+-R\s+777\s+/",
        r"chown\s+-R\s+.*\s+/",
        r">\s*/etc/passwd",
        r">\s*/etc/shadow",
        r"\bwipe\b",
        r"\bshred\b.*-[a-z]*u",
    ]

    # Map keyword signals to risk levels
    _high_risk_keywords: list[str] = [
        "drop", "delete", "remove", "purge", "destroy", "format", "wipe",
        "truncate", "kill", "terminate", "shutdown", "halt", "poweroff",
    ]

    _medium_risk_keywords: list[str] = [
        "restart", "reload", "stop", "disable", "modify", "change", "update",
        "replace", "overwrite", "alter", "configure",
    ]

    def create_plan(self, diagnosis: dict[str, Any], suggested_actions: list[dict[str, Any]]) -> Action:
        """Build an Action model with steps from diagnosis and LLM-suggested actions.

        Args:
            diagnosis: Diagnostic result dict, expected to contain at least
                       'action_type' (str) and optionally 'alert_id' (str/UUID).
            suggested_actions: List of step dicts, each with keys:
                               'description' (str), optionally 'command' (str),
                               'expected_outcome' (str), 'rollback_command' (str).

        Returns:
            A fully populated Action ready for validation and execution.
        """
        raw_type = diagnosis.get("action_type", "run_command")
        try:
            action_type = ActionType(raw_type)
        except ValueError:
            logger.warning("Unknown action_type %r, defaulting to run_command", raw_type)
            action_type = ActionType.run_command

        raw_alert_id = diagnosis.get("alert_id")
        alert_id: uuid.UUID | None = None
        if raw_alert_id is not None:
            try:
                alert_id = uuid.UUID(str(raw_alert_id))
            except (ValueError, AttributeError):
                logger.warning("Could not parse alert_id %r, ignoring", raw_alert_id)

        steps: list[ActionStep] = []
        for idx, step_data in enumerate(suggested_actions, start=1):
            step = ActionStep(
                step_number=idx,
                description=step_data.get("description", f"Step {idx}"),
                command=step_data.get("command"),
                expected_outcome=step_data.get("expected_outcome", ""),
                rollback_command=step_data.get("rollback_command"),
            )
            steps.append(step)

        # Determine whether the plan requires approval based on risk
        risk = self._assess_steps_risk(steps)
        requires_approval = risk in ("medium", "high")

        action = Action(
            alert_id=alert_id,
            action_type=action_type,
            status=ActionStatus.pending,
            steps=steps,
            requires_approval=requires_approval,
        )

        logger.info(
            "Created plan %s with %d step(s), risk=%s, requires_approval=%s",
            action.id,
            len(steps),
            risk,
            requires_approval,
        )
        return action

    def validate_plan(self, action: Action) -> list[str]:
        """Run safety checks on an action plan and return a list of violation messages.

        An empty list means the plan is safe to proceed. Any returned strings
        describe specific problems that must be resolved before execution.
        """
        violations: list[str] = []

        for step in action.steps:
            for cmd_field in (step.command, step.rollback_command):
                if cmd_field is None:
                    continue
                for pattern in self._dangerous_patterns:
                    if re.search(pattern, cmd_field, re.IGNORECASE):
                        violations.append(
                            f"Step {step.step_number}: dangerous pattern detected "
                            f"({pattern!r}) in command: {cmd_field!r}"
                        )

        if action.action_type == ActionType.run_command:
            for step in action.steps:
                if step.command is None:
                    violations.append(
                        f"Step {step.step_number}: action_type is run_command "
                        "but no command is specified."
                    )

        if action.action_type == ActionType.modify_config:
            for step in action.steps:
                if step.rollback_command is None and step.command is not None:
                    violations.append(
                        f"Step {step.step_number}: config modification has no "
                        "rollback_command — manual approval required."
                    )

        if not action.steps:
            violations.append("Action plan has no steps defined.")

        return violations

    def estimate_risk(self, action: Action) -> str:
        """Estimate the risk level of an action plan.

        Returns one of "low", "medium", or "high".
        """
        return self._assess_steps_risk(action.steps)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _assess_steps_risk(self, steps: list[ActionStep]) -> str:
        """Return the highest risk level found across all steps."""
        current_risk = "low"

        for step in steps:
            for source in (step.command, step.description, step.expected_outcome):
                if source is None:
                    continue
                lower = source.lower()

                # Check dangerous patterns first — always high
                for pattern in self._dangerous_patterns:
                    if re.search(pattern, source, re.IGNORECASE):
                        return "high"

                for keyword in self._high_risk_keywords:
                    if keyword in lower:
                        return "high"

                if current_risk != "high":
                    for keyword in self._medium_risk_keywords:
                        if keyword in lower:
                            current_risk = "medium"

        return current_risk
