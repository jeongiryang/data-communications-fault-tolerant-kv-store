import argparse
import threading

from kvstore.common.clock import LogicalClock
from kvstore.common.config import DEFAULT_MASTER_PORT, WorkerConfig, validate_port
from kvstore.worker.runtime import WorkerRuntime


WORKER_COUNT = 4
DEFAULT_P2P_BASE_PORT = 6001


def make_worker_configs(
    master_host: str,
    master_port: int,
    p2p_host: str = "127.0.0.1",
    p2p_base_port: int = DEFAULT_P2P_BASE_PORT,
) -> list[WorkerConfig]:
    validate_port(p2p_base_port + WORKER_COUNT - 1)
    return [
        WorkerConfig(
            worker_id=f"worker{number}",
            master_host=master_host,
            master_port=master_port,
            p2p_host=p2p_host,
            p2p_port=p2p_base_port + number - 1,
        )
        for number in range(1, WORKER_COUNT + 1)
    ]


def run_workers(configs: list[WorkerConfig], runtime_factory=WorkerRuntime) -> LogicalClock:
    if len(configs) != WORKER_COUNT:
        raise ValueError("exactly four worker configs are required")

    clock = LogicalClock()
    runtimes = [runtime_factory(config, clock=clock) for config in configs]
    errors: list[tuple[str, Exception]] = []
    error_lock = threading.Lock()

    def stop_all() -> None:
        for runtime in runtimes:
            stop = getattr(runtime, "stop", None)
            if stop is not None:
                stop()

    def run_worker(config: WorkerConfig, runtime) -> None:
        try:
            runtime.run()
        except Exception as exc:
            with error_lock:
                errors.append((config.worker_id, exc))
            stop_all()

    threads = [
        threading.Thread(target=run_worker, args=(config, runtime), name=config.worker_id)
        for config, runtime in zip(configs, runtimes)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    if errors:
        worker_id, error = errors[0]
        raise RuntimeError(f"{worker_id} stopped with an error: {error}") from error
    return clock


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Worker 4개 실행")
    parser.add_argument("--master-host", required=True)
    parser.add_argument("--master-port", type=int, default=DEFAULT_MASTER_PORT)
    parser.add_argument("--p2p-host", default="127.0.0.1")
    parser.add_argument("--p2p-base-port", type=int, default=DEFAULT_P2P_BASE_PORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    configs = make_worker_configs(
        master_host=args.master_host,
        master_port=args.master_port,
        p2p_host=args.p2p_host,
        p2p_base_port=args.p2p_base_port,
    )
    run_workers(configs)


if __name__ == "__main__":
    main()
