from dataclasses import dataclass


DEFAULT_MASTER_BIND_HOST = "0.0.0.0"
DEFAULT_MASTER_PORT = 5000
DEFAULT_QUEUE_CAPACITY = 10
DEFAULT_SOCKET_TIMEOUT_SECONDS = 5.0


def validate_port(port: int) -> int:
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    return port


@dataclass
class MasterConfig:
    host: str = DEFAULT_MASTER_BIND_HOST
    port: int = DEFAULT_MASTER_PORT
    expected_workers: int = 4

    def __post_init__(self) -> None:
        if not self.host:
            raise ValueError("host is required")
        validate_port(self.port)
        if self.expected_workers != 4:
            raise ValueError("the assignment requires exactly four workers")


@dataclass
class WorkerConfig:
    worker_id: str
    master_host: str
    master_port: int
    p2p_host: str
    p2p_port: int

    def __post_init__(self) -> None:
        if not self.worker_id:
            raise ValueError("worker_id is required")
        if not self.master_host or not self.p2p_host:
            raise ValueError("master_host and p2p_host are required")
        validate_port(self.master_port)
        validate_port(self.p2p_port)
