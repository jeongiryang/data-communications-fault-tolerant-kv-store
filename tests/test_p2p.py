import random
import socket
import unittest
import uuid

from kvstore.common.clock import LogicalClock
from kvstore.common.models import Message, MessageType, Task, WorkerEndpoint
from kvstore.common.stats import StatsCollector
from kvstore.worker.p2p import P2PService
from kvstore.worker.ready_queue import ReadyQueue


def _free_endpoint() -> WorkerEndpoint:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return WorkerEndpoint("127.0.0.1", port)


def _task(number: int) -> Task:
    return Task(task_id=f"task-{number}", key=f"{number:04x}", value=number + 1)


def _quiet_log(clock, node, event, status, message):
    return None


class _FixedRandom(random.Random):
    def uniform(self, a, b):
        return 1.0

    def randint(self, a, b):
        return 2


class P2PTests(unittest.TestCase):
    def test_overloaded_worker_moves_tasks_after_ack(self):
        sender_queue = ReadyQueue()
        receiver_queue = ReadyQueue()
        for number in range(8):
            sender_queue.try_enqueue(_task(number))

        sender_endpoint = _free_endpoint()
        receiver_endpoint = _free_endpoint()
        stats = StatsCollector()
        receiver = P2PService(
            "worker2",
            receiver_endpoint,
            {},
            receiver_queue,
            LogicalClock(),
            _quiet_log,
            stats,
        )
        sender = P2PService(
            "worker1",
            sender_endpoint,
            {"worker2": receiver_endpoint},
            sender_queue,
            LogicalClock(),
            _quiet_log,
            stats,
            rng=_FixedRandom(),
        )
        receiver.start(monitor=False)
        sender.start(monitor=False)
        try:
            self.assertTrue(sender.balance_once())
            self.assertEqual(sender_queue.snapshot_status("worker1").queue_size, 6)
            self.assertEqual(receiver_queue.snapshot_status("worker2").queue_size, 2)
            self.assertEqual(stats.summary(0)["total_p2p_events"], 1)
        finally:
            sender.stop()
            receiver.stop()

    def test_failed_ack_keeps_tasks_in_sender_queue(self):
        sender_queue = ReadyQueue()
        for number in range(8):
            sender_queue.try_enqueue(_task(number))
        peer_endpoint = _free_endpoint()
        sender = P2PService(
            "worker1",
            _free_endpoint(),
            {"worker2": peer_endpoint},
            sender_queue,
            LogicalClock(),
            _quiet_log,
            StatsCollector(),
            rng=_FixedRandom(),
        )

        def fake_request(endpoint, message_type, payload, *, request_id=None):
            if message_type is MessageType.P2P_STATUS_REQUEST:
                return Message(
                    MessageType.P2P_STATUS_RESPONSE,
                    "worker2",
                    request_id or str(uuid.uuid4()),
                    {"available_capacity": 10},
                )
            return None

        sender._request = fake_request
        self.assertFalse(sender.balance_once())
        self.assertEqual(sender_queue.snapshot_status("worker1").queue_size, 8)

    def test_duplicate_transfer_request_is_only_enqueued_once(self):
        receiver_queue = ReadyQueue()
        receiver_endpoint = _free_endpoint()
        sender = P2PService(
            "worker1",
            _free_endpoint(),
            {"worker2": receiver_endpoint},
            ReadyQueue(),
            LogicalClock(),
            _quiet_log,
            StatsCollector(),
        )
        receiver = P2PService(
            "worker2",
            receiver_endpoint,
            {},
            receiver_queue,
            LogicalClock(),
            _quiet_log,
            StatsCollector(),
        )
        receiver.start(monitor=False)
        request_id = "same-transfer"
        payload = {"tasks": [_task(1).to_dict()]}
        try:
            first = sender._request(
                receiver_endpoint,
                MessageType.P2P_TRANSFER,
                payload,
                request_id=request_id,
            )
            second = sender._request(
                receiver_endpoint,
                MessageType.P2P_TRANSFER,
                payload,
                request_id=request_id,
            )
            self.assertTrue(first.payload["accepted"])
            self.assertTrue(second.payload["accepted"])
            self.assertEqual(receiver_queue.snapshot_status("worker2").queue_size, 1)
        finally:
            receiver.stop()


if __name__ == "__main__":
    unittest.main()
