# Virtual Clock Test Harness

Python 3 standard-library-only injectable clock and self-tests.

## Files

- `clock.py`: `VirtualClock`, `RealClock`, timers, and asynchronous `sleep()`.
- `retry_logic.py`: timeout, cancel, and retry example logic; it receives the clock by dependency injection.
- `test_virtual_clock.py`: clock semantics, example timing, and virtual/real comparison tests.

## Clock API

```python
clock = VirtualClock(start_time=0.0)

clock.now()
timer = clock.call_later(2, callback, arg)
timer = clock.call_at(10, callback)
timer = clock.repeat(5, callback)
timer.cancel()

clock.advance_next()  # move to and process exactly one due event
clock.advance_to(20)  # process every event due by time 20

await clock.sleep(2)  # uses the injected clock inside async code
```

`RealClock` has the same scheduling and sleep API, but is backed by a running
`asyncio` event loop's monotonic clock.

## Timing semantics

- The virtual clock stores timers as `(due_time, registration_sequence)` in a heap.
- Callbacks due at the same virtual time run in registration order.
- Callbacks added while time is advancing immediately re-enter heap ordering by their new due time.
- Cancelled one-shot or periodic timers are removed lazily and never run.
- Periodic timers keep fixed phase: the next instance is due at `previous_due + interval`.
- Periodic rescheduling preserves the original registration sequence for callbacks that repeatedly coincide.

## Real-clock comparison

The comparison test runs the exact same `retry_logic.py` state machine under:

1. `VirtualClock`, advanced explicitly to zero.
2. `RealClock`, inside a real `asyncio` event loop.

All timeout, response, and retry delays in that comparison are zero, so the test
exercises real event-loop scheduling without any wall-clock wait. Timestamps are
removed before comparison because the real event loop's monotonic origin is not
zero; the event names, attempt numbers, outcomes, and order are asserted equal.

Positive-delay timing is covered separately by deterministic virtual-clock
tests with exact timestamps.

## Run

```bash
python3 -m unittest -v
```

Or:

```bash
python3 test_virtual_clock.py -v
```
