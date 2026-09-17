from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

from .config import DEFAULT_QUEUE_CAPACITY


PROTOCOL_VERSION = 1


class MessageType(str, Enum):
    REGISTER = "REGISTER"
    TASK = "TASK"
    QUEUE_STATUS = "QUEUE_STATUS"
    RESULT_SUCCESS = "RESULT_SUCCESS"
    RESULT_FAIL = "RESULT_FAIL"
    P2P_STATUS_REQUEST = "P2P_STATUS_REQUEST"
    P2P_STATUS_RESPONSE = "P2P_STATUS_RESPONSE"
    P2P_TRANSFER = "P2P_TRANSFER"
    ACK = "ACK"
    TERMINATE = "TERMINATE"
    ERROR = "ERROR"


@dataclass
class Task:
    task_id: str
    key: str
    value: int
    attempt: int = 0
    previous_worker_id: str | None = None
    enqueued_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.task_id:
            raise ValueError("task_id is required")
        normalized_key = self.key.lower()
        if len(normalized_key) != 4 or any(
            character not in "0123456789abcdef" for character in normalized_key
        ):
            raise ValueError("key must be exactly four hexadecimal characters")
        if not 1 <= self.value <= 100:
            raise ValueError("value must be between 1 and 100")
        if self.attempt < 0:
            raise ValueError("attempt must be non-negative")
        if self.enqueued_at < 0:
            raise ValueError("enqueued_at must be non-negative")
        self.key = normalized_key

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        previous_worker_id = data.get("previous_worker_id")
        if previous_worker_id is not None:
            previous_worker_id = str(previous_worker_id)

        return cls(
            task_id=str(data["task_id"]),
            key=str(data["key"]),
            value=int(data["value"]),
            attempt=int(data.get("attempt", 0)),
            previous_worker_id=previous_worker_id,
            enqueued_at=float(data.get("enqueued_at", 0.0)),
        )


@dataclass
class WorkerEndpoint:
    host: str
    port: int

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("endpoint host is required")
        if not 1 <= self.port <= 65535:
            raise ValueError("endpoint port must be between 1 and 65535")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerEndpoint":
        return cls(host=str(data["host"]), port=int(data["port"]))


@dataclass
class RegisterPayload:
    worker_id: str
    p2p_endpoint: WorkerEndpoint
    queue_capacity: int = DEFAULT_QUEUE_CAPACITY

    def __post_init__(self) -> None:
        if not self.worker_id:
            raise ValueError("worker_id is required")
        if self.queue_capacity != DEFAULT_QUEUE_CAPACITY:
            raise ValueError("the assignment requires queue_capacity=10")

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "p2p_endpoint": self.p2p_endpoint.to_dict(),
            "queue_capacity": self.queue_capacity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RegisterPayload":
        endpoint = data.get("p2p_endpoint")
        if not isinstance(endpoint, dict):
            raise ValueError("p2p_endpoint must be an object")
        return cls(
            worker_id=str(data["worker_id"]),
            p2p_endpoint=WorkerEndpoint.from_dict(endpoint),
            queue_capacity=int(data.get("queue_capacity", DEFAULT_QUEUE_CAPACITY)),
        )


@dataclass
class WorkerStatus:
    worker_id: str
    queue_size: int
    queue_capacity: int = DEFAULT_QUEUE_CAPACITY
    processing_task_id: str | None = None

    def __post_init__(self) -> None:
        if not self.worker_id:
            raise ValueError("worker_id is required")
        if self.queue_capacity != DEFAULT_QUEUE_CAPACITY:
            raise ValueError("the assignment requires queue_capacity=10")
        if not 0 <= self.queue_size <= self.queue_capacity:
            raise ValueError("queue_size must fit within queue_capacity")

    @property
    def available_capacity(self) -> int:
        return self.queue_capacity - self.queue_size

    @property
    def estimated_wait_seconds(self) -> int:
        return self.queue_size * 2

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkerStatus":
        processing_task_id = data.get("processing_task_id")
        if processing_task_id is not None:
            processing_task_id = str(processing_task_id)

        return cls(
            worker_id=str(data["worker_id"]),
            queue_size=int(data["queue_size"]),
            queue_capacity=int(data.get("queue_capacity", DEFAULT_QUEUE_CAPACITY)),
            processing_task_id=processing_task_id,
        )


@dataclass
class Message:
    message_type: MessageType
    sender_id: str
    request_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    logical_clock: float = 0.0
    version: int = PROTOCOL_VERSION

    def __post_init__(self) -> None:
        if self.version != PROTOCOL_VERSION:
            raise ValueError(f"unsupported protocol version: {self.version}")
        if not self.sender_id:
            raise ValueError("sender_id is required")
        if not self.request_id:
            raise ValueError("request_id is required")
        if not isinstance(self.payload, dict):
            raise ValueError("payload must be a dictionary")
        if self.logical_clock < 0:
            raise ValueError("logical_clock must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": self.message_type.value,
            "sender_id": self.sender_id,
            "request_id": self.request_id,
            "logical_clock": self.logical_clock,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        payload = data.get("payload", {})
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")
        return cls(
            version=int(data.get("version", PROTOCOL_VERSION)),
            message_type=MessageType(str(data["type"])),
            sender_id=str(data["sender_id"]),
            request_id=str(data["request_id"]),
            logical_clock=float(data.get("logical_clock", 0.0)),
            payload=dict(payload),
        )
