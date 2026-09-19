import threading
import unittest

from kvstore.common.stats import StatsCollector, WorkerStats


class StatsTests(unittest.TestCase):
    def test_worker_stats_initial_and_wait_time(self) -> None:
        stats = WorkerStats(worker_id="worker-1")
        self.assertEqual(stats.average_wait_seconds, 0.0)

        stats.total_wait_seconds = 10.5
        stats.processed_count = 3
        self.assertAlmostEqual(stats.average_wait_seconds, 3.5)

    def test_record_task_success_and_fail(self) -> None:
        collector = StatsCollector()
        collector.record_task_success("worker-1", wait_seconds=1.5)
        collector.record_task_success("worker-1", wait_seconds=2.5)
        collector.record_task_fail("worker-1", wait_seconds=2.0)

        w1 = collector.get_worker_stats("worker-1")
        self.assertEqual(w1.throughput, 2)
        self.assertEqual(w1.success_count, 2)
        self.assertEqual(w1.fail_count, 1)
        self.assertEqual(w1.processed_count, 3)
        self.assertAlmostEqual(w1.average_wait_seconds, 2.0)

    def test_record_p2p_transfer(self) -> None:
        collector = StatsCollector()
        collector.record_p2p_transfer(sender_id="worker-1", receiver_id="worker-2", task_count=3)
        collector.record_p2p_transfer(sender_id="worker-1", receiver_id="worker-3", task_count=2)

        w1 = collector.get_worker_stats("worker-1")
        w2 = collector.get_worker_stats("worker-2")
        w3 = collector.get_worker_stats("worker-3")

        self.assertEqual(w1.p2p_transfers_sent, 2)
        self.assertEqual(w1.p2p_tasks_sent, 5)
        self.assertEqual(w2.p2p_transfers_received, 1)
        self.assertEqual(w2.p2p_tasks_received, 3)
        self.assertEqual(w3.p2p_transfers_received, 1)
        self.assertEqual(w3.p2p_tasks_received, 2)

    def test_record_reallocation(self) -> None:
        collector = StatsCollector()
        collector.record_reallocation()
        collector.record_reallocation()

        summary = collector.summary(total_simulation_seconds=100.0)
        self.assertEqual(summary["total_reallocations"], 2)

    def test_duplicate_event_id_is_not_counted_twice(self) -> None:
        collector = StatsCollector()
        collector.record_task_success("worker-1", 1.0, event_id="result-1")
        collector.record_task_success("worker-1", 1.0, event_id="result-1")
        collector.record_reallocation(event_id="retry-1")
        collector.record_reallocation(event_id="retry-1")

        summary = collector.summary(total_simulation_seconds=1.0)
        self.assertEqual(summary["total_success_count"], 1)
        self.assertEqual(summary["total_reallocations"], 1)

    def test_concurrent_multithread_updates_are_thread_safe(self) -> None:
        collector = StatsCollector()

        def worker_loop(wid: str) -> None:
            for _ in range(200):
                collector.record_task_success(wid, wait_seconds=1.0)
                collector.record_task_fail(wid, wait_seconds=1.0)
                collector.record_reallocation()

        threads = [
            threading.Thread(target=worker_loop, args=(f"worker-{i}",))
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        summary = collector.summary(total_simulation_seconds=500.0)
        self.assertEqual(summary["total_throughput"], 800)  # 4 * 200
        self.assertEqual(summary["total_success_count"], 800)
        self.assertEqual(summary["total_fail_count"], 800)
        self.assertEqual(summary["total_reallocations"], 800)

    def test_report_formatting(self) -> None:
        collector = StatsCollector()
        collector.record_task_success("worker-1", wait_seconds=1.0)
        collector.record_p2p_transfer("worker-1", "worker-2", task_count=2)

        lines = collector.format_report_lines(total_simulation_seconds=42.5)
        report_text = "\n".join(lines)

        self.assertIn("전체 수행시간(초): 42.50", report_text)
        self.assertIn("Worker worker-1", report_text)
        self.assertIn("P2P 부하 분산: 1회 (2개 작업 이동)", report_text)


if __name__ == "__main__":
    unittest.main()
