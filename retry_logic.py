"""Logic under test: fetch with per-attempt timeout, retries and backoff.

The code only ever talks to the injected ``Clock`` -- it never touches
``time`` or ``threading`` directly -- so the exact same code runs under
``VirtualClock`` and ``RealClock``.
"""

from virtual_clock import Clock


class FlakyService:
    """Scripted asynchronous service.

    ``script[i] = (delay, ok, value)`` describes how attempt ``i`` behaves:
    it calls back ``delay`` seconds after start with ``(ok, value)``.
    The service itself also only uses the injected clock, which is what
    makes virtual/real parity testable.
    """

    def __init__(self, clock: Clock, script) -> None:
        self._clock = clock
        self._script = list(script)
        self.attempts = 0

    def start(self, callback) -> None:
        delay, ok, value = self._script[min(self.attempts, len(self._script) - 1)]
        self.attempts += 1
        self._clock.call_later(delay, callback, ok, value)


def fetch_with_retry(clock: Clock, service: FlakyService, *,
                     timeout: float, retries: int, backoff: float, log: list):
    """Try the service up to ``retries + 1`` times.

    Each attempt is bounded by ``timeout``; failed/timed-out attempts are
    followed by a ``backoff`` sleep. Every step is appended to ``log`` as
    ``(kind, detail)`` so tests can assert the exact event order.
    Returns ``(status, value)`` with status in {"success", "exhausted"}.
    """
    for attempt in range(retries + 1):
        log.append(("attempt", attempt))
        state = {"result": None, "timed_out": False}

        def on_result(ok, value, _state=state):
            _state["result"] = (ok, value)

        def on_timeout(_state=state):
            _state["timed_out"] = True

        timer = clock.call_later(timeout, on_timeout)
        service.start(on_result)
        clock.wait_until(lambda: state["result"] is not None or state["timed_out"])

        if state["timed_out"]:
            log.append(("timeout", attempt))
        else:
            timer.cancel()
            ok, value = state["result"]
            if ok:
                log.append(("success", value))
                return "success", value
            log.append(("failure", value))

        if attempt < retries:
            log.append(("backoff", attempt))
            clock.sleep(backoff)

    log.append(("exhausted", None))
    return "exhausted", None
