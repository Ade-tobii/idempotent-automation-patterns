"""Demo: the bank accepts a payment, then times out before the bot logs success.

    python -m examples.payment_posting
"""
from __future__ import annotations

import logging

from examples.fakes import FakeBank, banner, setup_logging
from patterns.idempotency import Ledger, Payment, post_once

log = logging.getLogger("demo")

PAYMENTS = [
    Payment("ACC-1001", "1500.00", "2026-01-15", "INV-001"),
    Payment("ACC-1002", "820.50", "2026-01-15", "INV-002"),  # bank accepts, then times out
    Payment("ACC-1003", "4300.00", "2026-01-15", "INV-003"),
]


def naive_bot() -> FakeBank:
    banner("1) Naive bot: 'it timed out, so post it again'")
    bank = FakeBank(timeout_after_accept={"INV-002"})
    failed = []
    for p in PAYMENTS:
        try:
            bank.post(p)
            log.info("%s: posted", p.reference)
        except TimeoutError as exc:
            log.warning("%s: %s -> queued for retry", p.reference, exc)
            failed.append(p)
    for p in failed:
        bank.post(p)
        log.info("%s: posted on retry", p.reference)
    return bank


def ledger_bot() -> FakeBank:
    banner("2) Ledger bot: claim key as PENDING before posting, reconcile on retry")
    bank = FakeBank(timeout_after_accept={"INV-002"})
    ledger = Ledger()  # use a file path in production, e.g. Ledger("ledger.db")
    for run in (1, 2):
        print(f"\n--- run {run} ---")
        for p in PAYMENTS:
            try:
                outcome = post_once(p, ledger, bank)
                log.info("%s: outcome = %s", p.reference, outcome.value)
            except TimeoutError as exc:
                log.warning("%s: %s -> leaving key PENDING, will retry next run", p.reference, exc)
    return bank


def show(bank: FakeBank) -> None:
    for p in PAYMENTS:
        n = bank.times_posted(p.reference)
        print(f"  {p.reference}: posted {n}x {'<-- DUPLICATE PAYMENT' if n > 1 else 'ok'}")


def main() -> None:
    setup_logging()
    naive = naive_bot()
    safe = ledger_bot()
    banner("Bank side, what actually got paid")
    print("Naive bot:")
    show(naive)
    print("Ledger bot:")
    show(safe)


if __name__ == "__main__":
    main()
