"""최길웅 담당: Worker Ready Queue.

과제 규격 요약
- 최대 크기 10, Thread-safe.
- 가득 차면 새 작업을 거부한다 (accepted=False, 큐는 그대로 유지).
- 재할당(priority) 작업은 일반 작업보다 먼저 처리한다.
- Queue가 70%(7개)를 초과한 상태에서 작업이 들고 날 때마다 WARN 여부를 알려준다.
- P2P 이전(배준희 담당)이 쓸 최소 인터페이스: reserve_for_transfer / confirm_removed /
  release_reservation. protocol-v0.1.md 규칙대로 ACK 받기 전까지는 큐에서 실제로
  지우지 않고 reserved 영역에 보관한다.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass

from kvstore.common.models import Task, WorkerStatus

WARN_THRESHOLD_RATIO = 0.7


@dataclass
class QueueChangeResult:
    accepted: bool
    queue_size: int
    queue_capacity: int
    warn: bool


class ReadyQueue:
    def __init__(self, capacity: int = 10) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._lock = threading.Lock()
        # Condition이 self._lock을 그대로 쓰기 때문에, enqueue 쪽에서 self._lock을
        # 잡고 notify해도 처리 스레드의 wait()과 안전하게 맞물린다.
        self._not_empty = threading.Condition(self._lock)
        self._normal: deque[Task] = deque()
        self._priority: deque[Task] = deque()
        self._reserved: dict[str, Task] = {}
        self._processing_task_id: str | None = None

    @property
    def capacity(self) -> int:
        return self._capacity

    def _size_locked(self) -> int:
        return len(self._normal) + len(self._priority) + len(self._reserved)

    def _warn_locked(self) -> bool:
        return self._size_locked() > self._capacity * WARN_THRESHOLD_RATIO

    def _result_locked(self, accepted: bool) -> QueueChangeResult:
        return QueueChangeResult(
            accepted=accepted,
            queue_size=self._size_locked(),
            queue_capacity=self._capacity,
            warn=self._warn_locked(),
        )

    def try_enqueue(self, task: Task, *, priority: bool = False) -> QueueChangeResult:
        with self._lock:
            if self._size_locked() >= self._capacity:
                return self._result_locked(accepted=False)
            (self._priority if priority else self._normal).append(task)
            self._not_empty.notify()
            return self._result_locked(accepted=True)

    def dequeue_for_processing(self, timeout: float | None = None) -> Task | None:
        # timeout을 짧게 주고 주기적으로 깨어나서 종료 신호(shutdown event)를 확인할 수 있게 한다.
        with self._not_empty:
            if not self._priority and not self._normal:
                self._not_empty.wait(timeout=timeout)
            task = None
            if self._priority:
                task = self._priority.popleft()
            elif self._normal:
                task = self._normal.popleft()
            if task is not None:
                self._processing_task_id = task.task_id
            return task

    def mark_processing_done(self) -> QueueChangeResult:
        with self._lock:
            self._processing_task_id = None
            return self._result_locked(accepted=True)

    def snapshot_status(self, worker_id: str) -> WorkerStatus:
        with self._lock:
            return WorkerStatus(
                worker_id=worker_id,
                queue_size=self._size_locked(),
                queue_capacity=self._capacity,
                processing_task_id=self._processing_task_id,
            )

    # ---- 아래부터는 P2P 부하 분산(배준희 담당)이 사용하는 최소 인터페이스 ----

    def peek_transfer_candidates(self, count: int) -> list[Task]:
        with self._lock:
            return list(self._normal)[-count:]

    def reserve_for_transfer(self, task_id: str) -> Task | None:
        # 송신 쪽은 ACK 받기 전까지 큐에서 완전히 지우면 안 되므로, 일단 reserved
        # 영역으로 옮겨서 처리 대상에서만 제외시킨다 (아직 삭제 확정은 아님).
        with self._lock:
            for source in (self._normal, self._priority):
                for task in list(source):
                    if task.task_id == task_id:
                        source.remove(task)
                        self._reserved[task_id] = task
                        return task
            return None

    def confirm_removed(self, task_id: str) -> None:
        with self._lock:
            self._reserved.pop(task_id, None)

    def release_reservation(self, task_id: str) -> None:
        # 거절/timeout/연결 오류 시 예약을 풀고 원래 큐로 되돌린다.
        with self._lock:
            task = self._reserved.pop(task_id, None)
            if task is not None:
                self._normal.appendleft(task)
                self._not_empty.notify()

    def wake_all(self) -> None:
        with self._not_empty:
            self._not_empty.notify_all()