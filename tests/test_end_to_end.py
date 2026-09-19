import random
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from kvstore.common.config import WorkerConfig
from kvstore.common.logger import NodeLogger
from kvstore.master.runtime import MasterRuntime
from kvstore.worker.launcher import run_workers


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()
    return port


class EndToEndTests(unittest.TestCase):
    def test_real_master_and_four_workers_finish(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            master_logger = NodeLogger(
                "Master", Path(temp_dir) / "Master.txt", print_to_console=False
            )
            master = MasterRuntime(
                host="127.0.0.1",
                port=0,
                task_count=100,
                rng=random.Random(7),
                log=master_logger,
            )
            master_result: dict[str, int] = {}
            master_error: list[Exception] = []

            def run_master() -> None:
                try:
                    master_result.update(master.run())
                except Exception as exc:
                    master_error.append(exc)

            master_thread = threading.Thread(target=run_master, daemon=True)
            master_thread.start()
            self.assertTrue(master.wait_until_listening())
            master_port = master.address[1]
            configs = [
                WorkerConfig(
                    worker_id=f"worker{number}",
                    master_host="127.0.0.1",
                    master_port=master_port,
                    p2p_host="127.0.0.1",
                    p2p_port=_free_port(),
                )
                for number in range(1, 5)
            ]

            try:
                run_workers(configs, log_dir=temp_dir, print_to_console=False)
                master_thread.join(timeout=15)
            finally:
                master.stop()
                master_logger.close()

            self.assertFalse(master_thread.is_alive())
            self.assertEqual(master_error, [])
            self.assertEqual(len(master_result), 100)
            for filename in ("Master.txt", "Worker1.txt", "Worker2.txt", "Worker3.txt", "Worker4.txt"):
                path = Path(temp_dir) / filename
                self.assertTrue(path.exists())
                self.assertGreater(path.stat().st_size, 0)


if __name__ == "__main__":
    unittest.main()
