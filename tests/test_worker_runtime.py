import socket
import threading
import unittest
import uuid

from kvstore.common.clock import LogicalClock
from kvstore.common.config import WorkerConfig
from kvstore.common.models import Message, MessageType, Task
from kvstore.common.protocol import receive_message, send_message
from kvstore.worker.ready_queue import ReadyQueue
from kvstore.worker.runtime import WorkerRuntime


class _ScriptedRandom:
    """처리시간과 성공 여부를 고정해서 테스트를 결정적으로 만든다."""

    def __init__(self, uniform_value: float, random_value: float) -> None:
        self._uniform_value = uniform_value
        self._random_value = random_value

    def uniform(self, a: float, b: float) -> float:  # noqa: ARG002 - 시그니처만 맞춘다
        return self._uniform_value

    def random(self) -> float:
        return self._random_value


def _terminate_message() -> Message:
    return Message(
        message_type=MessageType.TERMINATE,
        sender_id="master",
        request_id=str(uuid.uuid4()),
        payload={},
    )


def _task_message(task_id: str, key: str = "a3f7", value: int = 42) -> Message:
    return Message(
        message_type=MessageType.TASK,
        sender_id="master",
        request_id=str(uuid.uuid4()),
        payload={
            "task": Task(task_id=task_id, key=key, value=value).to_dict(),
            "priority": False,
        },
    )


def _start_fake_master(script):
    """REGISTER -> ACK까지 처리한 뒤 나머지는 script(conn)에 맡기는 1회용 TCP 서버.
    (host, port, server_socket, thread)를 돌려주며, 호출자가 정리한다."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()

    def _run() -> None:
        try:
            conn, _ = server.accept()
        except OSError:
            return
        conn.settimeout(5.0)  # script가 잘못돼도 테스트가 무한정 멈추지 않게 한다
        try:
            register = receive_message(conn)
            assert register.message_type is MessageType.REGISTER
            send_message(
                conn,
                Message(
                    message_type=MessageType.ACK,
                    sender_id="master",
                    request_id=register.request_id,
                    payload={},
                ),
            )
            script(conn)
        except OSError:
            pass
        finally:
            conn.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return host, port, server, thread


class WorkerRuntimeTests(unittest.TestCase):
    def _run_worker(self, script, *, worker_id: str = "worker-test", rng: _ScriptedRandom):
        host, port, server, master_thread = _start_fake_master(script)
        config = WorkerConfig(
            worker_id=worker_id,
            master_host=host,
            master_port=port,
            p2p_host="127.0.0.1",
            p2p_port=6001,  # 이 테스트에서 실제로 bind하지 않는 더미 값
        )
        worker = WorkerRuntime(
            config, queue=ReadyQueue(capacity=10), clock=LogicalClock(), rng=rng
        )
        worker_thread = threading.Thread(target=worker.run, daemon=True)
        worker_thread.start()
        worker_thread.join(timeout=5)
        server.close()
        master_thread.join(timeout=2)
        self.assertFalse(worker_thread.is_alive(), "worker.run()이 제한 시간 안에 끝나지 않았다")
        return worker

    def test_task_success_flow(self):
        received: list[Message] = []

        def script(conn):
            send_message(conn, _task_message("task-1"))
            while True:
                message = receive_message(conn)
                received.append(message)
                if message.message_type in (MessageType.RESULT_SUCCESS, MessageType.RESULT_FAIL):
                    break
            send_message(conn, _terminate_message())

        self._run_worker(script, rng=_ScriptedRandom(uniform_value=2.0, random_value=0.1))

        successes = [m for m in received if m.message_type == MessageType.RESULT_SUCCESS]
        self.assertEqual(len(successes), 1)
        self.assertEqual(successes[0].payload["task_id"], "task-1")
        self.assertEqual(successes[0].payload["worker_id"], "worker-test")

    def test_task_failure_flow(self):
        received: list[Message] = []

        def script(conn):
            send_message(conn, _task_message("task-2", key="beef", value=7))
            while True:
                message = receive_message(conn)
                received.append(message)
                if message.message_type in (MessageType.RESULT_SUCCESS, MessageType.RESULT_FAIL):
                    break
            send_message(conn, _terminate_message())

        self._run_worker(script, rng=_ScriptedRandom(uniform_value=1.5, random_value=0.95))

        failures = [m for m in received if m.message_type == MessageType.RESULT_FAIL]
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].payload["task_id"], "task-2")
        self.assertEqual(failures[0].payload["reason"], "20% rule")

    def test_multiple_worker_instances_run_independently(self):
        # Worker ID/포트만 바꿔 같은 코드를 여러 개 실행할 수 있어야 한다는 요구사항 검증.
        results: dict[str, list[Message]] = {"worker-a": [], "worker-b": []}

        def make_script(worker_key: str, task_id: str):
            def script(conn):
                send_message(conn, _task_message(task_id))
                while True:
                    message = receive_message(conn)
                    results[worker_key].append(message)
                    if message.message_type == MessageType.RESULT_SUCCESS:
                        break
                send_message(conn, _terminate_message())

            return script

        host_a, port_a, server_a, master_a = _start_fake_master(make_script("worker-a", "task-a"))
        host_b, port_b, server_b, master_b = _start_fake_master(make_script("worker-b", "task-b"))

        def run(worker_id: str, host: str, port: int, p2p_port: int) -> None:
            config = WorkerConfig(
                worker_id=worker_id,
                master_host=host,
                master_port=port,
                p2p_host="127.0.0.1",
                p2p_port=p2p_port,  # 이 테스트에서 실제로 bind하지 않는 더미 값
            )
            WorkerRuntime(
                config,
                queue=ReadyQueue(capacity=10),
                clock=LogicalClock(),
                rng=_ScriptedRandom(uniform_value=1.0, random_value=0.1),
            ).run()

        thread_a = threading.Thread(target=run, args=("worker-a", host_a, port_a, 6001))
        thread_b = threading.Thread(target=run, args=("worker-b", host_b, port_b, 6002))
        thread_a.start()
        thread_b.start()
        thread_a.join(timeout=5)
        thread_b.join(timeout=5)
        server_a.close()
        server_b.close()
        master_a.join(timeout=2)
        master_b.join(timeout=2)

        self.assertFalse(thread_a.is_alive())
        self.assertFalse(thread_b.is_alive())
        success_a = [m for m in results["worker-a"] if m.message_type == MessageType.RESULT_SUCCESS]
        success_b = [m for m in results["worker-b"] if m.message_type == MessageType.RESULT_SUCCESS]
        self.assertEqual(success_a[0].payload["worker_id"], "worker-a")
        self.assertEqual(success_b[0].payload["worker_id"], "worker-b")


if __name__ == "__main__":
    unittest.main()