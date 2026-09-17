import argparse
import json
import socket
import uuid

from kvstore.common.config import DEFAULT_MASTER_PORT, DEFAULT_SOCKET_TIMEOUT_SECONDS
from kvstore.common.models import Message, MessageType, RegisterPayload, WorkerEndpoint
from kvstore.common.protocol import receive_message, send_message


def register_worker(
    *,
    worker_id: str,
    master_host: str,
    master_port: int,
    p2p_host: str,
    p2p_port: int,
) -> Message:
    registration = RegisterPayload(
        worker_id=worker_id,
        p2p_endpoint=WorkerEndpoint(host=p2p_host, port=p2p_port),
    )
    request = Message(
        message_type=MessageType.REGISTER,
        sender_id=worker_id,
        request_id=str(uuid.uuid4()),
        payload=registration.to_dict(),
    )
    with socket.create_connection(
        (master_host, master_port), timeout=DEFAULT_SOCKET_TIMEOUT_SECONDS
    ) as connection:
        connection.settimeout(DEFAULT_SOCKET_TIMEOUT_SECONDS)
        send_message(connection, request)
        response = receive_message(connection)
    if response.request_id != request.request_id:
        raise RuntimeError("Master response request_id does not match the request")
    if response.message_type is not MessageType.ACK:
        raise RuntimeError(f"registration failed: {response.payload}")
    return response


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Worker 등록 확인용 클라이언트")
    parser.add_argument("--worker-id", required=True)
    parser.add_argument("--master-host", required=True)
    parser.add_argument("--master-port", default=DEFAULT_MASTER_PORT, type=int)
    parser.add_argument("--p2p-host", required=True)
    parser.add_argument("--p2p-port", required=True, type=int)
    return parser


def main() -> None:
    args = build_argument_parser().parse_args()
    response = register_worker(
        worker_id=args.worker_id,
        master_host=args.master_host,
        master_port=args.master_port,
        p2p_host=args.p2p_host,
        p2p_port=args.p2p_port,
    )
    print(json.dumps(response.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
