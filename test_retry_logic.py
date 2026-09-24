"""Timing tests for fetch_with_retry running on a VirtualClock.

No real waiting anywhere: all time is virtual.
"""

import unittest

from retry_logic import FlakyService, fetch_with_retry
from virtual_clock import VirtualClock

TIMEOUT = 2.0
BACKOFF = 1.0


def run(script, retries=2, timeout=TIMEOUT, backoff=BACKOFF):
    clock = VirtualClock()
    service = FlakyService(clock, script)
    log = []
    result = fetch_with_retry(
        clock, service, timeout=timeout, retries=retries, backoff=backoff, log=log
    )
    return clock, log, result


class SuccessPathTests(unittest.TestCase):
    def test_success_on_first_attempt(self):
        clock, log, result = run([(0.5, True, "payload")])
        self.assertEqual(result, ("success", "payload"))
        self.assertEqual(log, [("attempt", 0), ("success", "payload")])
        self.assertEqual(clock.now(), 0.5)  # finished exactly at result time
        self.assertEqual(clock.pending, 0)  # timeout timer was cancelled

    def test_failure_then_success_with_backoff(self):
        clock, log, result = run([(0.5, False, "boom"), (0.5, True, "ok")])
        self.assertEqual(result, ("success", "ok"))
        self.assertEqual(
            log,
            [
                ("attempt", 0),
                ("failure", "boom"),
                ("backoff", 0),
                ("attempt", 1),
                ("success", "ok"),
            ],
        )
        # t=0.5 failure, +1.0 backoff, +0.5 to result
        self.assertEqual(clock.now(), 2.0)


class TimeoutPathTests(unittest.TestCase):
    def test_timeout_then_retry_then_success(self):
        # attempt 0 answers at t=5 (> timeout=2) -> times out; attempt 1 fast ok
        clock, log, result = run([(5.0, True, "late"), (0.5, True, "ok")], retries=1)
        self.assertEqual(result, ("success", "ok"))
        self.assertEqual(
            log,
            [
                ("attempt", 0),
                ("timeout", 0),
                ("backoff", 0),
                ("attempt", 1),
                ("success", "ok"),
            ],
        )
        # timeout at t=2, backoff to t=3, result at t=3.5
        self.assertEqual(clock.now(), 3.5)

    def test_all_attempts_time_out_gives_exhausted(self):
        clock, log, result = run([(9.0, True, "x")] * 3, retries=2)
        self.assertEqual(result, ("exhausted", None))
        self.assertEqual(
            log,
            [
                ("attempt", 0), ("timeout", 0), ("backoff", 0),
                ("attempt", 1), ("timeout", 1), ("backoff", 1),
                ("attempt", 2), ("timeout", 2),
                ("exhausted", None),
            ],
        )
        # attempts start at t=0, 3, 6; last timeout at t=8
        self.assertEqual(clock.now(), 8.0)

    def test_timeout_wins_over_same_deadline_result(self):
        # Result scheduled after the timeout timer at the same deadline:
        # timeout was registered first, so it fires first -> attempt times out.
        clock, log, result = run([(TIMEOUT, True, "too-late")], retries=0)
        self.assertEqual(result, ("exhausted", None))
        self.assertEqual(
            log,
            [("attempt", 0), ("timeout", 0), ("exhausted", None)],
        )


if __name__ == "__main__":
    unittest.main()
