"""Thread-safe logical clock used by all simulated events."""

from __future__ import annotations

import threading


class LogicalClock:
    """A monotonic, process-local simulation clock.

    The clock deliberately does not read wall-clock time or sleep. Callers advance
    it when a simulated processing or communication event occurs.
    """

    def __init__(self, initial: float = 0.0) -> None:
        if initial < 0:
            raise ValueError("initial clock value must be non-negative")
        self._value = float(initial)
        self._lock = threading.Lock()

    def read(self) -> float:
        with self._lock:
            return self._value

    def advance(self, seconds: float) -> float:
        if seconds < 0:
            raise ValueError("clock cannot advance by a negative duration")
        with self._lock:
            self._value += float(seconds)
            return self._value

    def observe(self, remote_value: float) -> float:
        """Merge a clock value received from another node without going backward."""
        if remote_value < 0:
            raise ValueError("remote clock value must be non-negative")
        with self._lock:
            self._value = max(self._value, float(remote_value))
            return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = 0.0
