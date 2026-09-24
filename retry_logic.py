"""Example timeout-and-retry logic written against the injected clock."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


SuccessCallback = Callable[[Any], None]
FailureCallback = Callable[[BaseException], None]
Operation = Callable[[int, SuccessCallback, FailureCallback], Any]


@dataclass(frozen=True)
class RetryOptions:
    attempts: int = 3
    timeout: float | int = 1.0
    retry_delay: float | int = 0.5

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError("attempts must be at least 1")
        if self.timeout < 0:
            raise ValueError("timeout must be non-negative")
        if self.retry_delay < 0:
            raise ValueError("retry_delay must be non-negative")


def run_with_timeout_and_retries(
    clock: Any,
    operation: Operation,
    options: RetryOptions,
    events: list[tuple[Any, ...]],
    on_finished: Callable[[str], None] | None = None,
) -> None:
    """Run callback-based asynchronous operation with timeout and retries.

    The function performs no real work and knows nothing about whether
    ``clock`` is virtual or real. Timers are armed in this order:

    1. timeout timer
    2. operation response timer

    Consequently, when both are due at exactly the same instant the timeout
    wins, matching their registration order.
    """

    state = {"active": True, "attempt": 0}

    def finish(result: str) -> None:
        if not state["active"]:
            return
        state["active"] = False
        events.append(("finish", result, clock.now()))
        if on_finished is not None:
            on_finished(result)

    def schedule_retry(attempt: int) -> None:
        events.append(("schedule_retry", attempt, clock.now()))
        clock.call_later(options.retry_delay, start_attempt, attempt + 1)

    def start_attempt(attempt: int) -> None:
        if not state["active"]:
            return
        state["attempt"] = attempt
        events.append(("start", attempt, clock.now()))

        def on_timeout() -> None:
            if not state["active"] or state["attempt"] != attempt:
                return
            events.append(("timeout", attempt, clock.now()))
            response.cancel()

            if attempt < options.attempts:
                schedule_retry(attempt)
            else:
                finish("timeout")

        timeout = clock.call_later(options.timeout, on_timeout)

        def on_success(value: Any) -> None:
            if not state["active"] or state["attempt"] != attempt:
                return
            timeout.cancel()
            events.append(
                ("response", attempt, "success", value, clock.now())
            )
            finish("success")

        def on_failure(error: BaseException) -> None:
            if not state["active"] or state["attempt"] != attempt:
                return
            timeout.cancel()
            events.append(
                ("response", attempt, "failure", str(error), clock.now())
            )

            if attempt < options.attempts:
                schedule_retry(attempt)
            else:
                finish("failure")

        response = operation(attempt, on_success, on_failure)

    start_attempt(1)


def make_operation(
    clock: Any,
    response_plan: dict[int, tuple[float | int, bool, Any]],
) -> Operation:
    """Create a fake asynchronous operation scheduled through the clock."""

    def operation(
        attempt: int,
        on_success: SuccessCallback,
        on_failure: FailureCallback,
    ) -> Any:
        delay, successful, payload = response_plan[attempt]

        if successful:
            return clock.call_later(delay, on_success, payload)

        error = RuntimeError(f"attempt {attempt} failed: {payload}")
        return clock.call_later(delay, on_failure, error)

    return operation
