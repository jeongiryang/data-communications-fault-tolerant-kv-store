import threading


class LogicalClock:
    """실제 시간을 기다리지 않고 과제의 가상 시간을 기록한다."""

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
        """다른 노드의 시각을 반영하되 현재 시각보다 작아지지 않게 한다."""
        if remote_value < 0:
            raise ValueError("remote clock value must be non-negative")
        with self._lock:
            self._value = max(self._value, float(remote_value))
            return self._value

    def reset(self) -> None:
        with self._lock:
            self._value = 0.0
