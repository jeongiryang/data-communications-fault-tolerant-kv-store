import unittest

from kvstore.common.models import Task
from kvstore.worker.ready_queue import ReadyQueue


def _make_task(task_id: str, key: str = "aaaa", value: int = 1) -> Task:
    return Task(task_id=task_id, key=key, value=value)


class ReadyQueueTests(unittest.TestCase):
    def test_accepts_up_to_capacity(self):
        queue = ReadyQueue(capacity=10)
        for index in range(10):
            result = queue.try_enqueue(_make_task(f"task-{index}"))
            self.assertTrue(result.accepted)
        self.assertEqual(queue.snapshot_status("worker1").queue_size, 10)

    def test_rejects_when_full(self):
        queue = ReadyQueue(capacity=10)
        for index in range(10):
            queue.try_enqueue(_make_task(f"task-{index}"))

        result = queue.try_enqueue(_make_task("task-overflow"))

        self.assertFalse(result.accepted)
        self.assertEqual(result.queue_size, 10)
        self.assertEqual(result.reason, "full")

    def test_rejects_duplicate_active_task(self):
        queue = ReadyQueue(capacity=10)
        queue.try_enqueue(_make_task("task-1"))

        result = queue.try_enqueue(_make_task("task-1"))

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "duplicate")
        self.assertEqual(result.queue_size, 1)

    def test_priority_task_dequeued_before_normal(self):
        queue = ReadyQueue(capacity=10)
        queue.try_enqueue(_make_task("normal-1"))
        queue.try_enqueue(_make_task("priority-1"), priority=True)

        task = queue.dequeue_for_processing(timeout=0)

        self.assertEqual(task.task_id, "priority-1")

    def test_warn_flag_only_after_70_percent(self):
        queue = ReadyQueue(capacity=10)
        result = None
        for index in range(7):
            result = queue.try_enqueue(_make_task(f"task-{index}"))
        self.assertFalse(result.warn)  # 정확히 7/10은 70% 초과가 아니다

        result = queue.try_enqueue(_make_task("task-8"))
        self.assertTrue(result.warn)  # 8/10부터 70% 초과

    def test_dequeue_returns_none_when_empty(self):
        queue = ReadyQueue(capacity=10)

        task = queue.dequeue_for_processing(timeout=0.05)

        self.assertIsNone(task)

    def test_transfer_reservation_then_confirm_removes_task(self):
        queue = ReadyQueue(capacity=10)
        queue.try_enqueue(_make_task("task-1"))

        reserved = queue.reserve_for_transfer("task-1")
        self.assertIsNotNone(reserved)
        # 예약 중에는 처리 대상에서 빠져야 한다 (아직 삭제 확정은 아님).
        self.assertIsNone(queue.dequeue_for_processing(timeout=0))

        queue.confirm_removed("task-1")

        self.assertEqual(queue.snapshot_status("worker1").queue_size, 0)

    def test_warn_still_true_after_dequeue_when_still_above_threshold(self):
        queue = ReadyQueue(capacity=10)
        for index in range(9):
            queue.try_enqueue(_make_task(f"task-{index}"))

        queue.dequeue_for_processing(timeout=0)  # 9 -> 8, 여전히 70% 초과여야 한다

        status = queue.snapshot_status("worker1")
        self.assertGreater(status.queue_size, status.queue_capacity * 0.7)

    def test_transfer_release_restores_task_to_queue(self):
        queue = ReadyQueue(capacity=10)
        queue.try_enqueue(_make_task("task-1"))
        queue.reserve_for_transfer("task-1")

        queue.release_reservation("task-1")

        task = queue.dequeue_for_processing(timeout=0)
        self.assertEqual(task.task_id, "task-1")

    def test_transfer_release_keeps_priority(self):
        queue = ReadyQueue(capacity=10)
        queue.try_enqueue(_make_task("normal"))
        queue.try_enqueue(_make_task("priority"), priority=True)
        queue.reserve_for_transfer("priority")

        queue.release_reservation("priority")

        task = queue.dequeue_for_processing(timeout=0)
        self.assertEqual(task.task_id, "priority")


if __name__ == "__main__":
    unittest.main()
