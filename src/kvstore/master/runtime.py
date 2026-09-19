import argparse
import heapq
import random
import socket
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field

from kvstore.common.clock import LogicalClock
from kvstore.common.config import (
    COMMUNICATION_DELAY_SECONDS,
    DEFAULT_MASTER_BIND_HOST,
    DEFAULT_MASTER_PORT,
    DEFAULT_SOCKET_TIMEOUT_SECONDS,
)
from kvstore.common.models import Message, MessageType, RegisterPayload, Task, WorkerStatus
from kvstore.common.protocol import ProtocolError, receive_message, send_message


TASK_COUNT = 5000
EXPECTED_WORKERS = 4


def generate_tasks(count: int = TASK_COUNT, rng: random.Random | None = None) -> list[Task]:
    if not 1 <= count <= 65_536:
        raise ValueError("task count must be between 1 and 65536")
    random_source = rng if rng is not None else random.Random()
    keys = random_source.sample(range(65_536), count)
    return [
        Task(
            task_id=f"task-{index:04d}",
            key=f"{key:04x}",
            value=random_source.randint(1, 100),
        )
        for index, key in enumerate(keys, start=1)
    ]


def _default_log(clock: float, node: str, event: str, status: str, message: str) -> None:
    print(f"[{clock:.2f}] {node} | {event} | {status} | {message}", flush=True)


@dataclass
class WorkerConnection:
    worker_id: str
    sock: socket.socket
    status: WorkerStatus
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    assigned_task_ids: set[str] = field(default_factory=set)
    connected: bool = True


class MasterRuntime:
    def __init__(
        self,
        host: str = DEFAULT_MASTER_BIND_HOST,
        port: int = DEFAULT_MASTER_PORT,
        task_count: int = TASK_COUNT,
        *,
        clock: LogicalClock | None = None,
        rng: random.Random | None = None,
        log=_default_log,
    ) -> None:
        self.host = host
        self.port = port
        self.task_count = task_count
        self.clock = clock if clock is not None else LogicalClock()
        self._rng = rng if rng is not None else random.Random()
        self._log = log
        self._condition = threading.Condition()
        self._workers: dict[str, WorkerConnection] = {}
        self._tasks: dict[str, Task] = {}
        self._task_state: dict[str, str] = {}
        self._normal_tasks: deque[str] = deque()
        self._retry_tasks: list[tuple[int, str]] = []
        self._retry_order = 0
        self._store: dict[str, int] = {}
        self._listener: socket.socket | None = None
        self._receiver_threads: list[threading.Thread] = []
        self._stopped = threading.Event()
        self._listening = threading.Event()
        self.address: tuple[str, int] | None = None

    @property
    def store(self) -> dict[str, int]:
        with self._condition:
            return dict(self._store)

    @property
    def workers(self) -> list[str]:
        with self._condition:
            return sorted(self._workers)

    def wait_until_listening(self, timeout: float = 5.0) -> bool:
        return self._listening.wait(timeout)

    def run(self) -> dict[str, int]:
        self._prepare_tasks()
        self._open_listener()
        try:
            self._accept_workers()
            self._start_receivers()
            self._distribute_tasks()
            self._send_terminate()
        finally:
            self.stop()
            for thread in self._receiver_threads:
                thread.join(timeout=2.0)
        self._emit("TERMINATE", "SUCCESS", f"Stored {len(self._store)} tasks.")
        return self.store

    def stop(self) -> None:
        self._stopped.set()
        with self._condition:
            self._condition.notify_all()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                self._listener = None
        for worker in list(self._workers.values()):
            try:
                worker.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                worker.connected = False
            try:
                worker.sock.close()
            except OSError:
                worker.connected = False

    def _prepare_tasks(self) -> None:
        tasks = generate_tasks(self.task_count, self._rng)
        with self._condition:
            for task in tasks:
                self._tasks[task.task_id] = task
                self._task_state[task.task_id] = "pending"
                self._normal_tasks.append(task.task_id)
        self._emit("INIT", "INFO", f"Created {len(tasks)} tasks.")

    def _open_listener(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(EXPECTED_WORKERS)
        self._listener = listener
        bound_host, bound_port = listener.getsockname()
        self.address = (str(bound_host), int(bound_port))
        self._listening.set()
        self._emit("INIT", "SUCCESS", f"Listening on {bound_host}:{bound_port}.")

    def _accept_workers(self) -> None:
        assert self._listener is not None
        while len(self._workers) < EXPECTED_WORKERS and not self._stopped.is_set():
            conn, _ = self._listener.accept()
            conn.settimeout(DEFAULT_SOCKET_TIMEOUT_SECONDS)
            request_id = "unknown"
            try:
                message = receive_message(conn)
                request_id = message.request_id
                self.clock.observe(message.logical_clock)
                worker = self._register_worker(conn, message)
                self._send(
                    worker,
                    MessageType.ACK,
                    {
                        "acknowledged_type": MessageType.REGISTER.value,
                        "worker_id": worker.worker_id,
                        "registered_workers": len(self._workers),
                    },
                    request_id=message.request_id,
                )
                conn.settimeout(None)
                self._emit("CONNECT", "SUCCESS", f"Registered {worker.worker_id}.")
            except (EOFError, OSError, ProtocolError, ValueError) as exc:
                self._remove_unconfirmed_worker(conn)
                self._send_registration_error(conn, request_id, exc)
                conn.close()

    def _remove_unconfirmed_worker(self, conn: socket.socket) -> None:
        with self._condition:
            for worker_id, worker in list(self._workers.items()):
                if worker.sock is conn:
                    del self._workers[worker_id]
                    return

    def _register_worker(self, conn: socket.socket, message: Message) -> WorkerConnection:
        if message.message_type is not MessageType.REGISTER:
            raise ValueError("first message must be REGISTER")
        payload = RegisterPayload.from_dict(message.payload)
        if payload.worker_id != message.sender_id:
            raise ValueError("sender_id and worker_id must match")
        with self._condition:
            if payload.worker_id in self._workers:
                raise ValueError(f"worker already registered: {payload.worker_id}")
            worker = WorkerConnection(
                worker_id=payload.worker_id,
                sock=conn,
                status=WorkerStatus(
                    worker_id=payload.worker_id,
                    queue_size=0,
                    queue_capacity=payload.queue_capacity,
                ),
            )
            self._workers[worker.worker_id] = worker
            return worker

    def _send_registration_error(self, conn: socket.socket, request_id: str, exc: Exception) -> None:
        self.clock.advance(COMMUNICATION_DELAY_SECONDS)
        response = Message(
            message_type=MessageType.ERROR,
            sender_id="master",
            request_id=request_id,
            logical_clock=self.clock.read(),
            payload={"error": str(exc)},
        )
        try:
            send_message(conn, response)
        except OSError:
            return

    def _start_receivers(self) -> None:
        for worker in list(self._workers.values()):
            thread = threading.Thread(
                target=self._receive_loop,
                args=(worker,),
                name=f"{worker.worker_id}-master-recv",
            )
            thread.start()
            self._receiver_threads.append(thread)

    def _receive_loop(self, worker: WorkerConnection) -> None:
        while not self._stopped.is_set():
            try:
                message = receive_message(worker.sock)
            except (EOFError, OSError, ProtocolError):
                self._disconnect_worker(worker)
                return

            self.clock.observe(message.logical_clock)
            if message.sender_id != worker.worker_id:
                self._emit("RECV", "WARN", f"Ignored sender mismatch from {worker.worker_id}.")
                continue
            try:
                self._handle_message(worker, message)
            except (KeyError, TypeError, ValueError) as exc:
                self._emit("RECV", "WARN", f"Invalid message from {worker.worker_id}: {exc}")

    def _handle_message(self, worker: WorkerConnection, message: Message) -> None:
        if message.message_type is MessageType.QUEUE_STATUS:
            status = WorkerStatus.from_dict(message.payload)
            if status.worker_id != worker.worker_id:
                raise ValueError("QUEUE_STATUS worker_id mismatch")
            with self._condition:
                worker.status = status
                self._condition.notify_all()
        elif message.message_type is MessageType.RESULT_SUCCESS:
            self._handle_success(worker, message.payload)
        elif message.message_type is MessageType.RESULT_FAIL:
            self._handle_failure(worker, message.payload)
        else:
            self._emit("RECV", "WARN", f"Unhandled message type: {message.message_type.value}.")

    def _handle_success(self, worker: WorkerConnection, payload: dict) -> None:
        task_id = str(payload["task_id"])
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None or self._task_state[task_id] == "done":
                return
            self._clear_assignment_locked(task_id)
            self._store[task.key] = task.value
            self._task_state[task_id] = "done"
            self._condition.notify_all()
        self._emit("RESULT", "SUCCESS", f"{task_id} completed by {worker.worker_id}.")

    def _handle_failure(self, worker: WorkerConnection, payload: dict) -> None:
        task_id = str(payload["task_id"])
        reason = str(payload.get("reason", "processing failure"))
        with self._condition:
            task = self._tasks.get(task_id)
            if task is None or self._task_state[task_id] == "done":
                return
            self._clear_assignment_locked(task_id)
            if reason != "queue overflow":
                task.attempt += 1
            task.previous_worker_id = worker.worker_id
            self._queue_retry_locked(task_id)
            self._condition.notify_all()
        self._emit("RESULT", "FAIL", f"{task_id} failed on {worker.worker_id}: {reason}.")

    def _clear_assignment_locked(self, task_id: str) -> None:
        for connection in self._workers.values():
            connection.assigned_task_ids.discard(task_id)

    def _queue_retry_locked(self, task_id: str) -> None:
        if self._task_state.get(task_id) == "retry":
            return
        self._retry_order += 1
        self._task_state[task_id] = "retry"
        heapq.heappush(self._retry_tasks, (self._retry_order, task_id))

    def _disconnect_worker(self, worker: WorkerConnection) -> None:
        with self._condition:
            if not worker.connected:
                return
            worker.connected = False
            for task_id in list(worker.assigned_task_ids):
                if self._task_state.get(task_id) == "assigned":
                    task = self._tasks[task_id]
                    task.previous_worker_id = worker.worker_id
                    self._queue_retry_locked(task_id)
            worker.assigned_task_ids.clear()
            self._condition.notify_all()
        self._emit("CONNECT", "FAIL", f"Lost connection to {worker.worker_id}.")

    def _distribute_tasks(self) -> None:
        while not self._stopped.is_set():
            with self._condition:
                if len(self._store) == self.task_count:
                    return
                if not any(worker.connected for worker in self._workers.values()):
                    raise RuntimeError("all Worker connections were lost")
                assignment = self._next_assignment_locked()
                if assignment is None:
                    self._condition.wait(timeout=0.5)
                    continue
                task, worker, priority = assignment

            try:
                self._send(
                    worker,
                    MessageType.TASK,
                    {"task": task.to_dict(), "priority": priority},
                )
                self._emit("DISTRIB", "INFO", f"Sent {task.task_id} to {worker.worker_id}.")
            except OSError:
                self._disconnect_worker(worker)

    def _next_assignment_locked(self) -> tuple[Task, WorkerConnection, bool] | None:
        task, priority = self._next_task_locked()
        if task is None:
            return None
        worker = self._choose_worker_locked(task)
        if worker is None:
            self._put_task_back_locked(task.task_id, priority)
            return None

        task.enqueued_at = self.clock.read()
        self._task_state[task.task_id] = "assigned"
        worker.assigned_task_ids.add(task.task_id)
        worker.status.queue_size += 1
        return task, worker, priority

    def _next_task_locked(self) -> tuple[Task | None, bool]:
        while self._retry_tasks:
            _, task_id = heapq.heappop(self._retry_tasks)
            if self._task_state.get(task_id) == "retry":
                return self._tasks[task_id], True
        while self._normal_tasks:
            task_id = self._normal_tasks.popleft()
            if self._task_state.get(task_id) == "pending":
                return self._tasks[task_id], False
        return None, False

    def _choose_worker_locked(self, task: Task) -> WorkerConnection | None:
        candidates = [
            worker
            for worker in self._workers.values()
            if worker.connected
            and worker.status.queue_size < worker.status.queue_capacity
            and worker.worker_id != task.previous_worker_id
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda worker: (-worker.status.available_capacity, worker.worker_id))
        return candidates[0]

    def _put_task_back_locked(self, task_id: str, priority: bool) -> None:
        if priority:
            self._retry_order += 1
            heapq.heappush(self._retry_tasks, (self._retry_order, task_id))
            self._task_state[task_id] = "retry"
        else:
            self._normal_tasks.appendleft(task_id)
            self._task_state[task_id] = "pending"

    def _send(
        self,
        worker: WorkerConnection,
        message_type: MessageType,
        payload: dict,
        *,
        request_id: str | None = None,
    ) -> None:
        with worker.send_lock:
            self.clock.advance(COMMUNICATION_DELAY_SECONDS)
            message = Message(
                message_type=message_type,
                sender_id="master",
                request_id=request_id if request_id is not None else str(uuid.uuid4()),
                logical_clock=self.clock.read(),
                payload=payload,
            )
            send_message(worker.sock, message)

    def _send_terminate(self) -> None:
        for worker in list(self._workers.values()):
            if not worker.connected:
                continue
            try:
                self._send(worker, MessageType.TERMINATE, {})
            except OSError:
                self._disconnect_worker(worker)

    def _emit(self, event: str, status: str, message: str) -> None:
        self._log(self.clock.read(), "MASTER", event, status, message)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="분산 KV Store Master 실행")
    parser.add_argument("--host", default=DEFAULT_MASTER_BIND_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_MASTER_PORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    MasterRuntime(host=args.host, port=args.port).run()


if __name__ == "__main__":
    main()
