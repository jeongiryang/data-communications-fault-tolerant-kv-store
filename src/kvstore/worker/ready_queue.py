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
    reason: str | None = None


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
        self._reserved: dict[str, tuple[Task, bool]] = {}
        self._active_task_ids: set[str] = set()
        self._processing_task_id: str | None = None

    @property
    def capacity(self) -> int:
        return self._capacity

    def _size_locked(self) -> int:
        return len(self._normal) + len(self._priority) + len(self._reserved)

    def _warn_locked(self) -> bool:
        return self._size_locked() > self._capacity * WARN_THRESHOLD_RATIO

    def _result_locked(self, accepted: bool, reason: str | None = None) -> QueueChangeResult:
        return QueueChangeResult(
            accepted=accepted,
            queue_size=self._size_locked(),
            queue_capacity=self._capacity,
            warn=self._warn_locked(),
            reason=reason,
        )

    def try_enqueue(self, task: Task, *, priority: bool = False) -> QueueChangeResult:
        with self._lock:
            if task.task_id in self._active_task_ids:
                return self._result_locked(accepted=False, reason="duplicate")
            if self._size_locked() >= self._capacity:
                return self._result_locked(accepted=False, reason="full")
            (self._priority if priority else self._normal).append(task)
            self._active_task_ids.add(task.task_id)
            self._not_empty.notify()
            return self._result_locked(accepted=True)

    def try_enqueue_many(self, tasks: list[Task]) -> QueueChangeResult:
        """P2P로 받은 작업을 모두 넣을 수 있을 때만 한 번에 추가한다."""
        with self._lock:
            task_ids = [task.task_id for task in tasks]
            if len(task_ids) != len(set(task_ids)):
                return self._result_locked(accepted=False, reason="duplicate")
            if any(task_id in self._active_task_ids for task_id in task_ids):
                return self._result_locked(accepted=False, reason="duplicate")
            if self._size_locked() + len(tasks) > self._capacity:
                return self._result_locked(accepted=False, reason="full")
            self._normal.extend(tasks)
            self._active_task_ids.update(task_ids)
            self._not_empty.notify_all()
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
            if self._processing_task_id is not None:
                self._active_task_ids.discard(self._processing_task_id)
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

    def peek_transfer_candidates(self, count: int) -> list[Task]:
        if count <= 0:
            raise ValueError("count must be positive")
        with self._lock:
            candidates = list(self._normal)[-count:]
            if len(candidates) < count:
                needed = count - len(candidates)
                candidates.extend(list(self._priority)[-needed:])
            return candidates

    def reserve_for_transfer(self, task_id: str) -> Task | None:
        # ACK 전에는 소유권을 유지하되 처리 스레드가 가져가지 못하게 예약한다.
        with self._lock:
            for source, priority in ((self._normal, False), (self._priority, True)):
                for task in list(source):
                    if task.task_id == task_id:
                        source.remove(task)
                        self._reserved[task_id] = (task, priority)
                        return task
            return None

    def confirm_removed(self, task_id: str) -> QueueChangeResult:
        with self._lock:
            removed = self._reserved.pop(task_id, None)
            if removed is not None:
                self._active_task_ids.discard(task_id)
            return self._result_locked(accepted=removed is not None)

    def release_reservation(self, task_id: str) -> QueueChangeResult:
        # 전송 실패 시 일반 작업과 재할당 작업의 우선순위를 그대로 복구한다.
        with self._lock:
            reserved = self._reserved.pop(task_id, None)
            if reserved is not None:
                task, priority = reserved
                target = self._priority if priority else self._normal
                target.appendleft(task)
                self._not_empty.notify()
            return self._result_locked(accepted=reserved is not None)

    def wake_all(self) -> None:
        with self._not_empty:
            self._not_empty.notify_all()
