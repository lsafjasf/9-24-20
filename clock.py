"""Injectable virtual and real clocks.

The clocks expose the same small interface:

* ``now()`` reads the current time.
* ``call_later()`` / ``call_at()`` schedule one-shot callbacks.
* ``repeat()`` schedules a stable periodic callback.
* ``sleep()`` asynchronously sleeps using the injected clock.

Returned timer objects all provide ``cancel()``, ``active()`` and ``when()``.
"""

from __future__ import annotations

import asyncio
import heapq
from collections import namedtuple
from typing import Any, Callable


_Entry = namedtuple("_Entry", ("when", "seq", "timer"))


def _number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    value = float(value)
    if value != value:
        raise ValueError(f"{name} must not be NaN")
    return value


class VirtualTimer:
    """A timer owned by :class:`VirtualClock`."""

    __slots__ = (
        "_when",
        "_interval",
        "_seq",
        "_callback",
        "_args",
        "_active",
        "_cancel_requested",
    )

    def __init__(
        self,
        when: float,
        interval: float | None,
        seq: int,
        callback: Callable[..., Any],
        args: tuple[Any, ...],
    ) -> None:
        self._when = when
        self._interval = interval
        self._seq = seq
        self._callback = callback
        self._args = args
        self._active = True
        self._cancel_requested = False

    def cancel(self) -> bool:
        """Cancel this timer.

        Cancelling an already fired or cancelled timer is a no-op.
        """
        if not self._active:
            return False
        self._active = False
        self._cancel_requested = True
        return True

    def active(self) -> bool:
        return self._active

    def cancelled(self) -> bool:
        return self._cancel_requested

    def when(self) -> float:
        return self._when


class VirtualClock:
    """Deterministic in-process clock controlled explicitly by tests."""

    def __init__(self, start_time: float | int = 0.0) -> None:
        start_time = _number(start_time, "start_time")
        if start_time == float("inf") or start_time == float("-inf"):
            raise ValueError("start_time must be finite")
        self._now = float(start_time)
        self._seq = 0
        self._heap: list[_Entry] = []

    def now(self) -> float:
        return self._now

    def pending_count(self) -> int:
        active_entries = [
            entry for entry in self._heap if entry.timer._active
        ]
        if len(active_entries) != len(self._heap):
            self._heap = active_entries
            heapq.heapify(self._heap)
        return len(self._heap)

    def call_later(
        self,
        delay: float | int,
        callback: Callable[..., Any],
        *args: Any,
    ) -> VirtualTimer:
        delay = _number(delay, "delay")
        if delay < 0:
            raise ValueError("delay must be non-negative")
        return self.call_at(self._now + delay, callback, *args)

    def call_at(
        self,
        when: float | int,
        callback: Callable[..., Any],
        *args: Any,
    ) -> VirtualTimer:
        when = _number(when, "when")
        if when == float("inf") or when == float("-inf"):
            raise ValueError("when must be finite")
        if when < self._now:
            raise ValueError("cannot schedule a callback in the past")
        if not callable(callback):
            raise TypeError("callback must be callable")
        return self._register(when, None, callback, args)

    def repeat(
        self,
        interval: float | int,
        callback: Callable[..., Any],
        *args: Any,
    ) -> VirtualTimer:
        interval = _number(interval, "interval")
        if interval <= 0:
            raise ValueError("interval must be positive")
        return self.repeat_at(self._now + interval, interval, callback, *args)

    def repeat_at(
        self,
        when: float | int,
        interval: float | int,
        callback: Callable[..., Any],
        *args: Any,
    ) -> VirtualTimer:
        when = _number(when, "when")
        interval = _number(interval, "interval")
        if when < self._now:
            raise ValueError("cannot schedule a callback in the past")
        if interval <= 0:
            raise ValueError("interval must be positive")
        if not callable(callback):
            raise TypeError("callback must be callable")
        return self._register(when, interval, callback, args)

    def advance_next(self) -> float | None:
        """Advance to and fire exactly one due callback.

        Returns the callback's absolute time, or ``None`` when no timer is
        pending. Callbacks registered for the current time while a callback
        is running remain in the same heap-ordered sequence.
        """
        entry = self._pop_due(at=None)
        if entry is None:
            return None
        self._now = entry.when
        self._fire(entry)
        return entry.when

    def advance_to(self, target: float | int) -> None:
        """Advance to ``target`` and fire every callback due by then."""
        target = _number(target, "target")
        if target in (float("inf"), float("-inf")):
            raise ValueError("target must be finite")
        if target < self._now:
            raise ValueError("cannot move the clock backwards")

        while True:
            entry = self._pop_due(at=target)
            if entry is None:
                break
            self._now = entry.when
            self._fire(entry)

        self._now = target

    async def sleep(self, delay: float | int) -> None:
        delay = _number(delay, "delay")
        if delay < 0:
            raise ValueError("delay must be non-negative")

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        timer = self.call_later(delay, future.set_result, None)
        try:
            await future
        finally:
            timer.cancel()

    def _register(
        self,
        when: float,
        interval: float | None,
        callback: Callable[..., Any],
        args: tuple[Any, ...],
    ) -> VirtualTimer:
        self._seq += 1
        timer = VirtualTimer(when, interval, self._seq, callback, args)
        heapq.heappush(self._heap, _Entry(when, self._seq, timer))
        return timer

    def _purge(self) -> None:
        while self._heap and not self._heap[0].timer._active:
            heapq.heappop(self._heap)

    def _pop_due(self, at: float | None) -> _Entry | None:
        while self._heap:
            entry = self._heap[0]
            timer = entry.timer
            if not timer._active or timer._seq != entry.seq:
                heapq.heappop(self._heap)
                continue
            if at is not None and entry.when > at:
                return None
            return heapq.heappop(self._heap)
        return None

    def _fire(self, entry: _Entry) -> None:
        timer = entry.timer

        if timer._interval is None:
            timer._active = False
        else:
            timer._when += timer._interval
            heapq.heappush(
                self._heap,
                _Entry(timer._when, timer._seq, timer),
            )

        timer._callback(*timer._args)


class RealTimer:
    """A timer backed by a running asyncio event loop."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        when: float,
        interval: float | None,
        callback: Callable[..., Any],
        args: tuple[Any, ...],
    ) -> None:
        self._loop = loop
        self._when = when
        self._interval = interval
        self._callback = callback
        self._args = args
        self._active = True
        self._cancel_requested = False
        self._handle: asyncio.Handle | asyncio.TimerHandle = self._schedule(when)

    def cancel(self) -> bool:
        if not self._active:
            return False
        self._active = False
        self._cancel_requested = True
        self._handle.cancel()
        return True

    def active(self) -> bool:
        return self._active

    def cancelled(self) -> bool:
        return self._cancel_requested

    def when(self) -> float:
        return self._when

    def _schedule(self, when: float) -> asyncio.Handle | asyncio.TimerHandle:
        delay = when - self._loop.time()
        if delay <= 0:
            return self._loop.call_soon(self._fire)
        return self._loop.call_at(when, self._fire)

    def _fire(self) -> None:
        if not self._active:
            return

        if self._interval is None:
            self._active = False
        else:
            self._when += self._interval
            self._handle = self._schedule(self._when)

        self._callback(*self._args)


class RealClock:
    """Clock backed by the monotonic clock of an asyncio event loop."""

    def __init__(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    def now(self) -> float:
        return self._loop.time()

    def call_later(
        self,
        delay: float | int,
        callback: Callable[..., Any],
        *args: Any,
    ) -> RealTimer:
        delay = _number(delay, "delay")
        if delay < 0:
            raise ValueError("delay must be non-negative")
        return self.call_at(self._loop.time() + delay, callback, *args)

    def call_at(
        self,
        when: float | int,
        callback: Callable[..., Any],
        *args: Any,
    ) -> RealTimer:
        when = _number(when, "when")
        if not callable(callback):
            raise TypeError("callback must be callable")
        return RealTimer(self._loop, when, None, callback, args)

    def repeat(
        self,
        interval: float | int,
        callback: Callable[..., Any],
        *args: Any,
    ) -> RealTimer:
        interval = _number(interval, "interval")
        if interval <= 0:
            raise ValueError("interval must be positive")
        return self.repeat_at(
            self._loop.time() + interval, interval, callback, *args
        )

    def repeat_at(
        self,
        when: float | int,
        interval: float | int,
        callback: Callable[..., Any],
        *args: Any,
    ) -> RealTimer:
        when = _number(when, "when")
        interval = _number(interval, "interval")
        if interval <= 0:
            raise ValueError("interval must be positive")
        if not callable(callback):
            raise TypeError("callback must be callable")
        return RealTimer(self._loop, when, interval, callback, args)

    async def sleep(self, delay: float | int) -> None:
        delay = _number(delay, "delay")
        if delay < 0:
            raise ValueError("delay must be non-negative")

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        timer = self.call_later(delay, future.set_result, None)
        try:
            await future
        finally:
            timer.cancel()
