import argparse
import random
import socket
import threading
import uuid

from kvstore.common.clock import LogicalClock
from kvstore.common.config import (
    COMMUNICATION_DELAY_SECONDS,
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
from kvstore.common.stats import StatsCollector
from kvstore.worker.p2p import P2PService
from kvstore.worker.ready_queue import WARN_THRESHOLD_RATIO, ReadyQueue

MIN_PROCESSING_SECONDS = 1.0
MAX_PROCESSING_SECONDS = 3.0
SUCCESS_PROBABILITY = 0.8
QUEUE_WAIT_SECONDS = 0.5


def _default_log(clock: float, node: str, event: str, status: str, message: str) -> None:
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
        peers: dict[str, WorkerEndpoint] | None = None,
        stats: StatsCollector | None = None,
    ) -> None:
        self.config = config
        self.queue = queue if queue is not None else ReadyQueue(capacity=DEFAULT_QUEUE_CAPACITY)
        self.clock = clock if clock is not None else LogicalClock()
        self._log = log
        # 테스트에서 성공/실패를 강제로 재현할 수 있도록 주입 가능하게 둔다.
        # 실제 실행 시에는 매번 새 random.Random()이 생성되어 무작위성이 유지된다.
        self._rng = rng or random.Random()
        self.stats = stats if stats is not None else StatsCollector()
        self._node_name = config.worker_id.capitalize()
        self._socket: socket.socket | None = None
        # 수신 Thread와 처리 Thread가 같은 TCP 연결로 메시지를 보낸다.
        self._send_lock = threading.Lock()
        self._shutdown = threading.Event()
        self._terminated_by_master = False
        self._p2p = P2PService(
            worker_id=config.worker_id,
            endpoint=WorkerEndpoint(host=config.p2p_host, port=config.p2p_port),
            peers=peers or {},
            queue=self.queue,
            clock=self.clock,
            log=log,
            stats=self.stats,
            rng=self._rng,
            queue_changed=self._send_queue_status,
        )

    def run(self) -> None:
        self._p2p.start()
        try:
            self._socket = self._connect_and_register()
            if self._shutdown.is_set():
                self._socket.close()
                return
            receiver = threading.Thread(
                target=self._receive_loop, name=f"{self.config.worker_id}-recv", daemon=True
            )
            processor = threading.Thread(
                target=self._process_loop, name=f"{self.config.worker_id}-proc", daemon=True
            )
            receiver.start()
            processor.start()

            receiver.join()
            self._shutdown.set()
            self.queue.wake_all()
            processor.join()
        finally:
            self._shutdown.set()
            self.queue.wake_all()
            self._p2p.stop()
            if self._socket is not None:
                self._socket.close()

        self._emit_final_stats()
        if self._terminated_by_master:
            self._emit("TERMINATE", "SUCCESS", f"{self.config.worker_id}가 정상 종료했습니다.")
        else:
            self._emit("TERMINATE", "FAIL", f"{self.config.worker_id}가 비정상 종료했습니다.")

    def stop(self) -> None:
        self._shutdown.set()
        self.queue.wake_all()
        self._p2p.stop()
        if self._socket is not None:
            try:
                self._socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                return

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
        self.clock.advance(COMMUNICATION_DELAY_SECONDS)
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
        if response.message_type != MessageType.ACK or response.request_id != request.request_id:
            sock.close()
            raise RuntimeError(f"Master가 등록을 거부했습니다: {response.payload}")

        self._emit(
            "CONNECT",
            "SUCCESS",
            f"Master 연결과 Ready Queue 초기화를 완료했습니다 (0/{self.queue.capacity}).",
        )
        # 프레임 일부를 읽은 뒤 timeout이 나면 다음 메시지 경계를 잃을 수 있다.
        sock.settimeout(None)
        return sock

    def _receive_loop(self) -> None:
        assert self._socket is not None
        while True:
            try:
                message = receive_message(self._socket)
            except (EOFError, ProtocolError, OSError):
                self._emit("RECV", "FAIL", "Master 연결이 끊어졌습니다.")
                return

            self.clock.observe(message.logical_clock)

            try:
                if message.message_type == MessageType.TASK:
                    self._handle_task(message)
                elif message.message_type == MessageType.TERMINATE:
                    self._terminated_by_master = True
                    self._emit("TERMINATE", "INFO", "Master의 종료 신호를 받았습니다.")
                    return
                else:
                    self._emit("RECV", "WARN", f"처리할 수 없는 메시지 유형입니다: {message.message_type}")
            except OSError:
                self._emit("RECV", "FAIL", "Master에 응답을 보내지 못했습니다.")
                self.stop()
                return

    def _handle_task(self, message: Message) -> None:
        task_payload = message.payload.get("task", {})
        priority = bool(message.payload.get("priority", False))
        try:
            task = Task.from_dict(task_payload)
        except (KeyError, ValueError, TypeError) as exc:
            self._emit("RECV", "FAIL", f"잘못된 작업 데이터를 거부했습니다: {exc}")
            return

        task.enqueued_at = self.clock.read()
        result = self.queue.try_enqueue(task, priority=priority)
        if not result.accepted:
            if result.reason == "duplicate":
                self._emit("QUEUE", "WARN", f"중복 작업을 무시했습니다: {task.task_id}.")
                self._send_queue_status()
                return
            self._emit(
                "QUEUE",
                "WARN",
                f"Queue가 가득 차서 작업을 거부했습니다 "
                f"({result.queue_size}/{result.queue_capacity}).",
            )
            self.stats.record_queue_overflow(self.config.worker_id)
            self._send_result_fail(task, reason="queue overflow")
            return

        self._emit(
            "RECV",
            "INFO",
            f"작업 수신: KV[{task.task_id}] (Key={task.key}, Value={task.value})",
        )
        if result.warn:
            self._emit(
                "QUEUE", "WARN", f"Queue 사용량이 70%를 초과했습니다: {result.queue_size}/{result.queue_capacity}."
            )
            if result.queue_size * 2 > 15:
                self._p2p.request_check()
        self._send_queue_status()

    def _process_loop(self) -> None:
        while not self._shutdown.is_set():
            task = self.queue.dequeue_for_processing(timeout=QUEUE_WAIT_SECONDS)
            if task is None:
                continue
            wait_seconds = max(0.0, self.clock.read() - task.enqueued_at)

            # 작업이 Ready Queue를 "나가는" 시점. 여전히 70% 초과 상태면 WARN을 남긴다
            # (과제 스펙: 70% 초과 상태에서 들고날 때마다 매번 기록).
            status = self.queue.snapshot_status(self.config.worker_id)
            if status.queue_size > status.queue_capacity * WARN_THRESHOLD_RATIO:
                self._emit(
                    "QUEUE",
                    "WARN",
                    f"작업을 꺼낸 뒤에도 Queue 사용량이 70%를 초과합니다: "
                    f"{status.queue_size}/{status.queue_capacity}.",
                )
            try:
                self._send_queue_status()
            except OSError:
                self._emit("PROC", "FAIL", "Master에 Queue 상태를 보내지 못했습니다.")
                self.stop()
                return

            self._emit("PROC", "INFO", f"KV[{task.task_id}] 처리를 시작합니다.")
            processing_seconds = self._rng.uniform(MIN_PROCESSING_SECONDS, MAX_PROCESSING_SECONDS)
            succeeded = self._rng.random() < SUCCESS_PROBABILITY

            # 실제로 기다리지 않고, 결과가 확정되는 이 시점에 논리 시계만 정확히 한 번 올린다.
            self.clock.advance(processing_seconds)

            try:
                if succeeded:
                    self._send_result_success(task, processing_seconds, wait_seconds)
                    self.stats.record_task_success(
                        self.config.worker_id,
                        wait_seconds,
                        event_id=f"success:{task.task_id}",
                    )
                else:
                    self._send_result_fail(
                        task,
                        reason="20% rule",
                        processing_seconds=processing_seconds,
                        wait_seconds=wait_seconds,
                    )
                    self.stats.record_task_fail(
                        self.config.worker_id,
                        wait_seconds,
                        event_id=f"fail:{task.task_id}:{task.attempt}",
                    )
                self.queue.mark_processing_done()
                self._send_queue_status()
            except OSError:
                self._emit("PROC", "FAIL", "Master에 처리 결과를 보내지 못했습니다.")
                self.stop()
                return

    def _send(self, message_type: MessageType, payload: dict) -> None:
        assert self._socket is not None
        with self._send_lock:
            self.clock.advance(COMMUNICATION_DELAY_SECONDS)
            message = Message(
                message_type=message_type,
                sender_id=self.config.worker_id,
                request_id=str(uuid.uuid4()),
                logical_clock=self.clock.read(),
                payload=payload,
            )
            send_message(self._socket, message)

    def _send_result_success(
        self, task: Task, processing_seconds: float, wait_seconds: float = 0.0
    ) -> None:
        self._send(
            MessageType.RESULT_SUCCESS,
            {
                "task_id": task.task_id,
                "key": task.key,
                "value": task.value,
                "worker_id": self.config.worker_id,
                "assignment_worker_id": task.assignment_worker_id or self.config.worker_id,
                "wait_seconds": wait_seconds,
            },
        )
        self._emit(
            "PROC", "SUCCESS", f"KV[{task.task_id}] 저장 완료. 처리시간={processing_seconds:.2f}s."
        )

    def _send_result_fail(
        self,
        task: Task,
        *,
        reason: str,
        processing_seconds: float | None = None,
        wait_seconds: float = 0.0,
    ) -> None:
        self._send(
            MessageType.RESULT_FAIL,
            {
                "task_id": task.task_id,
                "worker_id": self.config.worker_id,
                "assignment_worker_id": task.assignment_worker_id or self.config.worker_id,
                "reason": reason,
                "wait_seconds": wait_seconds,
            },
        )
        suffix = f" 처리시간={processing_seconds:.2f}s." if processing_seconds is not None else ""
        reason_text = {
            "20% rule": "20% 실패 규칙",
            "queue overflow": "Queue 용량 초과",
        }.get(reason, reason)
        self._emit("PROC", "FAIL", f"KV[{task.task_id}] 처리 실패 ({reason_text}).{suffix}")

    def _send_queue_status(self) -> None:
        if self._socket is None or self._shutdown.is_set():
            return
        status = self.queue.snapshot_status(self.config.worker_id)
        worker_stats = self.stats.get_worker_stats(self.config.worker_id)
        payload = status.to_dict()
        payload["p2p_statistics"] = {
            "sent_events": worker_stats.p2p_transfers_sent,
            "sent_tasks": worker_stats.p2p_tasks_sent,
            "received_events": worker_stats.p2p_transfers_received,
            "received_tasks": worker_stats.p2p_tasks_received,
        }
        self._send(MessageType.QUEUE_STATUS, payload)

    def _emit_final_stats(self) -> None:
        worker = self.stats.get_worker_stats(self.config.worker_id)
        self._emit(
            "STAT",
            "INFO",
            f"처리량={worker.throughput}, 성공={worker.success_count}, "
            f"실패={worker.fail_count}, 평균대기시간={worker.average_wait_seconds:.2f}s, "
            f"P2P 송신={worker.p2p_transfers_sent}, 수신={worker.p2p_transfers_received}.",
        )


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
