"""Approval workflow for OptAware agent.

Controls whether actions are executed automatically or require manual sign-off
based on the configured mode and the estimated risk of each action.
"""

from __future__ import annotations

import logging
from typing import Literal

from models.actions import Action, ActionStatus

logger = logging.getLogger(__name__)

ApprovalMode = Literal["auto", "manual", "hybrid"]


class ApprovalWorkflow:
    """Manage action approval based on risk level and operational mode.

    Modes
    -----
    auto    — all actions are approved immediately.
    manual  — all actions must be approved explicitly via :meth:`approve`.
    hybrid  — low-risk actions are auto-approved; medium/high-risk actions
              are held for manual review.
    """

    def __init__(self, mode: ApprovalMode = "hybrid") -> None:
        if mode not in ("auto", "manual", "hybrid"):
            raise ValueError(f"Invalid approval mode {mode!r}. Use 'auto', 'manual', or 'hybrid'.")
        self._mode: ApprovalMode = mode
        # action_id (str) -> Action waiting for a decision
        self._pending: dict[str, Action] = {}

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def mode(self) -> ApprovalMode:
        """Current approval mode."""
        return self._mode

    @mode.setter
    def mode(self, value: ApprovalMode) -> None:
        if value not in ("auto", "manual", "hybrid"):
            raise ValueError(f"Invalid approval mode {value!r}.")
        logger.info("Approval mode changed from %s to %s.", self._mode, value)
        self._mode = value

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def request_approval(self, action: Action, risk: str = "low") -> bool:
        """Determine whether an action is approved to run.

        In auto mode every action is immediately approved.
        In manual mode every action is placed in the pending queue.
        In hybrid mode low-risk actions are auto-approved; medium/high are
        queued for manual review.

        The action's ``status`` is updated to ``approved`` or left as
        ``pending`` accordingly.

        Args:
            action: The Action requesting approval.
            risk: Estimated risk level — "low", "medium", or "high".

        Returns:
            True if the action was auto-approved, False if it is now pending
            manual review.
        """
        action_id = str(action.id)

        if self._mode == "auto":
            action.status = ActionStatus.approved
            logger.info("Action %s auto-approved (mode=auto).", action_id)
            return True

        if self._mode == "manual":
            self._pending[action_id] = action
            logger.info(
                "Action %s queued for manual approval (mode=manual).", action_id
            )
            return False

        # hybrid
        if risk == "low":
            action.status = ActionStatus.approved
            logger.info(
                "Action %s auto-approved (mode=hybrid, risk=low).", action_id
            )
            return True

        self._pending[action_id] = action
        logger.info(
            "Action %s queued for manual approval (mode=hybrid, risk=%s).",
            action_id,
            risk,
        )
        return False

    def approve(self, action_id: str) -> Action | None:
        """Manually approve a pending action.

        Args:
            action_id: The string representation of the action UUID.

        Returns:
            The approved Action, or None if no pending action matches.
        """
        action = self._pending.pop(action_id, None)
        if action is None:
            logger.warning("approve() called for unknown action %s.", action_id)
            return None

        action.status = ActionStatus.approved
        logger.info("Action %s manually approved.", action_id)
        return action

    def deny(self, action_id: str, reason: str = "") -> Action | None:
        """Deny a pending action, preventing its execution.

        Args:
            action_id: The string representation of the action UUID.
            reason: Human-readable explanation for the denial.

        Returns:
            The denied Action (with status set to ``failed``), or None if no
            pending action matches.
        """
        action = self._pending.pop(action_id, None)
        if action is None:
            logger.warning("deny() called for unknown action %s.", action_id)
            return None

        action.status = ActionStatus.failed
        action.result = {"denied": True, "reason": reason}
        logger.info("Action %s denied. Reason: %s", action_id, reason or "(none)")
        return action

    def get_pending(self) -> list[Action]:
        """Return all actions currently awaiting manual approval."""
        return list(self._pending.values())
