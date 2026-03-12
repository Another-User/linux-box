"""Action executor for OptAware agent.

Executes action plans step by step, with dry-run support and full
stdout/stderr capture.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
import subprocess
from datetime import datetime, timezone
from typing import Any

from models.actions import Action, ActionStep, ActionStatus

logger = logging.getLogger(__name__)


class Executor:
    """Execute action plans step by step with dry-run support."""

    async def execute(self, action: Action, dry_run: bool = True) -> Action:
        """Run each step in an action plan and update the action's status.

        In dry-run mode every step is logged but no command is actually run.
        The action transitions through: pending -> executing -> completed/failed.

        Args:
            action: The Action to execute (mutated in place).
            dry_run: When True, commands are logged but not executed.

        Returns:
            The updated Action with final status and result data.
        """
        logger.info(
            "Starting execution of action %s (dry_run=%s, steps=%d)",
            action.id,
            dry_run,
            len(action.steps),
        )

        action.status = ActionStatus.executing
        action.started_at = datetime.now(timezone.utc)

        step_results: list[dict[str, Any]] = []
        all_succeeded = True

        for step in action.steps:
            success, output = await self._execute_step(step, dry_run=dry_run)
            step_results.append(
                {
                    "step_number": step.step_number,
                    "description": step.description,
                    "command": step.command,
                    "success": success,
                    "output": output,
                }
            )
            if not success:
                all_succeeded = False
                logger.error(
                    "Step %d failed for action %s: %s",
                    step.step_number,
                    action.id,
                    output,
                )
                break

        action.completed_at = datetime.now(timezone.utc)
        action.status = ActionStatus.completed if all_succeeded else ActionStatus.failed
        action.result = {
            "dry_run": dry_run,
            "steps": step_results,
            "succeeded": all_succeeded,
        }

        logger.info(
            "Action %s finished with status=%s", action.id, action.status
        )
        return action

    async def _execute_step(
        self, step: ActionStep, dry_run: bool = True
    ) -> tuple[bool, str]:
        """Execute a single step and return (success, combined_output).

        If the step has no command the step is considered a no-op success.
        """
        if step.command is None:
            msg = f"Step {step.step_number} ({step.description!r}): no command, skipping."
            logger.info(msg)
            return True, msg

        if dry_run:
            msg = (
                f"[DRY RUN] Step {step.step_number} ({step.description!r}): "
                f"would execute: {step.command!r}"
            )
            logger.info(msg)
            return True, msg

        logger.info(
            "Executing step %d (%r): %s",
            step.step_number,
            step.description,
            step.command,
        )
        returncode, stdout, stderr = await asyncio.to_thread(
            self._execute_command, step.command
        )
        combined = self._format_output(returncode, stdout, stderr)
        success = returncode == 0

        if success:
            logger.info(
                "Step %d succeeded (rc=%d).", step.step_number, returncode
            )
        else:
            logger.warning(
                "Step %d failed (rc=%d): %s", step.step_number, returncode, stderr
            )

        return success, combined

    def _execute_command(
        self, cmd: str, timeout: int = 60
    ) -> tuple[int, str, str]:
        """Run *cmd* in a subprocess and return (returncode, stdout, stderr).

        Args:
            cmd: The shell command string to execute.
            timeout: Maximum seconds to wait for the process to finish.

        Returns:
            A 3-tuple of (returncode, stdout_text, stderr_text).
        """
        try:
            args = shlex.split(cmd)
        except ValueError:
            # Fall back to shell execution for complex expressions
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr

        try:
            result = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired:
            logger.error("Command timed out after %d seconds: %r", timeout, cmd)
            return -1, "", f"TimeoutExpired: command exceeded {timeout}s limit."
        except FileNotFoundError as exc:
            logger.error("Command not found: %r — %s", cmd, exc)
            return 127, "", f"FileNotFoundError: {exc}"
        except OSError as exc:
            logger.error("OS error running command %r: %s", cmd, exc)
            return -1, "", f"OSError: {exc}"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _format_output(returncode: int, stdout: str, stderr: str) -> str:
        """Combine returncode, stdout, and stderr into a single string."""
        parts: list[str] = [f"returncode={returncode}"]
        if stdout.strip():
            parts.append(f"stdout:\n{stdout.rstrip()}")
        if stderr.strip():
            parts.append(f"stderr:\n{stderr.rstrip()}")
        return "\n".join(parts)
