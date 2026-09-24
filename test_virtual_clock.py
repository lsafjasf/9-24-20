import asyncio
import unittest

from clock import RealClock, VirtualClock
from retry_logic import RetryOptions, make_operation, run_with_timeout_and_retries


def event_keys(events):
    """Remove clock timestamps for cross-clock ordering comparison."""
    return [event[:-1] for event in events]


class VirtualClockTests(unittest.TestCase):
    def test_step_advances_only_to_next_event(self):
        clock = VirtualClock(start_time=10)
        events = []
        clock.call_later(5, events.append, "late")
        clock.call_later(2, events.append, "early")

        self.assertEqual(clock.advance_next(), 12)
        self.assertEqual(clock.now(), 12)
        self.assertEqual(events, ["early"])

        self.assertEqual(clock.advance_next(), 15)
        self.assertEqual(clock.now(), 15)
        self.assertEqual(events, ["early", "late"])
        self.assertIsNone(clock.advance_next())

    def test_same_time_callbacks_fire_in_registration_order(self):
        clock = VirtualClock()
        events = []

        def second():
            events.append("second")
            clock.call_later(0, events.append, "inserted")

        clock.call_later(0, events.append, "first")
        clock.call_later(0, second)
        clock.call_later(0, events.append, "third")

        clock.advance_to(0)

        self.assertEqual(events, ["first", "second", "third", "inserted"])

    def test_callback_during_advance_reorders_by_new_due_time(self):
        clock = VirtualClock()
        events = []

        def at_two():
            events.append(("at_two", clock.now()))
            clock.call_at(3, events.append, ("inserted_three", clock.now()))
            clock.call_at(5, events.append, ("inserted_five", clock.now()))

        clock.call_at(2, at_two)
        clock.call_at(
            4,
            lambda: events.append(("existing_four", clock.now())),
        )

        clock.advance_to(5)

        self.assertEqual(
            events,
            [
                ("at_two", 2.0),
                ("inserted_three", 2.0),
                ("existing_four", 4.0),
                ("inserted_five", 2.0),
            ],
        )

    def test_cancelled_timer_never_fires(self):
        clock = VirtualClock()
        events = []
        first = clock.call_at(1, events.append, "first")
        cancelled = clock.call_at(1, events.append, "cancelled")
        clock.call_at(1, events.append, "third")

        self.assertTrue(cancelled.cancel())

        clock.advance_to(10)

        self.assertEqual(events, ["first", "third"])
        self.assertTrue(cancelled.cancelled())
        self.assertFalse(cancelled.active())
        self.assertEqual(clock.pending_count(), 0)

    def test_periodic_timer_has_stable_periodic_instances(self):
        clock = VirtualClock(start_time=0)
        events = []

        timer = clock.repeat(2, events.append, "tick")
        clock.advance_to(1)
        self.assertEqual(events, [])

        clock.advance_next()
        clock.advance_next()
        clock.advance_next()
        timer.cancel()
        clock.advance_to(20)

        self.assertEqual(events, ["tick", "tick", "tick"])
        self.assertEqual(clock.now(), 20)

    def test_two_periodic_timers_keep_fixed_phases_and_order(self):
        clock = VirtualClock()
        events = []
        every_two = clock.repeat(2, lambda: events.append((clock.now(), 2)))
        every_three = clock.repeat(3, lambda: events.append((clock.now(), 3)))

        clock.advance_to(6)
        every_two.cancel()
        every_three.cancel()

        self.assertEqual(
            events,
            [
                (2.0, 2),
                (3.0, 3),
                (4.0, 2),
                (6.0, 2),
                (6.0, 3),
            ],
        )

    def test_periodic_callback_can_cancel_itself(self):
        clock = VirtualClock()
        events = []
        state = {"timer": None}

        def callback():
            events.append(clock.now())
            assert state["timer"] is not None
            state["timer"].cancel()

        state["timer"] = clock.repeat_at(2, 2, callback)
        clock.advance_to(10)

        self.assertEqual(events, [2.0])

    def test_async_sleep_uses_injected_clock(self):
        clock = VirtualClock()
        events = []

        async def main():
            events.append(("before", clock.now()))
            await clock.sleep(5)
            events.append(("after", clock.now()))

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            task = loop.create_task(main())
            loop.run_until_complete(asyncio.sleep(0))
            self.assertEqual(events, [("before", 0.0)])

            clock.advance_to(5)
            loop.run_until_complete(task)
        finally:
            loop.close()
            asyncio.set_event_loop(None)

        self.assertEqual(events, [("before", 0.0), ("after", 5.0)])

    def test_cancelling_async_sleep_cancels_virtual_timer(self):
        clock = VirtualClock()

        async def main():
            await clock.sleep(5)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            task = loop.create_task(main())
            loop.run_until_complete(asyncio.sleep(0))
            self.assertEqual(clock.pending_count(), 1)

            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                loop.run_until_complete(task)

            self.assertEqual(clock.pending_count(), 0)
            clock.advance_to(10)
        finally:
            loop.close()
            asyncio.set_event_loop(None)


class RetryLogicTimingTests(unittest.TestCase):
    def test_timeout_then_retry_timeline(self):
        clock = VirtualClock()
        events = []
        operation = make_operation(
            clock,
            {
                1: (3, False, "unreachable"),
                2: (3, False, "unreachable"),
                3: (3, False, "unreachable"),
            },
        )
        options = RetryOptions(attempts=3, timeout=1, retry_delay=0.5)

        run_with_timeout_and_retries(clock, operation, options, events)
        clock.advance_to(4)

        self.assertEqual(
            events,
            [
                ("start", 1, 0),
                ("timeout", 1, 1),
                ("schedule_retry", 1, 1),
                ("start", 2, 1.5),
                ("timeout", 2, 2.5),
                ("schedule_retry", 2, 2.5),
                ("start", 3, 3),
                ("timeout", 3, 4),
                ("finish", "timeout", 4),
            ],
        )
        self.assertEqual(clock.pending_count(), 0)

    def test_success_response_cancels_timeout_and_retries(self):
        clock = VirtualClock()
        events = []
        operation = make_operation(clock, {1: (0.5, True, "ok")})
        options = RetryOptions(attempts=3, timeout=1, retry_delay=0.5)

        run_with_timeout_and_retries(clock, operation, options, events)
        clock.advance_to(10)

        self.assertEqual(
            events,
            [
                ("start", 1, 0),
                ("response", 1, "success", "ok", 0.5),
                ("finish", "success", 0.5),
            ],
        )
        self.assertEqual(clock.pending_count(), 0)

    def test_failure_retries_until_later_success(self):
        clock = VirtualClock()
        events = []
        operation = make_operation(
            clock,
            {
                1: (0.25, False, "bad"),
                2: (0.25, True, "ok"),
            },
        )
        options = RetryOptions(attempts=3, timeout=1, retry_delay=0.5)

        run_with_timeout_and_retries(clock, operation, options, events)
        clock.advance_to(2)

        self.assertEqual(
            events,
            [
                ("start", 1, 0),
                ("response", 1, "failure", "attempt 1 failed: bad", 0.25),
                ("schedule_retry", 1, 0.25),
                ("start", 2, 0.75),
                ("response", 2, "success", "ok", 1.0),
                ("finish", "success", 1.0),
            ],
        )

    def test_timeout_wins_same_instant_by_registration_order(self):
        clock = VirtualClock()
        events = []
        operation = make_operation(clock, {1: (1, True, "same-time")})
        options = RetryOptions(attempts=1, timeout=1, retry_delay=0)

        run_with_timeout_and_retries(clock, operation, options, events)
        clock.advance_to(1)

        self.assertEqual(
            events,
            [
                ("start", 1, 0),
                ("timeout", 1, 1),
                ("finish", "timeout", 1),
            ],
        )


class RealVirtualComparisonTests(unittest.TestCase):
    def _virtual_zero_wait_run(self):
        clock = VirtualClock()
        events = []
        operation = make_operation(
            clock,
            {
                1: (0, False, "unreachable"),
                2: (0, False, "unreachable"),
                3: (0, False, "unreachable"),
            },
        )
        options = RetryOptions(attempts=3, timeout=0, retry_delay=0)

        run_with_timeout_and_retries(clock, operation, options, events)
        clock.advance_to(0)
        return events

    def _real_zero_wait_run(self):
        events = []
        finished = asyncio.Event()
        result = {}

        async def main():
            clock = RealClock()
            operation = make_operation(
                clock,
                {
                    1: (0, False, "unreachable"),
                    2: (0, False, "unreachable"),
                    3: (0, False, "unreachable"),
                },
            )
            options = RetryOptions(attempts=3, timeout=0, retry_delay=0)

            def on_finished(value):
                result["value"] = value
                finished.set()

            run_with_timeout_and_retries(
                clock, operation, options, events, on_finished
            )
            await finished.wait()

        asyncio.run(main())
        return events, result["value"]

    def test_zero_wait_timeout_retry_order_matches_real_clock(self):
        virtual_events = self._virtual_zero_wait_run()
        real_events, real_result = self._real_zero_wait_run()

        expected_keys = [
            ("start", 1),
            ("timeout", 1),
            ("schedule_retry", 1),
            ("start", 2),
            ("timeout", 2),
            ("schedule_retry", 2),
            ("start", 3),
            ("timeout", 3),
            ("finish", "timeout"),
        ]

        self.assertEqual(event_keys(virtual_events), expected_keys)
        self.assertEqual(event_keys(real_events), expected_keys)
        self.assertEqual(
            event_keys(virtual_events), event_keys(real_events)
        )
        self.assertEqual(virtual_events[-1][1], "timeout")
        self.assertEqual(real_result, "timeout")


if __name__ == "__main__":
    unittest.main()
