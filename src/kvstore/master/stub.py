import argparse
import socketserver
import threading
from dataclasses import dataclass

from kvstore.common.config import DEFAULT_MASTER_BIND_HOST, DEFAULT_MASTER_PORT
from kvstore.common.models import Message, MessageType, RegisterPayload
from kvstore.common.protocol import ProtocolError, receive_message, send_message


@dataclass
class RegisteredWorker:
    worker_id: str
    host: str
    port: int


class RegistrationState:
    def __init__(self) -> None:
        self._workers: dict[str, RegisteredWorker] = {}
        self._lock = threading.Lock()

    def register(self, payload: RegisterPayload) -> RegisteredWorker:
        worker = RegisteredWorker(
            worker_id=payload.worker_id,
            host=payload.p2p_endpoint.host,
            port=payload.p2p_endpoint.port,
        )
        with self._lock:
            existing = self._workers.get(worker.worker_id)
            if existing is not None and existing != worker:
                raise ValueError(f"worker_id already registered: {worker.worker_id}")
            self._workers[worker.worker_id] = worker
        return worker

    def snapshot(self) -> dict[str, RegisteredWorker]:
        with self._lock:
            return dict(self._workers)


class RegistrationHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        server = self.server
        request_id = "unknown"
        try:
            message = receive_message(self.request)
            request_id = message.request_id
            if message.message_type is not MessageType.REGISTER:
                raise ValueError("the first message must be REGISTER")
            registration = RegisterPayload.from_dict(message.payload)
            worker = server.registration_state.register(registration)
            response = Message(
                message_type=MessageType.ACK,
                sender_id="master",
                request_id=message.request_id,
                payload={
                    "acknowledged_type": MessageType.REGISTER.value,
                    "worker_id": worker.worker_id,
                    "registered_workers": len(server.registration_state.snapshot()),
                },
            )
            send_message(self.request, response)
        except (EOFError, ProtocolError, ValueError) as exc:
            response = Message(
                message_type=MessageType.ERROR,
                sender_id="master",
                request_id=request_id,
                payload={"error": str(exc)},
            )
            try:
                send_message(self.request, response)
            except OSError:
                # 상대 노드가 이미 연결을 닫았다면 오류 응답을 보낼 수 없으므로 종료한다.
                return


class RegistrationServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        self.registration_state = RegistrationState()
        super().__init__(address, RegistrationHandler)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Master 등록 확인용 서버")
    parser.add_argument("--host", default=DEFAULT_MASTER_BIND_HOST)
    parser.add_argument("--port", default=DEFAULT_MASTER_PORT, type=int)
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    with RegistrationServer((args.host, args.port)) as server:
        host, port = server.server_address
        print(f"Master 등록 서버 실행: {host}:{port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Master 등록 서버를 종료합니다.", flush=True)


if __name__ == "__main__":
    main()
