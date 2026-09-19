import threading
import unittest

from kvstore.common.clock import LogicalClock
from kvstore.worker.launcher import make_worker_configs, run_workers


class WorkerLauncherTests(unittest.TestCase):
    def test_starts_four_workers_with_one_clock(self):
        barrier = threading.Barrier(4)
        clock_ids: list[int] = []
        thread_ids: list[int] = []
        lock = threading.Lock()

        class FakeRuntime:
            def __init__(self, config, *, clock: LogicalClock):
                self.config = config
                self.clock = clock

            def run(self):
                with lock:
                    clock_ids.append(id(self.clock))
                    thread_ids.append(threading.get_ident())
                barrier.wait(timeout=2)

            def stop(self):
                return

        configs = make_worker_configs("127.0.0.1", 5000)
        shared_clock = run_workers(configs, runtime_factory=FakeRuntime)

        self.assertEqual(
            [config.worker_id for config in configs],
            ["worker1", "worker2", "worker3", "worker4"],
        )
        self.assertEqual([config.p2p_port for config in configs], [6001, 6002, 6003, 6004])
        self.assertEqual(len(set(thread_ids)), 4)
        self.assertEqual(set(clock_ids), {id(shared_clock)})

    def test_stops_other_workers_when_one_fails(self):
        stopped = threading.Event()

        class FakeRuntime:
            def __init__(self, config, *, clock: LogicalClock):
                self.config = config

            def run(self):
                if self.config.worker_id == "worker1":
                    raise OSError("connection failed")
                stopped.wait(timeout=2)

            def stop(self):
                stopped.set()

        configs = make_worker_configs("127.0.0.1", 5000)

        with self.assertRaises(RuntimeError):
            run_workers(configs, runtime_factory=FakeRuntime)
        self.assertTrue(stopped.is_set())


if __name__ == "__main__":
    unittest.main()
