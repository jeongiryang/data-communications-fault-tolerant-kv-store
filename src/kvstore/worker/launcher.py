import argparse
import threading
from pathlib import Path

from kvstore.common.clock import LogicalClock
from kvstore.common.config import DEFAULT_MASTER_PORT, WorkerConfig, validate_port
from kvstore.common.logger import NodeLogger
from kvstore.common.models import WorkerEndpoint
from kvstore.common.stats import StatsCollector
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


def run_workers(
    configs: list[WorkerConfig],
    runtime_factory=WorkerRuntime,
    *,
    log_dir: str | Path = ".",
    print_to_console: bool = True,
) -> LogicalClock:
    if len(configs) != WORKER_COUNT:
        raise ValueError("exactly four worker configs are required")

    clock = LogicalClock()
    loggers: list[NodeLogger] = []
    if runtime_factory is WorkerRuntime:
        peers = {
            config.worker_id: WorkerEndpoint(config.p2p_host, config.p2p_port)
            for config in configs
        }
        stats = StatsCollector()
        for config in configs:
            number = config.worker_id.removeprefix("worker")
            node_name = f"Worker{number}"
            logger = NodeLogger(
                node_name,
                Path(log_dir) / f"{node_name}.txt",
                print_to_console=print_to_console,
            )
            loggers.append(logger)
        runtimes = [
            runtime_factory(
                config,
                clock=clock,
                log=logger,
                peers=peers,
                stats=stats,
            )
            for config, logger in zip(configs, loggers)
        ]
    else:
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
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        for logger in loggers:
            logger.close()

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
    parser.add_argument("--log-dir", default=".")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    configs = make_worker_configs(
        master_host=args.master_host,
        master_port=args.master_port,
        p2p_host=args.p2p_host,
        p2p_base_port=args.p2p_base_port,
    )
    run_workers(configs, log_dir=args.log_dir)


if __name__ == "__main__":
    main()
