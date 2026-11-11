"""Circuit breaker: stop hammering a dependency that is clearly down.

CLOSED    -- calls flow normally; consecutive failures are counted.
OPEN      -- after N consecutive failures, calls are rejected locally for `cooldown` seconds.
HALF_OPEN -- after the cooldown, one trial call is let through.
             Success -> CLOSED. Failure -> OPEN again (new cooldown).
"""
from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Callable, TypeVar

log = logging.getLogger(__name__)
T = TypeVar("T")


class State(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitOpenError(Exception):
    def __init__(self, name: str, retry_in: float) -> None:
        super().__init__(f"circuit '{name}' is OPEN, retry in {retry_in:.1f}s")
        self.retry_in = retry_in


class CircuitBreaker:
    def __init__(
        self,
        name: str = "dependency",
        failure_threshold: int = 3,
        cooldown: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self.clock = clock
        self.failures = 0
        self._state = State.CLOSED
        self._opened_at = 0.0

    @property
    def state(self) -> State:
        if self._state is State.OPEN and self.clock() - self._opened_at >= self.cooldown:
            self._transition(State.HALF_OPEN)
        return self._state

    def call(self, fn: Callable[..., T], *args, **kwargs) -> T:
        if self.state is State.OPEN:
            raise CircuitOpenError(self.name, self.cooldown - (self.clock() - self._opened_at))
        try:
            result = fn(*args, **kwargs)
        except Exception:
            self._on_failure()
            raise
        self._on_success()
        return result

    def _on_success(self) -> None:
        self.failures = 0
        if self._state is not State.CLOSED:
            self._transition(State.CLOSED)

    def _on_failure(self) -> None:
        self.failures += 1
        if self._state is State.HALF_OPEN or self.failures >= self.failure_threshold:
            self._opened_at = self.clock()
            self._transition(State.OPEN)

    def _transition(self, new: State) -> None:
        log.warning("circuit '%s': %s -> %s", self.name, self._state.value, new.value)
        self._state = new
