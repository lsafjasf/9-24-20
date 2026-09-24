"""Injectable clock abstraction with virtual and real implementations.

Only the Python standard library is used.

Interface (abstract class ``Clock``):
    now()                          -> current time in seconds (float)
    sleep(seconds)                 -> block (or advance) for the given duration
    call_later(delay, cb, *args)   -> one-shot timer, returns TimerHandle
    call_at(when, cb, *args)       -> one-shot timer at an absolute time
    call_repeated(period, cb, ...) -> repeating timer, returns TimerHandle
    wait_until(predicate)          -> block until predicate() is true

Semantics guaranteed by BOTH implementations:
  * Timers are fired in (deadline, registration-order) order: callbacks
    registered for the same deadline fire FIFO.
  * Timers registered while the clock is advancing are inserted into the
    pending set and fire according to their own deadline -- never lost,
    never fired before their deadline.
  * A cancelled TimerHandle never fires again, no matter how far time
    advances.
  * Repeating timers are rescheduled at deadline + period (no drift), so
    they produce instances on a stable periodic grid.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from abc import ABC, abstractmethod


class TimerHandle:
    """Handle returned by every scheduling call. Cancel to disarm."""

    __slots__ = ("cancelled",)

    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class Clock(ABC):
    @abstractmethod
    def now(self) -> float:
        """Current time in seconds (monotonic)."""

    @abstractmethod
    def sleep(self, seconds: float) -> None:
        """Block the caller for the given duration."""

    @abstractmethod
    def call_later(self, delay: float, callback, *args) -> TimerHandle:
        """Schedule callback(*args) to run once after `delay` seconds."""

    @abstractmethod
    def call_at(self, when: float, callback, *args) -> TimerHandle:
        """Schedule callback(*args) to run once at absolute time `when`."""

    @abstractmethod
    def call_repeated(self, period: float, callback, *args) -> TimerHandle:
        """Schedule callback(*args) to run every `period` seconds."""

    @abstractmethod
    def wait_until(self, predicate) -> None:
        """Block until predicate() returns True."""


class VirtualClock(Clock):
    """Deterministic, single-threaded clock driven explicitly by the test.

    Time only moves when the test calls advance_to_next(), advance_to() or
    sleep(). Callbacks run synchronously on the caller's thread.
    """

    def __init__(self, start: float = 0.0) -> None:
        self._now = float(start)
        # Heap entries: (deadline, seq, handle, callback, args, period|None)
        self._heap: list = []
        self._seq = itertools.count()

    # -- reading time ----------------------------------------------------
    def now(self) -> float:
        return self._now

    # -- scheduling ------------------------------------------------------
    def call_later(self, delay: float, callback, *args) -> TimerHandle:
        return self._schedule(self._now + delay, callback, args, None)

    def call_at(self, when: float, callback, *args) -> TimerHandle:
        return self._schedule(when, callback, args, None)

    def call_repeated(self, period: float, callback, *args) -> TimerHandle:
        if period <= 0:
            raise ValueError("period must be positive")
        return self._schedule(self._now + period, callback, args, period)

    def _schedule(self, deadline, callback, args, period) -> TimerHandle:
        handle = TimerHandle()
        heapq.heappush(
            self._heap, (deadline, next(self._seq), handle, callback, args, period)
        )
        return handle

    @property
    def pending(self) -> int:
        """Number of live (non-cancelled) scheduled timers."""
        return sum(1 for entry in self._heap if not entry[2].cancelled)

    # -- driving time ----------------------------------------------------
    def advance_to_next(self) -> float:
        """Advance to the next due timer and fire everything due at that
        instant (FIFO for ties). Returns the time advanced to."""
        self._discard_cancelled()
        if not self._heap:
            raise RuntimeError("advance_to_next() with no pending timers")
        deadline = self._heap[0][0]
        self.advance_to(deadline)
        return deadline

    def advance_to(self, when: float) -> None:
        """Advance to absolute time `when`, firing every timer due at or
        before it, in (deadline, registration) order. Timers scheduled by
        callbacks during the advance take part in the same ordering."""
        if when < self._now:
            raise ValueError("cannot move the clock backwards")
        while True:
            self._discard_cancelled()
            if not self._heap or self._heap[0][0] > when:
                break
            deadline, _seq, handle, callback, args, period = heapq.heappop(self._heap)
            self._now = deadline
            if period is not None:
                # Reschedule on a stable grid (deadline + period, no drift)
                # *before* firing, so a callback may cancel future instances.
                heapq.heappush(
                    self._heap,
                    (deadline + period, next(self._seq), handle, callback, args, period),
                )
            callback(*args)
        self._now = when

    def sleep(self, seconds: float) -> None:
        """Sleeping on a virtual clock simply advances it."""
        if seconds < 0:
            raise ValueError("cannot sleep for a negative duration")
        self.advance_to(self._now + seconds)

    def wait_until(self, predicate) -> None:
        """Advance event by event until predicate() holds. Raises if the
        timer queue runs dry first (i.e. the logic would deadlock)."""
        while not predicate():
            self.advance_to_next()

    def _discard_cancelled(self) -> None:
        while self._heap and self._heap[0][2].cancelled:
            heapq.heappop(self._heap)


class RealClock(Clock):
    """Wall-clock implementation.

    A single background thread fires timers from a (deadline, seq) heap, so
    ordering semantics match VirtualClock exactly: same-deadline callbacks
    fire FIFO, and timers scheduled from inside callbacks join the same
    ordered queue.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._heap: list = []
        self._seq = itertools.count()
        self._closed = False
        self.errors: list = []  # exceptions raised by callbacks
        self._thread = threading.Thread(
            target=self._run, name="RealClock", daemon=True
        )
        self._thread.start()

    def now(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def call_later(self, delay: float, callback, *args) -> TimerHandle:
        return self._schedule(time.monotonic() + delay, callback, args, None)

    def call_at(self, when: float, callback, *args) -> TimerHandle:
        return self._schedule(when, callback, args, None)

    def call_repeated(self, period: float, callback, *args) -> TimerHandle:
        if period <= 0:
            raise ValueError("period must be positive")
        return self._schedule(time.monotonic() + period, callback, args, period)

    def _schedule(self, deadline, callback, args, period) -> TimerHandle:
        handle = TimerHandle()
        with self._cond:
            heapq.heappush(
                self._heap, (deadline, next(self._seq), handle, callback, args, period)
            )
            self._cond.notify()
        return handle

    def wait_until(self, predicate, poll: float = 0.0002) -> None:
        while not predicate():
            time.sleep(poll)

    def close(self) -> None:
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        self._thread.join(timeout=2.0)

    def __enter__(self) -> "RealClock":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _run(self) -> None:
        while True:
            with self._cond:
                while True:
                    if self._closed:
                        return
                    while self._heap and self._heap[0][2].cancelled:
                        heapq.heappop(self._heap)
                    if not self._heap:
                        self._cond.wait()
                        continue
                    deadline = self._heap[0][0]
                    remaining = deadline - time.monotonic()
                    if remaining > 0:
                        self._cond.wait(remaining)
                        continue
                    _dl, _seq, handle, callback, args, period = heapq.heappop(self._heap)
                    if period is not None:
                        heapq.heappush(
                            self._heap,
                            (deadline + period, next(self._seq), handle, callback, args, period),
                        )
                    break
            try:
                callback(*args)
            except Exception as exc:  # surfaced to the test via .errors
                self.errors.append(exc)
