import io
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from kvstore.common.logger import NodeLogger, format_console_log_line, format_log_line


class LoggerTests(unittest.TestCase):
    def test_format_log_line_conforms_to_spec(self) -> None:
        line = format_log_line(12.5, "Master", "INIT", "INFO", "Server started.")
        self.assertEqual(line, "[12.50] Master | INIT | INFO | Server started.")

    def test_console_log_uses_readable_logical_time(self) -> None:
        line = format_console_log_line(5_556.41, "Master", "PROGRESS", "INFO", "500/5000")
        self.assertEqual(
            line,
            "[논리시간 01시간 32분 36.41초] Master | PROGRESS | INFO | 500/5000",
        )

    def test_all_valid_statuses_are_accepted(self) -> None:
        for status in ("INFO", "SUCCESS", "FAIL", "WARN", "info", "success", "fail", "warn"):
            line = format_log_line(0.0, "Worker1", "TEST", status, "test message")
            self.assertIn(status.upper(), line)

    def test_invalid_status_raises_value_error(self) -> None:
        for invalid in ("DEBUG", "ERROR", "CRITICAL", "UNKNOWN", ""):
            with self.assertRaises(ValueError):
                format_log_line(1.0, "Worker1", "TEST", invalid, "should fail")

    def test_node_logger_writes_to_file_and_flushes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "Worker1.txt"
            with NodeLogger("Worker1", log_file_path=log_path, print_to_console=False) as logger:
                logger.log(0.0, "CONNECT", "SUCCESS", "Connected to master.")
                logger.log(2.5, "PROC", "SUCCESS", "Task done.")

            self.assertTrue(log_path.exists())
            content = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(content), 2)
            self.assertEqual(content[0], "[0.00] Worker1 | CONNECT | SUCCESS | Connected to master.")
            self.assertEqual(content[1], "[2.50] Worker1 | PROC | SUCCESS | Task done.")

    def test_concurrent_multithread_writes_do_not_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "Master.txt"
            logger = NodeLogger("Master", log_file_path=log_path, print_to_console=False)

            def write_many_logs(thread_id: int) -> None:
                for i in range(100):
                    logger.log(float(i), "DISTRIB", "INFO", f"Thread {thread_id} item {i}")

            threads = [threading.Thread(target=write_many_logs, args=(t,)) for t in range(5)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            logger.close()

            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 500)
            for line in lines:
                self.assertTrue(line.startswith("[") and "Master | DISTRIB | INFO | Thread" in line)

    def test_logger_callable_interface(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "Worker2.txt"
            logger = NodeLogger("Worker2", log_file_path=log_path, print_to_console=False)
            # runtime.py의 _default_log 형태인 (clock, node, event, status, message) 호출 검증
            logger(3.0, "Worker2", "PROC", "FAIL", "20% rule failure.")
            logger.close()

            content = log_path.read_text(encoding="utf-8").strip()
            self.assertEqual(content, "[3.00] Worker2 | PROC | FAIL | 20% rule failure.")

    def test_console_prints_key_events_but_file_keeps_every_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "Master.txt"
            output = io.StringIO()
            with redirect_stdout(output):
                with NodeLogger("Master", log_file_path=log_path) as logger:
                    logger.log(1.0, "DISTRIB", "INFO", "일반 작업 분배")
                    for index in range(4):
                        logger.log(2.0 + index, "QUEUE", "WARN", f"Queue 경고 {index}")
                    for index in range(6):
                        logger.log(6.0 + index, "RESULT", "FAIL", f"작업 실패 {index}")
                    for index in range(6):
                        logger.log(12.0 + index, "LB", "SUCCESS", f"P2P ACK 수신 {index}")
                    logger.log(18.0, "CONNECT", "FAIL", "연결 오류")
                    logger.log(19.0, "PROGRESS", "INFO", "500/5000 완료")

            console = output.getvalue()
            self.assertNotIn("일반 작업 분배", console)
            self.assertIn("Queue 경고 0", console)
            self.assertNotIn("Queue 경고 3", console)
            self.assertIn("작업 실패 4", console)
            self.assertNotIn("작업 실패 5", console)
            self.assertIn("P2P ACK 수신 4", console)
            self.assertNotIn("P2P ACK 수신 5", console)
            self.assertIn("연결 오류", console)
            self.assertIn("500/5000 완료", console)
            self.assertIn("[논리시간 00시간 00분 19.00초]", console)
            self.assertEqual(len(log_path.read_text(encoding="utf-8").splitlines()), 19)


if __name__ == "__main__":
    unittest.main()
