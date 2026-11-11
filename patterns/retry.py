"""Retry with error classification.

Not every failure deserves a retry:
  TRANSIENT (timeouts, locked files)        -> retry with exponential backoff + jitter
  DATA      (missing / invalid input)       -> log, skip this unit, continue the run
  SYSTEM    (bad credentials, app is down)  -> stop the run and alert a human
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable, TypeVar

from patterns.checkpoint import Checkpoint

log = logging.getLogger(__name__)
T = TypeVar("T")


class TransientError(Exception):
    """Worth retrying: the same call may succeed in a few seconds."""


class DataError(Exception):
    """This unit's input is bad. Retrying won't help; other units are fine."""


class SystemFailure(Exception):
    """The environment is broken. Continuing would only produce more failures."""


class ErrorKind(str, Enum):
    TRANSIENT = "TRANSIENT"
    DATA = "DATA"
    SYSTEM = "SYSTEM"


def classify(exc: BaseException) -> ErrorKind:
    # PermissionError covers "file is locked by another process" on Windows.
    if isinstance(exc, (TransientError, TimeoutError, ConnectionError, PermissionError)):
        return ErrorKind.TRANSIENT
    if isinstance(exc, (DataError, ValueError, KeyError)):
        return ErrorKind.DATA
    return ErrorKind.SYSTEM  # unknown errors fail safe: stop and ask a human


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base_delay: float = 2.0
    max_delay: float = 60.0
    jitter: float = 0.5  # up to +50% random spread so parallel bots don't retry in lockstep

    def delay(self, attempt: int, rng: random.Random) -> float:
        backoff = min(self.max_delay, self.base_delay * 2 ** (attempt - 1))
        return backoff * (1 + self.jitter * rng.random())


def call_with_retry(
    fn: Callable[[], T],
    policy: RetryPolicy = RetryPolicy(),
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    label: str = "call",
) -> T:
    """Run fn; retry only TRANSIENT errors. DATA/SYSTEM errors propagate immediately."""
    rng = rng or random.Random()
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if classify(exc) is not ErrorKind.TRANSIENT or attempt == policy.max_attempts:
                raise
            wait = policy.delay(attempt, rng)
            log.warning("%s: attempt %d failed (%s: %s), retrying in %.1fs",
                        label, attempt, type(exc).__name__, exc, wait)
            sleep(wait)
    raise AssertionError("unreachable")


@dataclass
class RunSummary:
    succeeded: list[str] = field(default_factory=list)
    resumed: list[str] = field(default_factory=list)  # done in an earlier run
    skipped: dict[str, str] = field(default_factory=dict)  # unit -> reason
    stopped_at: str | None = None
    stop_reason: str | None = None

    def report(self) -> str:
        lines = [
            f"succeeded this run : {len(self.succeeded)}",
            f"already done       : {len(self.resumed)} (from checkpoint)",
            f"skipped            : {len(self.skipped)}",
        ]
        lines += [f"  - {unit}: {reason}" for unit, reason in self.skipped.items()]
        if self.stopped_at:
            lines.append(f"STOPPED at {self.stopped_at}: {self.stop_reason}")
        return "\n".join(lines)


def process_units(
    units: Iterable[str],
    handler: Callable[[str], None],
    checkpoint: Checkpoint,
    *,
    policy: RetryPolicy = RetryPolicy(),
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    alert: Callable[[str], None] = lambda msg: log.critical("ALERT: %s", msg),
) -> RunSummary:
    """Process each unit once, with classification-aware retries and per-unit checkpoints."""
    summary = RunSummary()
    rng = rng or random.Random()
    for unit in units:
        if checkpoint.is_done(unit):
            summary.resumed.append(unit)
            continue
        try:
            call_with_retry(lambda: handler(unit), policy, sleep, rng, label=unit)
        except Exception as exc:
            kind = classify(exc)
            reason = f"{kind.value}: {exc}"
            if kind is ErrorKind.SYSTEM:
                summary.stopped_at, summary.stop_reason = unit, reason
                alert(f"run stopped at {unit} -- {reason}")
                break
            # DATA, or TRANSIENT that exhausted its retries: skip, don't checkpoint,
            # so the next run picks it up again.
            if kind is ErrorKind.TRANSIENT:
                reason += " (retries exhausted)"
            log.warning("%s: skipped -- %s", unit, reason)
            summary.skipped[unit] = reason
            continue
        checkpoint.mark_done(unit)
        summary.succeeded.append(unit)
        log.info("%s: done (checkpointed)", unit)
    return summary
