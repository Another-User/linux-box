"""Anomaly detector — statistical detection over rolling metric windows.

Uses a z-score method on per-metric rolling histories and also applies
pattern-based heuristics for well-known problem signatures such as
steadily-increasing memory (memory leak indicator).
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# Default number of samples kept per metric
_DEFAULT_WINDOW_SIZE = 60

# Sensitivity → z-score threshold mapping.
# Higher sensitivity → lower z-score threshold → easier to trigger.
# Sensitivity is clamped to [0.0, 1.0].
_SENSITIVITY_TO_ZSCORE: list[tuple[float, float]] = [
    (0.0, 5.0),   # insensitive — only extreme outliers
    (0.25, 3.5),
    (0.5, 3.0),   # moderate (common 3-sigma rule)
    (0.7, 2.5),   # default
    (0.85, 2.0),
    (1.0, 1.5),   # very sensitive
]


def _sensitivity_to_threshold(sensitivity: float) -> float:
    """Linearly interpolate a z-score threshold from a [0,1] sensitivity."""
    sensitivity = max(0.0, min(1.0, sensitivity))
    # Walk the table and interpolate between the two bounding entries
    for i in range(len(_SENSITIVITY_TO_ZSCORE) - 1):
        s0, z0 = _SENSITIVITY_TO_ZSCORE[i]
        s1, z1 = _SENSITIVITY_TO_ZSCORE[i + 1]
        if s0 <= sensitivity <= s1:
            t = (sensitivity - s0) / (s1 - s0)
            return z0 + t * (z1 - z0)
    # Edge cases
    if sensitivity <= _SENSITIVITY_TO_ZSCORE[0][0]:
        return _SENSITIVITY_TO_ZSCORE[0][1]
    return _SENSITIVITY_TO_ZSCORE[-1][1]


class AnomalyDetector:
    """Detect statistical anomalies in streaming metric samples.

    Parameters
    ----------
    window_size:
        Number of historical samples to retain per metric name.
        Smaller windows react faster; larger windows are more stable.
    """

    def __init__(self, window_size: int = _DEFAULT_WINDOW_SIZE) -> None:
        self._window_size = window_size
        self._history: dict[str, deque[float]] = {}

    # ------------------------------------------------------------------
    # Sample management
    # ------------------------------------------------------------------

    def add_sample(self, metric_name: str, value: float) -> None:
        """Append *value* to the rolling history for *metric_name*.

        The oldest sample is evicted automatically once the window is full.
        """
        if metric_name not in self._history:
            self._history[metric_name] = deque(maxlen=self._window_size)
        self._history[metric_name].append(value)

    def get_history(self, metric_name: str) -> list[float]:
        """Return a copy of the current history for *metric_name*."""
        if metric_name not in self._history:
            return []
        return list(self._history[metric_name])

    def clear(self, metric_name: Optional[str] = None) -> None:
        """Clear history.  If *metric_name* is None, clear all histories."""
        if metric_name is None:
            self._history.clear()
        else:
            self._history.pop(metric_name, None)

    # ------------------------------------------------------------------
    # Anomaly detection
    # ------------------------------------------------------------------

    def is_anomalous(
        self,
        metric_name: str,
        value: float,
        sensitivity: float = 0.7,
    ) -> tuple[bool, str]:
        """Determine whether *value* is anomalous for *metric_name*.

        The check uses the z-score of *value* relative to the rolling
        history.  A minimum of 5 samples is required before any anomaly
        can be flagged (too few samples → insufficient baseline).

        Returns
        -------
        (is_anomaly, reason)
            *reason* is a human-readable explanation or empty string when
            no anomaly was detected.
        """
        history = self._history.get(metric_name)
        if history is None or len(history) < 5:
            return False, ""

        samples = list(history)
        mean = statistics.mean(samples)

        # Use population stdev over the window (not sample stdev) because
        # we treat the window as the full reference distribution.
        try:
            stdev = statistics.pstdev(samples)
        except statistics.StatisticsError:
            return False, ""

        if stdev == 0.0:
            # All values identical — a deviation from the constant is anomalous
            if value != mean:
                return (
                    True,
                    f"{metric_name}: value {value} deviates from constant baseline {mean}",
                )
            return False, ""

        z = abs((value - mean) / stdev)
        threshold = _sensitivity_to_threshold(sensitivity)

        if z > threshold:
            direction = "above" if value > mean else "below"
            return (
                True,
                (
                    f"{metric_name}: value {value:.4g} is {direction} mean "
                    f"({mean:.4g} ± {stdev:.4g}), z={z:.2f} > threshold {threshold:.2f}"
                ),
            )
        return False, ""

    # ------------------------------------------------------------------
    # Trend detection
    # ------------------------------------------------------------------

    def detect_trend(self, metric_name: str) -> str:
        """Return the trend direction for *metric_name*.

        Uses a simple linear regression slope over the rolling window.

        Returns
        -------
        "increasing" | "decreasing" | "stable"
        """
        history = self._history.get(metric_name)
        if history is None or len(history) < 3:
            return "stable"

        samples = list(history)
        n = len(samples)
        xs = list(range(n))

        x_mean = sum(xs) / n
        y_mean = sum(samples) / n

        numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, samples))
        denominator = sum((x - x_mean) ** 2 for x in xs)

        if denominator == 0.0:
            return "stable"

        slope = numerator / denominator

        # Normalise slope relative to the mean so that the threshold is
        # independent of the metric's absolute magnitude.
        if y_mean != 0.0:
            relative_slope = slope / abs(y_mean)
        else:
            relative_slope = slope

        if relative_slope > 0.01:
            return "increasing"
        if relative_slope < -0.01:
            return "decreasing"
        return "stable"

    # ------------------------------------------------------------------
    # Pattern-based heuristics
    # ------------------------------------------------------------------

    def detect_patterns(self, metric_name: str) -> list[str]:
        """Return a list of detected bad-pattern descriptions.

        Currently implemented patterns:

        * **memory_leak** — ``memory.*`` metrics that trend increasing over
          the full window without a single reset (drop).
        * **cpu_saturation** — CPU metric consistently above 85 % for the
          last 10 samples.
        * **disk_filling** — disk-usage metric with strictly monotone
          increase over the last 10 samples.
        * **oscillating** — metric alternates direction more than 6 times
          in the last 10 samples (thrashing / flapping).
        """
        history = self._history.get(metric_name)
        if history is None or len(history) < 5:
            return []

        samples = list(history)
        patterns: list[str] = []
        name_lower = metric_name.lower()

        # --- Memory-leak pattern ---
        if "memory" in name_lower or "mem" in name_lower or "rss" in name_lower:
            trend = self.detect_trend(metric_name)
            if trend == "increasing":
                # Check there is no significant drop (>5 %) in the window
                min_val = min(samples)
                max_val = max(samples)
                if max_val > 0 and (max_val - min_val) / max_val < 0.95:
                    # Values never dropped back to near the minimum
                    if samples[-1] > statistics.mean(samples):
                        patterns.append(
                            f"{metric_name}: steadily increasing memory — possible memory leak"
                        )

        # --- CPU saturation pattern ---
        if "cpu" in name_lower:
            recent = samples[-10:] if len(samples) >= 10 else samples
            if all(v > 85.0 for v in recent):
                patterns.append(
                    f"{metric_name}: CPU consistently above 85% for {len(recent)} samples"
                )

        # --- Disk filling pattern ---
        if "disk" in name_lower or "usage" in name_lower:
            recent = samples[-10:] if len(samples) >= 10 else samples
            if len(recent) >= 3:
                strictly_increasing = all(
                    recent[i] <= recent[i + 1] for i in range(len(recent) - 1)
                )
                if strictly_increasing and recent[-1] > recent[0]:
                    patterns.append(
                        f"{metric_name}: disk usage increasing monotonically "
                        f"({recent[0]:.1f}% → {recent[-1]:.1f}%)"
                    )

        # --- Oscillation / flapping pattern ---
        recent = samples[-10:] if len(samples) >= 10 else samples
        if len(recent) >= 4:
            direction_changes = 0
            for i in range(1, len(recent) - 1):
                prev_dir = math.copysign(1, recent[i] - recent[i - 1])
                next_dir = math.copysign(1, recent[i + 1] - recent[i])
                if prev_dir != next_dir and recent[i] != recent[i - 1]:
                    direction_changes += 1
            if direction_changes >= 6:
                patterns.append(
                    f"{metric_name}: oscillating / flapping "
                    f"({direction_changes} direction changes in last {len(recent)} samples)"
                )

        return patterns
