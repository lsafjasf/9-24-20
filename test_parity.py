"""Parity tests: the SAME logic must produce the SAME event order under
VirtualClock and RealClock.

The real side runs on a manually advanced time base (DrivenRealClock):
RealClock's scheduler thread, condition variables and (deadline, seq)
heap ordering are fully exercised, but time only moves when the test
advances it. No assertion depends on real-time waits or millisecond-scale
margins, so the suite is deterministic no matter how loaded the machine
is -- the previous version scaled scenarios down to 5 ms/unit, where a
result/timeout margin of half a unit (2.5 ms) could be swallowed by
scheduler latency and a success was randomly judged a timeout.
"""

import heapq
import unittest

from retry_logic import FlakyService, fetch_with_retry
from virtual_clock import RealClock, VirtualClock

SCENARIOS = {
    "success_first_try": dict(
        script=[(0.5, True, "ok")], timeout=2.0, retries=2, backoff=1.0,
    ),
    "timeout_then_success": dict(
        script=[(5.0, True, "late"), (0.5, True, "ok")],
        timeout=1.0, retries=1, backoff=1.0,
    ),
    "failure_then_success": dict(
        script=[(0.5, False, "boom"), (0.5, True, "ok")],
        timeout=2.0, retries=2, backoff=1.0,
    ),
    "all_attempts_time_out": dict(
        script=[(5.0, True, "x"), (5.0, True, "x"), (5.0, True, "x")],
        timeout=1.0, retries=2, backoff=1.0,
    ),
    "mixed_timeout_failure_success": dict(
        script=[(5.0, True, "late"), (0.5, False, "boom"), (0.5, True, "ok")],
        timeout=1.0, retries=2, backoff=1.0,
    ),
    "hairline_margin_success": dict(
        # 0.001 units between result and timeout: impossible to adjudicate
        # with real-time waits, trivial for a deterministic clock.
        script=[(0.999, True, "ok")], timeout=1.0, retries=0, backoff=1.0,
    ),
}


class DrivenRealClock(RealClock):
    """RealClock on a manual time base: deterministic, no real waiting.

    Time stands still until the test calls advance_to()/advance_to_next();
    each advance blocks until the scheduler thread has fired every timer
    due at the new time, so event order is decided purely by the clock's
    (deadline, registration-order) semantics -- never by wall-clock races.
    """

    def __init__(self) -> None:
        self._manual_now = 0.0
        self._settled_at = float("-inf")
        super().__init__(monotonic=self._manual_time)

    # -- manual time base -------------------------------------------------
    def _manual_time(self) -> float:
        return self._manual_now

    def _notify_settled(self) -> None:
        # Called by the scheduler thread with self._cond held.
        self._settled_at = self._manual_now
        self._cond.notify_all()

    # -- driving time (test thread only) ----------------------------------
    def advance_to(self, when: float) -> None:
        if when < self._manual_now:
            raise ValueError("cannot move the clock backwards")
        with self._cond:
            self._manual_now = when
            self._cond.notify_all()
            while self._settled_at < when:
                self._cond.wait()

    def advance_to_next(self) -> float:
        with self._cond:
            while self._heap and self._heap[0][2].cancelled:
                heapq.heappop(self._heap)
            if not self._heap:
                raise RuntimeError("advance_to_next() with no pending timers")
            deadline = self._heap[0][0]
        self.advance_to(deadline)
        return deadline

    def sleep(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("cannot sleep for a negative duration")
        self.advance_to(self._manual_now + seconds)

    def wait_until(self, predicate) -> None:
        while not predicate():
            self.advance_to_next()


def run_virtual(script, timeout, retries, backoff):
    clock = VirtualClock()
    log = []
    result = fetch_with_retry(
        clock, FlakyService(clock, script),
        timeout=timeout, retries=retries, backoff=backoff, log=log,
    )
    return log, result


def run_real(script, timeout, retries, backoff):
    # Same abstract units as the virtual run -- no time scaling at all.
    with DrivenRealClock() as clock:
        log = []
        result = fetch_with_retry(
            clock, FlakyService(clock, script),
            timeout=timeout, retries=retries, backoff=backoff, log=log,
        )
        assert not clock.errors, f"clock callback errors: {clock.errors}"
    return log, result


class ParityTests(unittest.TestCase):
    def test_event_order_matches_between_virtual_and_real_clock(self):
        for name, scenario in SCENARIOS.items():
            with self.subTest(scenario=name):
                v_log, v_result = run_virtual(**scenario)
                r_log, r_result = run_real(**scenario)
                self.assertEqual(v_result, r_result)
                self.assertEqual(v_log, r_log)


class DrivenRealClockTests(unittest.TestCase):
    def test_colliding_repeating_timers_keep_registration_order(self):
        with DrivenRealClock() as clock:
            events = []
            # Both are due at t=6; the timer registered first must fire
            # first even though it was rescheduled more recently (t=4 vs
            # t=3). Mirrors the VirtualClock semantics test.
            clock.call_repeated(2.0, lambda: events.append("first"))
            clock.call_repeated(3.0, lambda: events.append("second"))
            clock.advance_to(6.0)
        self.assertEqual(
            events, ["first", "second", "first", "first", "second"]
        )


if __name__ == "__main__":
    unittest.main()
