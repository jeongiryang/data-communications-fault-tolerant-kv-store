"""Length-prefixed JSON framing for every TCP connection."""

from __future__ import annotations

import json
import socket
import struct
from typing import Final

from .models import Message


HEADER: Final[struct.Struct] = struct.Struct("!I")
MAX_FRAME_BYTES: Final[int] = 1_048_576


class ProtocolError(Exception):
    """Raised for malformed, truncated, or oversized protocol frames."""


def encode_message(message: Message) -> bytes:
    try:
        payload = json.dumps(
            message.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("message payload is not JSON serializable") from exc

    if not payload or len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError("message payload size is outside the allowed range")
    return HEADER.pack(len(payload)) + payload


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise EOFError("connection closed while receiving a protocol frame")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def receive_message(sock: socket.socket) -> Message:
    header = _recv_exact(sock, HEADER.size)
    (payload_size,) = HEADER.unpack(header)
    if payload_size == 0 or payload_size > MAX_FRAME_BYTES:
        raise ProtocolError(f"invalid frame size: {payload_size}")

    raw_payload = _recv_exact(sock, payload_size)
    try:
        decoded = json.loads(raw_payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("top-level JSON value must be an object")
        return Message.from_dict(decoded)
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ProtocolError("invalid message payload") from exc


def send_message(sock: socket.socket, message: Message) -> None:
    sock.sendall(encode_message(message))
