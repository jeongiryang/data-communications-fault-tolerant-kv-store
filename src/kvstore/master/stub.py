"""Small registration server that unblocks Worker development.

This is intentionally not the final scheduler. It only proves that the shared
framing and REGISTER/ACK contract work over a real TCP socket.
"""

from __future__ import annotations

import argparse
import socketserver
import threading
from dataclasses import dataclass
from typing import cast

from kvstore.common.config import DEFAULT_MASTER_BIND_HOST, DEFAULT_MASTER_PORT
from kvstore.common.models import Message, MessageType, RegisterPayload
from kvstore.common.protocol import ProtocolError, receive_message, send_message


@dataclass(frozen=True, slots=True)
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
        server = cast(RegistrationServer, self.server)
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
                pass


class RegistrationServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int]) -> None:
        self.registration_state = RegistrationState()
        super().__init__(address, RegistrationHandler)


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Master registration stub")
    parser.add_argument("--host", default=DEFAULT_MASTER_BIND_HOST)
    parser.add_argument("--port", default=DEFAULT_MASTER_PORT, type=int)
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    with RegistrationServer((args.host, args.port)) as server:
        host, port = server.server_address
        print(f"Master registration stub listening on {host}:{port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Stopping Master registration stub", flush=True)


if __name__ == "__main__":
    main()
