"""Semantics tests for VirtualClock: ordering, advance, cancel, repeat."""

import unittest

from virtual_clock import VirtualClock


class OrderingTests(unittest.TestCase):
    def test_same_deadline_fires_in_registration_order(self):
        clock = VirtualClock()
        events = []
        clock.call_later(1.0, lambda: events.append("first"))
        clock.call_later(1.0, lambda: events.append("second"))
        clock.call_later(1.0, lambda: events.append("third"))
        clock.advance_to(1.0)
        self.assertEqual(events, ["first", "second", "third"])

    def test_mixed_deadlines_fire_in_deadline_then_registration_order(self):
        clock = VirtualClock()
        events = []
        clock.call_later(3.0, lambda: events.append("c"))
        clock.call_later(1.0, lambda: events.append("a"))
        clock.call_later(1.0, lambda: events.append("b"))
        clock.call_at(2.0, lambda: events.append("at-2"))
        clock.advance_to(10.0)
        self.assertEqual(events, ["a", "b", "at-2", "c"])
        self.assertEqual(clock.now(), 10.0)

    def test_timer_scheduled_during_advance_joins_ordering(self):
        clock = VirtualClock()
        events = []

        def spawn():
            events.append("spawner@1")
            # deadline 2.5: must fire between the 2.0 and 3.0 timers
            clock.call_later(1.5, lambda: events.append("spawned@2.5"))

        clock.call_later(1.0, spawn)
        clock.call_later(2.0, lambda: events.append("plain@2"))
        clock.call_later(3.0, lambda: events.append("plain@3"))
        clock.advance_to(10.0)
        self.assertEqual(events, ["spawner@1", "plain@2", "spawned@2.5", "plain@3"])

    def test_timer_scheduled_beyond_horizon_is_not_fired_early(self):
        clock = VirtualClock()
        events = []
        clock.call_later(1.0, lambda: clock.call_later(5.0, lambda: events.append("late")))
        clock.advance_to(2.0)
        self.assertEqual(events, [])
        self.assertEqual(clock.pending, 1)
        clock.advance_to_next()
        self.assertEqual(events, ["late"])
        self.assertEqual(clock.now(), 6.0)


class AdvanceTests(unittest.TestCase):
    def test_advance_to_next_fires_only_the_next_instant(self):
        clock = VirtualClock()
        events = []
        clock.call_later(1.0, lambda: events.append("a"))
        clock.call_later(1.0, lambda: events.append("b"))
        clock.call_later(2.0, lambda: events.append("c"))
        self.assertEqual(clock.advance_to_next(), 1.0)
        self.assertEqual(events, ["a", "b"])  # both callbacks at t=1
        self.assertEqual(clock.advance_to_next(), 2.0)
        self.assertEqual(events, ["a", "b", "c"])

    def test_advance_to_next_without_timers_raises(self):
        clock = VirtualClock()
        with self.assertRaises(RuntimeError):
            clock.advance_to_next()

    def test_advance_backwards_raises(self):
        clock = VirtualClock(start=5.0)
        with self.assertRaises(ValueError):
            clock.advance_to(4.0)

    def test_sleep_advances_and_fires_due_timers(self):
        clock = VirtualClock()
        events = []
        clock.call_later(1.5, lambda: events.append(("fired", clock.now())))
        clock.sleep(2.0)
        self.assertEqual(events, [("fired", 1.5)])
        self.assertEqual(clock.now(), 2.0)


class CancelTests(unittest.TestCase):
    def test_cancelled_timer_never_fires(self):
        clock = VirtualClock()
        events = []
        handle = clock.call_later(1.0, lambda: events.append("nope"))
        handle.cancel()
        clock.advance_to(100.0)
        self.assertEqual(events, [])
        self.assertEqual(clock.pending, 0)

    def test_cancel_from_inside_another_callback(self):
        clock = VirtualClock()
        events = []
        victim = clock.call_later(2.0, lambda: events.append("victim"))
        clock.call_later(1.0, victim.cancel)
        clock.advance_to(10.0)
        self.assertEqual(events, [])

    def test_cancel_does_not_disturb_siblings_at_same_deadline(self):
        clock = VirtualClock()
        events = []
        clock.call_later(1.0, lambda: events.append("a")).cancel()
        clock.call_later(1.0, lambda: events.append("b"))
        clock.advance_to(1.0)
        self.assertEqual(events, ["b"])


class RepeatTests(unittest.TestCase):
    def test_repeating_timer_produces_stable_periodic_instances(self):
        clock = VirtualClock(start=0.5)
        fires = []
        clock.call_repeated(1.0, lambda: fires.append(clock.now()))
        clock.advance_to(4.0)
        self.assertEqual(fires, [1.5, 2.5, 3.5])  # no drift from the grid

    def test_repeating_timer_survives_and_interleaves_with_one_shots(self):
        clock = VirtualClock()
        events = []
        clock.call_repeated(2.0, lambda: events.append(("tick", clock.now())))
        clock.call_later(3.0, lambda: events.append(("one-shot", clock.now())))
        clock.advance_to(5.0)
        self.assertEqual(
            events,
            [("tick", 2.0), ("one-shot", 3.0), ("tick", 4.0)],
        )

    def test_cancelled_repeating_timer_stops(self):
        clock = VirtualClock()
        fires = []
        handle = clock.call_repeated(1.0, lambda: fires.append(clock.now()))
        clock.advance_to(3.0)
        handle.cancel()
        clock.advance_to(100.0)
        self.assertEqual(fires, [1.0, 2.0, 3.0])

    def test_cancel_from_inside_the_repeating_callback(self):
        clock = VirtualClock()
        fires = []
        holder = {}

        def tick():
            fires.append(clock.now())
            if len(fires) == 2:
                holder["h"].cancel()

        holder["h"] = clock.call_repeated(1.0, tick)
        clock.advance_to(10.0)
        self.assertEqual(fires, [1.0, 2.0])

    def test_non_positive_period_rejected(self):
        clock = VirtualClock()
        with self.assertRaises(ValueError):
            clock.call_repeated(0.0, lambda: None)


if __name__ == "__main__":
    unittest.main()
