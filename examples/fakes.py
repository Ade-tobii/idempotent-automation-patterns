"""Deterministic fakes used by the demos and tests. No real systems, no real data."""
from __future__ import annotations

import logging
import random
import sys

from patterns.idempotency import Payment
from patterns.retry import DataError, SystemFailure


class FakeClock:
    """Virtual time. Pass `clock` where a time source is expected and `clock.sleep` for sleep."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeBank:
    """A bank API that can accept a payment and then time out before replying."""

    def __init__(self, timeout_after_accept=(), timeout_before_accept=()) -> None:
        self.transactions: list[tuple[str, Payment]] = []
        self._after = set(timeout_after_accept)
        self._before = set(timeout_before_accept)

    def post(self, p: Payment) -> str:
        if p.reference in self._before:
            self._before.discard(p.reference)
            raise TimeoutError("bank timed out (payment NOT received)")
        txn_id = f"TXN-{len(self.transactions) + 1:04d}"
        self.transactions.append((txn_id, p))
        if p.reference in self._after:
            self._after.discard(p.reference)
            raise TimeoutError("bank timed out (payment WAS accepted)")
        return txn_id

    def find_on_statement(self, p: Payment) -> str | None:
        return next((txn for txn, q in self.transactions if q == p), None)

    def times_posted(self, reference: str) -> int:
        return sum(1 for _, q in self.transactions if q.reference == reference)


class FakeFacilitySource:
    """32 facilities' month-end exports, with seeded transient noise and scripted failures."""

    def __init__(self, seed: int = 7, transient_rate: float = 0.3,
                 missing: set[str] = frozenset(), credentials_expire_at: str | None = None) -> None:
        self.rng = random.Random(seed)
        self.transient_rate = transient_rate
        self.missing = set(missing)
        self.credentials_expire_at = credentials_expire_at
        self.credentials_ok = True
        self.fetch_calls: list[str] = []

    def fetch(self, facility: str) -> dict:
        self.fetch_calls.append(facility)
        if facility == self.credentials_expire_at:
            self.credentials_ok = False
        if not self.credentials_ok:
            raise SystemFailure("login rejected: service-account password expired")
        roll = self.rng.random()
        if roll < self.transient_rate / 2:
            raise TimeoutError("portal timed out")
        if roll < self.transient_rate:
            raise PermissionError("export file locked by another process")
        if facility in self.missing:
            raise DataError("closing-balance file missing for this period")
        return {"facility": facility, "closing_balance": round(self.rng.uniform(1e4, 9e4), 2)}


class FlakyAPI:
    """Downstream API that is hard-down during [down_from, down_until) in virtual time."""

    def __init__(self, clock: FakeClock, down_from: float, down_until: float, latency: float = 1.0) -> None:
        self.clock, self.down_from, self.down_until, self.latency = clock, down_from, down_until, latency
        self.calls = 0
        self.calls_while_down = 0
        self.received_ids: list[str] = []

    def send(self, rows: list[dict]) -> None:
        self.calls += 1
        self.clock.sleep(self.latency)
        if self.down_from <= self.clock() < self.down_until:
            self.calls_while_down += 1
            raise ConnectionError("503 Service Unavailable")
        self.received_ids.extend(r["id"] for r in rows)


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s", stream=sys.stdout)


def banner(title: str) -> None:
    print(f"\n{'=' * 70}\n{title}\n{'=' * 70}")
