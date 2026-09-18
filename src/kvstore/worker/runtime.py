"""최길웅 담당: Worker 메인 실행 흐름.

Master에 REGISTER -> 메시지 수신 Thread + 작업 처리 Thread 분리 -> TERMINATE까지 처리한다.
같은 코드를 --worker-id/--p2p-port만 바꿔서 실행하면 Worker1~4가 된다.
"""
from __future__ import annotations

import argparse
import random
import socket
import threading
import uuid

from kvstore.common.clock import LogicalClock
from kvstore.common.config import (
    DEFAULT_QUEUE_CAPACITY,
    DEFAULT_SOCKET_TIMEOUT_SECONDS,
    WorkerConfig,
)
from kvstore.common.models import (
    Message,
    MessageType,
    RegisterPayload,
    Task,
    WorkerEndpoint,
)
from kvstore.common.protocol import ProtocolError, receive_message, send_message
from kvstore.worker.ready_queue import ReadyQueue

MIN_PROCESSING_SECONDS = 1.0
MAX_PROCESSING_SECONDS = 3.0
SUCCESS_PROBABILITY = 0.8
POLL_TIMEOUT_SECONDS = 0.5


def _default_log(clock: float, node: str, event: str, status: str, message: str) -> None:
    # 배준희 담당의 공통 Logger가 완성되면 이 함수 대신 교체해서 쓴다.
    # 지금은 과제가 요구하는 로그 포맷만 맞춰 콘솔에 출력한다.
    print(f"[{clock:.2f}] {node} | {event} | {status} | {message}")


class WorkerRuntime:
    def __init__(
        self,
        config: WorkerConfig,
        *,
        queue: ReadyQueue | None = None,
        clock: LogicalClock | None = None,
        log=_default_log,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.queue = queue or ReadyQueue(capacity=DEFAULT_QUEUE_CAPACITY)
        self.clock = clock or LogicalClock()
        self._log = log
        # 테스트에서 성공/실패를 강제로 재현할 수 있도록 주입 가능하게 둔다.
        # 실제 실행 시에는 매번 새 random.Random()이 생성되어 무작위성이 유지된다.
        self._rng = rng or random.Random()
        self._node_name = config.worker_id.upper()
        self._socket: socket.socket | None = None
        self._send_lock = threading.Lock()  # recv 스레드와 처리 스레드가 같은 소켓을 공유해서 필요
        self._shutdown = threading.Event()

    def run(self) -> None:
        self._socket = self._connect_and_register()
        receiver = threading.Thread(
            target=self._receive_loop, name=f"{self.config.worker_id}-recv", daemon=True
        )
        processor = threading.Thread(
            target=self._process_loop, name=f"{self.config.worker_id}-proc", daemon=True
        )
        receiver.start()
        processor.start()

        receiver.join()  # TERMINATE 수신 또는 연결 끊김으로 돌아올 때까지 대기
        self._shutdown.set()
        self.queue.wake_all()
        processor.join()

        if self._socket is not None:
            self._socket.close()
        self._emit("TERMINATE", "SUCCESS", f"{self.config.worker_id} gracefully disconnected.")

    def _emit(self, event: str, status: str, message: str) -> None:
        self._log(self.clock.read(), self._node_name, event, status, message)

    def _connect_and_register(self) -> socket.socket:
        sock = socket.create_connection(
            (self.config.master_host, self.config.master_port),
            timeout=DEFAULT_SOCKET_TIMEOUT_SECONDS,
        )
        registration = RegisterPayload(
            worker_id=self.config.worker_id,
            p2p_endpoint=WorkerEndpoint(host=self.config.p2p_host, port=self.config.p2p_port),
            queue_capacity=self.queue.capacity,
        )
        request = Message(
            message_type=MessageType.REGISTER,
            sender_id=self.config.worker_id,
            request_id=str(uuid.uuid4()),
            logical_clock=self.clock.read(),
            payload=registration.to_dict(),
        )
        send_message(sock, request)
        response = receive_message(sock)
        self.clock.observe(response.logical_clock)
        if response.message_type == MessageType.ERROR:
            sock.close()
            raise RuntimeError(f"registration rejected by Master: {response.payload}")

        self._emit(
            "CONNECT",
            "SUCCESS",
            f"Connected to Master. Ready Queue initialized (0/{self.queue.capacity}).",
        )
        # 등록 이후 recv 루프는 짧은 timeout으로 주기적으로 깨어나며 blocking 대기한다.
        sock.settimeout(POLL_TIMEOUT_SECONDS)
        return sock

    def _receive_loop(self) -> None:
        assert self._socket is not None
        while True:
            try:
                message = receive_message(self._socket)
            except socket.timeout:
                continue
            except (EOFError, ProtocolError, OSError):
                self._emit("RECV", "FAIL", "Connection to Master lost.")
                return

            self.clock.observe(message.logical_clock)

            if message.message_type == MessageType.TASK:
                self._handle_task(message)
            elif message.message_type == MessageType.TERMINATE:
                self._emit("TERMINATE", "INFO", "Termination signal received from Master.")
                return
            else:
                self._emit("RECV", "WARN", f"Unhandled message type: {message.message_type}")

    def _handle_task(self, message: Message) -> None:
        task_payload = message.payload.get("task", {})
        priority = bool(message.payload.get("priority", False))
        try:
            task = Task.from_dict(task_payload)
        except (KeyError, ValueError, TypeError) as exc:
            self._emit("RECV", "FAIL", f"Invalid task payload rejected: {exc}")
            return

        result = self.queue.try_enqueue(task, priority=priority)
        if not result.accepted:
            self._emit(
                "QUEUE",
                "WARN",
                f"Queue full ({result.queue_size}/{result.queue_capacity}). "
                f"New task request rejected.",
            )
            self._send_result_fail(task, reason="queue overflow")
            return

        self._emit(
            "RECV",
            "INFO",
            f"Received task: KV[{task.task_id}] (Key={task.key}, Value={task.value})",
        )
        if result.warn:
            self._emit(
                "QUEUE", "WARN", f"Queue nearing full: {result.queue_size}/{result.queue_capacity}."
            )
        self._send_queue_status()

    def _process_loop(self) -> None:
        while not self._shutdown.is_set():
            task = self.queue.dequeue_for_processing(timeout=POLL_TIMEOUT_SECONDS)
            if task is None:
                continue

            self._emit("PROC", "INFO", f"Processing KV[{task.task_id}]...")
            processing_seconds = self._rng.uniform(MIN_PROCESSING_SECONDS, MAX_PROCESSING_SECONDS)
            succeeded = self._rng.random() < SUCCESS_PROBABILITY

            # 실제로 기다리지 않고, 결과가 확정되는 이 시점에 논리 시계만 정확히 한 번 올린다.
            self.clock.advance(processing_seconds)

            if succeeded:
                self._send_result_success(task, processing_seconds)
            else:
                self._send_result_fail(
                    task, reason="20% rule", processing_seconds=processing_seconds
                )
            self.queue.mark_processing_done()
            self._send_queue_status()

    def _send(self, message_type: MessageType, payload: dict) -> None:
        message = Message(
            message_type=message_type,
            sender_id=self.config.worker_id,
            request_id=str(uuid.uuid4()),
            logical_clock=self.clock.read(),
            payload=payload,
        )
        assert self._socket is not None
        with self._send_lock:
            send_message(self._socket, message)

    def _send_result_success(self, task: Task, processing_seconds: float) -> None:
        self._send(
            MessageType.RESULT_SUCCESS,
            {
                "task_id": task.task_id,
                "key": task.key,
                "value": task.value,
                "worker_id": self.config.worker_id,
            },
        )
        self._emit(
            "PROC", "SUCCESS", f"KV[{task.task_id}] stored. time={processing_seconds:.2f}s."
        )

    def _send_result_fail(
        self, task: Task, *, reason: str, processing_seconds: float | None = None
    ) -> None:
        self._send(
            MessageType.RESULT_FAIL,
            {"task_id": task.task_id, "worker_id": self.config.worker_id, "reason": reason},
        )
        suffix = f" time={processing_seconds:.2f}s." if processing_seconds is not None else ""
        self._emit("PROC", "FAIL", f"KV[{task.task_id}] FAILED ({reason}).{suffix}")

    def _send_queue_status(self) -> None:
        status = self.queue.snapshot_status(self.config.worker_id)
        self._send(MessageType.QUEUE_STATUS, status.to_dict())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="분산 KV Store Worker 실행")
    parser.add_argument("--worker-id", required=True, help="예: worker1")
    parser.add_argument("--master-host", required=True)
    parser.add_argument("--master-port", type=int, required=True)
    parser.add_argument("--p2p-host", default="127.0.0.1")
    parser.add_argument("--p2p-port", type=int, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    config = WorkerConfig(
        worker_id=args.worker_id,
        master_host=args.master_host,
        master_port=args.master_port,
        p2p_host=args.p2p_host,
        p2p_port=args.p2p_port,
    )
    WorkerRuntime(config).run()


if __name__ == "__main__":
    main()