"""Parity tests: the SAME logic must produce the SAME event order under
VirtualClock and RealClock.

Durations are expressed in abstract "units"; the real clock runs them with
a tiny unit (2 ms) so the whole parity suite finishes in well under a
second and no test depends on meaningful real waiting.
"""

import unittest

from retry_logic import FlakyService, fetch_with_retry
from virtual_clock import RealClock, VirtualClock

REAL_UNIT = 0.005  # seconds per abstract time unit on the real clock

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
}


def run_virtual(script, timeout, retries, backoff):
    clock = VirtualClock()
    log = []
    result = fetch_with_retry(
        clock, FlakyService(clock, script),
        timeout=timeout, retries=retries, backoff=backoff, log=log,
    )
    return log, result


def run_real(script, timeout, retries, backoff):
    with RealClock() as clock:
        scale = lambda script: [(d * REAL_UNIT, ok, v) for d, ok, v in script]
        log = []
        result = fetch_with_retry(
            clock, FlakyService(clock, scale(script)),
            timeout=timeout * REAL_UNIT,
            retries=retries,
            backoff=backoff * REAL_UNIT,
            log=log,
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


if __name__ == "__main__":
    unittest.main()
