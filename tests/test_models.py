import unittest

from kvstore.common.models import (
    Message,
    MessageType,
    RegisterPayload,
    Task,
    WorkerEndpoint,
    WorkerStatus,
)


class ModelTests(unittest.TestCase):
    def test_task_round_trip_normalizes_hex_key(self) -> None:
        task = Task(task_id="task-1", key="A3F7", value=42)
        self.assertEqual(Task.from_dict(task.to_dict()), task)
        self.assertEqual(task.key, "a3f7")

    def test_invalid_task_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Task(task_id="task-1", key="not-hex", value=42)
        with self.assertRaises(ValueError):
            Task(task_id="task-1", key="a3f7", value=101)

    def test_wait_estimate_matches_assignment_formula(self) -> None:
        status = WorkerStatus(worker_id="worker-1", queue_size=8)
        self.assertEqual(status.available_capacity, 2)
        self.assertEqual(status.estimated_wait_seconds, 16)

    def test_registration_and_message_round_trip(self) -> None:
        registration = RegisterPayload(
            worker_id="worker-1",
            p2p_endpoint=WorkerEndpoint("192.0.2.10", 6001),
        )
        message = Message(
            message_type=MessageType.REGISTER,
            sender_id="worker-1",
            request_id="request-1",
            payload=registration.to_dict(),
        )
        restored = Message.from_dict(message.to_dict())
        self.assertEqual(restored, message)
        self.assertEqual(RegisterPayload.from_dict(restored.payload), registration)


if __name__ == "__main__":
    unittest.main()
