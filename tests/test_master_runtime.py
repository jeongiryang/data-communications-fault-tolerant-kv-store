import random
import socket
import threading
import unittest
import uuid

from kvstore.common.clock import LogicalClock
from kvstore.common.models import (
    Message,
    MessageType,
    RegisterPayload,
    Task,
    WorkerEndpoint,
    WorkerStatus,
)
from kvstore.common.protocol import receive_message, send_message
from kvstore.master.runtime import MasterRuntime, WorkerConnection, generate_tasks


class MasterRuntimeTests(unittest.TestCase):
    def test_generates_5000_unique_valid_tasks(self):
        tasks = generate_tasks(5000, random.Random(10))

        self.assertEqual(len(tasks), 5000)
        self.assertEqual(len({task.key for task in tasks}), 5000)
        self.assertTrue(all(len(task.key) == 4 for task in tasks))
        self.assertTrue(all(1 <= task.value <= 100 for task in tasks))

    def test_retry_task_is_selected_before_normal_task(self):
        master = MasterRuntime(task_count=3, rng=random.Random(1), log=lambda *args: None)
        master._prepare_tasks()
        with master._condition:
            master._queue_retry_locked("task-0002")
            task, priority = master._next_task_locked()

        self.assertEqual(task.task_id, "task-0002")
        self.assertTrue(priority)

    def test_selects_most_available_worker_and_avoids_previous_worker(self):
        master = MasterRuntime(task_count=1, log=lambda *args: None)
        sockets = [socket.socketpair() for _ in range(4)]
        try:
            workers = [
                WorkerConnection(
                    worker_id=f"worker{index}",
                    sock=pair[0],
                    status=WorkerStatus(
                        worker_id=f"worker{index}",
                        queue_size=queue_size,
                    ),
                    connected=connected,
                )
                for index, (pair, queue_size, connected) in enumerate(
                    zip(sockets, [0, 3, 10, 0], [True, True, True, False]),
                    start=1,
                )
            ]
            master._workers = {worker.worker_id: worker for worker in workers}
            normal_task = Task(task_id="normal", key="0001", value=1)
            retry_task = Task(
                task_id="retry",
                key="0002",
                value=2,
                previous_worker_id="worker1",
            )

            with master._condition:
                normal_worker = master._choose_worker_locked(normal_task)
                retry_worker = master._choose_worker_locked(retry_task)

            self.assertEqual(normal_worker.worker_id, "worker1")
            self.assertEqual(retry_worker.worker_id, "worker2")
        finally:
            for left, right in sockets:
                left.close()
                right.close()

    def test_distributes_retries_and_terminates(self):
        assignments: list[tuple[str, str, bool]] = []
        master_logs: list[tuple[float, str, str, str, str]] = []
        assignment_lock = threading.Lock()
        failed_once = threading.Event()
        worker_errors: list[Exception] = []
        master = MasterRuntime(
            host="127.0.0.1",
            port=0,
            task_count=24,
            rng=random.Random(3),
            log=lambda *args: master_logs.append(args),
        )

        master_thread = threading.Thread(target=master.run)
        master_thread.start()
        self.assertTrue(master.wait_until_listening())
        host, port = master.address

        def run_worker(worker_id: str, p2p_port: int) -> None:
            clock = LogicalClock()
            sock = socket.create_connection((host, port), timeout=5.0)

            def send(message_type: MessageType, payload: dict, request_id: str | None = None):
                clock.advance(1.0)
                send_message(
                    sock,
                    Message(
                        message_type=message_type,
                        sender_id=worker_id,
                        request_id=request_id or str(uuid.uuid4()),
                        logical_clock=clock.read(),
                        payload=payload,
                    ),
                )

            try:
                registration = RegisterPayload(
                    worker_id=worker_id,
                    p2p_endpoint=WorkerEndpoint(host="127.0.0.1", port=p2p_port),
                )
                request_id = str(uuid.uuid4())
                send(MessageType.REGISTER, registration.to_dict(), request_id)
                ack = receive_message(sock)
                self.assertEqual(ack.message_type, MessageType.ACK)
                self.assertEqual(ack.request_id, request_id)
                sock.settimeout(None)

                while True:
                    message = receive_message(sock)
                    clock.observe(message.logical_clock)
                    if message.message_type is MessageType.TERMINATE:
                        return
                    if message.message_type is not MessageType.TASK:
                        continue

                    task = Task.from_dict(message.payload["task"])
                    priority = bool(message.payload.get("priority", False))
                    with assignment_lock:
                        assignments.append((task.task_id, worker_id, priority))

                    send(
                        MessageType.QUEUE_STATUS,
                        {
                            "worker_id": worker_id,
                            "queue_size": 0,
                            "queue_capacity": 10,
                            "processing_task_id": task.task_id,
                        },
                    )
                    if task.task_id == "task-0001" and not failed_once.is_set():
                        failed_once.set()
                        send(
                            MessageType.RESULT_FAIL,
                            {
                                "task_id": task.task_id,
                                "worker_id": worker_id,
                                "reason": "20% rule",
                            },
                        )
                    else:
                        send(
                            MessageType.RESULT_SUCCESS,
                            {
                                "task_id": task.task_id,
                                "key": task.key,
                                "value": task.value,
                                "worker_id": worker_id,
                            },
                        )
            except Exception as exc:
                worker_errors.append(exc)
            finally:
                sock.close()

        worker_threads = [
            threading.Thread(target=run_worker, args=(f"worker{index}", 6000 + index))
            for index in range(1, 5)
        ]
        for thread in worker_threads:
            thread.start()
        for thread in worker_threads:
            thread.join(timeout=10)
        master_thread.join(timeout=10)

        self.assertFalse(master_thread.is_alive())
        self.assertTrue(all(not thread.is_alive() for thread in worker_threads))
        self.assertEqual(worker_errors, [])
        self.assertEqual(len(master.store), 24)
        task_one_assignments = [item for item in assignments if item[0] == "task-0001"]
        self.assertEqual(len(task_one_assignments), 2)
        self.assertNotEqual(task_one_assignments[0][1], task_one_assignments[1][1])
        self.assertTrue(task_one_assignments[1][2])
        self.assertFalse(
            any(
                event == "CONNECT" and status == "FAIL"
                for _, _, event, status, _ in master_logs
            )
        )
        self.assertTrue(
            any(
                event == "PROGRESS" and message == "작업 처리 진행률: 24/24"
                for _, _, event, _, message in master_logs
            )
        )


if __name__ == "__main__":
    unittest.main()
