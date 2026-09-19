import random
import socket
import threading
import uuid
from collections.abc import Callable

from kvstore.common.clock import LogicalClock
from kvstore.common.config import COMMUNICATION_DELAY_SECONDS, DEFAULT_SOCKET_TIMEOUT_SECONDS
from kvstore.common.models import Message, MessageType, Task, WorkerEndpoint
from kvstore.common.protocol import ProtocolError, receive_message, send_message
from kvstore.common.stats import StatsCollector
from kvstore.worker.ready_queue import ReadyQueue


OVERLOAD_WAIT_SECONDS = 15
MIN_TRANSFER_COUNT = 1
MAX_TRANSFER_COUNT = 3


class P2PService:
    def __init__(
        self,
        worker_id: str,
        endpoint: WorkerEndpoint,
        peers: dict[str, WorkerEndpoint],
        queue: ReadyQueue,
        clock: LogicalClock,
        log: Callable[[float, str, str, str, str], None],
        stats: StatsCollector,
        *,
        rng: random.Random | None = None,
        queue_changed: Callable[[], None] | None = None,
    ) -> None:
        self.worker_id = worker_id
        self.endpoint = endpoint
        self.peers = {peer_id: peer for peer_id, peer in peers.items() if peer_id != worker_id}
        self.queue = queue
        self.clock = clock
        self._log = log
        self._stats = stats
        self._rng = rng if rng is not None else random.Random()
        self._queue_changed = queue_changed
        self._shutdown = threading.Event()
        self._check_requested = threading.Event()
        self._listener: socket.socket | None = None
        self._server_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._handled_transfers: dict[str, dict] = {}
        self._handled_lock = threading.Lock()

    def start(self, *, monitor: bool = True) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.endpoint.host, self.endpoint.port))
        listener.listen()
        listener.settimeout(0.2)
        self._listener = listener
        self._server_thread = threading.Thread(
            target=self._serve, name=f"{self.worker_id}-p2p-server", daemon=True
        )
        self._server_thread.start()
        if monitor and self.peers:
            self._monitor_thread = threading.Thread(
                target=self._monitor, name=f"{self.worker_id}-p2p-monitor", daemon=True
            )
            self._monitor_thread.start()
        self._emit(
            "CONNECT",
            "SUCCESS",
            f"{self.endpoint.host}:{self.endpoint.port}에서 P2P 연결을 기다립니다.",
        )

    def stop(self) -> None:
        self._shutdown.set()
        self._check_requested.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        current = threading.current_thread()
        for thread in (self._server_thread, self._monitor_thread):
            if thread is not None and thread is not current:
                thread.join(timeout=1.0)

    def request_check(self) -> None:
        self._check_requested.set()

    def _monitor(self) -> None:
        while not self._shutdown.is_set():
            self._check_requested.wait(0.05)
            self._check_requested.clear()
            if self._shutdown.is_set():
                return
            status = self.queue.snapshot_status(self.worker_id)
            if status.estimated_wait_seconds <= OVERLOAD_WAIT_SECONDS:
                continue
            check_interval = self._rng.uniform(1.0, 3.0)
            self.clock.advance(check_interval)
            self.balance_once()

    def balance_once(self) -> bool:
        status = self.queue.snapshot_status(self.worker_id)
        if status.estimated_wait_seconds <= OVERLOAD_WAIT_SECONDS:
            return False

        self._emit(
            "LB",
            "WARN",
            f"예상 대기시간이 {status.estimated_wait_seconds}초로 15초를 초과하여 P2P 부하 분산을 시작합니다.",
        )
        peer_statuses: list[tuple[int, str]] = []
        for peer_id, endpoint in self.peers.items():
            response = self._request(
                endpoint,
                MessageType.P2P_STATUS_REQUEST,
                {"worker_id": self.worker_id},
            )
            if response is None or response.message_type is not MessageType.P2P_STATUS_RESPONSE:
                continue
            if response.sender_id != peer_id:
                continue
            available = int(response.payload.get("available_capacity", 0))
            self._emit("LB", "INFO", f"{peer_id}의 Queue 여유 공간: {available}/10.")
            if available > 0:
                peer_statuses.append((available, peer_id))

        if not peer_statuses:
            self._emit("LB", "WARN", "작업을 받을 수 있는 이웃 Worker가 없습니다.")
            return False

        peer_statuses.sort(key=lambda item: (-item[0], item[1]))
        available, peer_id = peer_statuses[0]
        transfer_count = min(self._rng.randint(MIN_TRANSFER_COUNT, MAX_TRANSFER_COUNT), available)
        candidates = self.queue.peek_transfer_candidates(transfer_count)
        reserved: list[Task] = []
        for task in candidates:
            selected = self.queue.reserve_for_transfer(task.task_id)
            if selected is not None:
                reserved.append(selected)

        if not reserved:
            return False

        task_ids = [task.task_id for task in reserved]
        request_id = str(uuid.uuid4())
        self._emit("LB", "INFO", f"{peer_id}에 작업 {len(reserved)}개를 이전합니다: {task_ids}.")
        response = self._request(
            self.peers[peer_id],
            MessageType.P2P_TRANSFER,
            {"tasks": [task.to_dict() for task in reserved]},
            request_id=request_id,
        )
        accepted = (
            response is not None
            and response.message_type is MessageType.ACK
            and response.sender_id == peer_id
            and response.request_id == request_id
            and response.payload.get("acknowledged_type") == MessageType.P2P_TRANSFER.value
            and response.payload.get("accepted") is True
            and set(response.payload.get("task_ids", [])) == set(task_ids)
        )
        if not accepted:
            for task in reserved:
                self.queue.release_reservation(task.task_id)
            self._emit("LB", "FAIL", f"{peer_id}로 P2P 이전하지 못해 작업을 Queue에 복구했습니다.")
            return False

        for task in reserved:
            self.queue.confirm_removed(task.task_id)
        self._stats.record_p2p_transfer(
            self.worker_id,
            peer_id,
            len(reserved),
            event_id=f"p2p:{request_id}",
        )
        self._notify_queue_changed()
        self._emit("LB", "SUCCESS", f"{peer_id}의 ACK를 확인하고 작업을 제거했습니다: {task_ids}.")
        return True

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._shutdown.is_set():
            try:
                conn, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(DEFAULT_SOCKET_TIMEOUT_SECONDS)
                try:
                    self._handle_connection(conn)
                except (EOFError, OSError, ProtocolError, TypeError, ValueError) as exc:
                    self._emit("LB", "FAIL", f"잘못된 P2P 요청을 거부했습니다: {exc}.")

    def _handle_connection(self, conn: socket.socket) -> None:
        message = receive_message(conn)
        self.clock.observe(message.logical_clock)
        if self.peers and message.sender_id not in self.peers:
            raise ValueError(f"unknown P2P sender: {message.sender_id}")
        if message.message_type is MessageType.P2P_STATUS_REQUEST:
            status = self.queue.snapshot_status(self.worker_id)
            self._send_response(
                conn,
                MessageType.P2P_STATUS_RESPONSE,
                message.request_id,
                {
                    "worker_id": self.worker_id,
                    "queue_size": status.queue_size,
                    "queue_capacity": status.queue_capacity,
                    "available_capacity": status.available_capacity,
                },
            )
            return
        if message.message_type is not MessageType.P2P_TRANSFER:
            raise ValueError(f"unsupported P2P message: {message.message_type.value}")

        with self._handled_lock:
            previous = self._handled_transfers.get(message.request_id)
        if previous is not None:
            self._send_response(conn, MessageType.ACK, message.request_id, previous)
            return

        raw_tasks = message.payload.get("tasks")
        if not isinstance(raw_tasks, list) or not MIN_TRANSFER_COUNT <= len(raw_tasks) <= MAX_TRANSFER_COUNT:
            raise ValueError("P2P transfer must contain 1 to 3 tasks")
        tasks = [Task.from_dict(task) for task in raw_tasks]
        for task in tasks:
            task.enqueued_at = self.clock.read()
        result = self.queue.try_enqueue_many(tasks)
        task_ids = [task.task_id for task in tasks]
        payload = {
            "acknowledged_type": MessageType.P2P_TRANSFER.value,
            "accepted": result.accepted,
            "task_ids": task_ids if result.accepted else [],
            "reason": result.reason,
        }
        with self._handled_lock:
            self._handled_transfers[message.request_id] = payload
        self._send_response(conn, MessageType.ACK, message.request_id, payload)
        if result.accepted:
            self._notify_queue_changed()
            self._emit(
                "LB",
                "SUCCESS",
                f"{message.sender_id}의 작업 {len(tasks)}개를 받고 ACK를 보냈습니다.",
            )
        else:
            self._emit("LB", "WARN", f"{message.sender_id}의 P2P 이전을 거부했습니다: {result.reason}.")

    def _request(
        self,
        endpoint: WorkerEndpoint,
        message_type: MessageType,
        payload: dict,
        *,
        request_id: str | None = None,
    ) -> Message | None:
        request_id = request_id if request_id is not None else str(uuid.uuid4())
        try:
            with socket.create_connection(
                (endpoint.host, endpoint.port), timeout=DEFAULT_SOCKET_TIMEOUT_SECONDS
            ) as sock:
                sock.settimeout(DEFAULT_SOCKET_TIMEOUT_SECONDS)
                self.clock.advance(COMMUNICATION_DELAY_SECONDS)
                send_message(
                    sock,
                    Message(
                        message_type=message_type,
                        sender_id=self.worker_id,
                        request_id=request_id,
                        logical_clock=self.clock.read(),
                        payload=payload,
                    ),
                )
                response = receive_message(sock)
                self.clock.observe(response.logical_clock)
                if response.request_id != request_id:
                    return None
                return response
        except (EOFError, OSError, ProtocolError, ValueError):
            return None

    def _send_response(
        self,
        conn: socket.socket,
        message_type: MessageType,
        request_id: str,
        payload: dict,
    ) -> None:
        self.clock.advance(COMMUNICATION_DELAY_SECONDS)
        send_message(
            conn,
            Message(
                message_type=message_type,
                sender_id=self.worker_id,
                request_id=request_id,
                logical_clock=self.clock.read(),
                payload=payload,
            ),
        )

    def _notify_queue_changed(self) -> None:
        if self._queue_changed is None:
            return
        try:
            self._queue_changed()
        except OSError:
            return

    def _emit(self, event: str, status: str, message: str) -> None:
        self._log(self.clock.read(), self.worker_id.capitalize(), event, status, message)
