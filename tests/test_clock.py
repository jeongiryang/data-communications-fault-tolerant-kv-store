import threading
import unittest

from kvstore.common.clock import LogicalClock


class LogicalClockTests(unittest.TestCase):
    def test_concurrent_advances_are_not_lost(self) -> None:
        clock = LogicalClock()

        def advance_many_times() -> None:
            for _ in range(1_000):
                clock.advance(1)

        threads = [threading.Thread(target=advance_many_times) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(clock.read(), 8_000.0)

    def test_negative_advance_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            LogicalClock().advance(-1)

    def test_observe_never_moves_clock_backwards(self) -> None:
        clock = LogicalClock(5)
        self.assertEqual(clock.observe(3), 5.0)
        self.assertEqual(clock.observe(9), 9.0)


if __name__ == "__main__":
    unittest.main()
