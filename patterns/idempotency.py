"""Idempotency ledger: make "post this payment" safe to run more than once.

The rule: write the intent to a durable ledger *before* touching the bank,
and mark it complete only *after* the bank confirms. If a retry finds the key
still PENDING, the previous attempt died mid-flight -- reconcile against the
bank statement instead of posting again.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Payment:
    account: str
    amount: str  # keep money as a string/Decimal, never float
    value_date: str  # ISO date, e.g. "2026-01-15"
    reference: str


def idempotency_key(p: Payment) -> str:
    """Deterministic key: same business payment -> same key, on any machine, any run."""
    normalized = "|".join([
        p.account.strip(),
        f"{Decimal(p.amount):.2f}",
        p.value_date.strip(),
        p.reference.strip().upper(),
    ])
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class Status(str, Enum):
    PENDING = "PENDING"
    COMPLETE = "COMPLETE"


class Outcome(str, Enum):
    POSTED = "POSTED"                      # first attempt, bank confirmed
    ALREADY_COMPLETE = "ALREADY_COMPLETE"  # ledger says done; nothing sent
    RECONCILED = "RECONCILED"              # was PENDING, found on statement
    NEEDS_REVIEW = "NEEDS_REVIEW"          # was PENDING, not on statement yet


class Bank(Protocol):
    def post(self, payment: Payment) -> str: ...
    def find_on_statement(self, payment: Payment) -> str | None: ...


class Ledger:
    """SQLite-backed ledger. The PRIMARY KEY on `key` is what blocks duplicates."""

    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        with self.conn:
            self.conn.execute(
                """CREATE TABLE IF NOT EXISTS ledger (
                       key         TEXT PRIMARY KEY,
                       status      TEXT NOT NULL,
                       reference   TEXT NOT NULL,
                       bank_txn_id TEXT,
                       updated_at  TEXT NOT NULL)"""
            )

    def claim(self, key: str, reference: str) -> bool:
        """Insert key as PENDING. Returns False if the key already exists."""
        try:
            with self.conn:  # commits immediately -- must be durable before the bank call
                self.conn.execute(
                    "INSERT INTO ledger VALUES (?, ?, ?, NULL, ?)",
                    (key, Status.PENDING.value, reference, _now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def status(self, key: str) -> Status | None:
        row = self.conn.execute("SELECT status FROM ledger WHERE key = ?", (key,)).fetchone()
        return Status(row[0]) if row else None

    def complete(self, key: str, bank_txn_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE ledger SET status = ?, bank_txn_id = ?, updated_at = ? WHERE key = ?",
                (Status.COMPLETE.value, bank_txn_id, _now(), key),
            )


def post_once(payment: Payment, ledger: Ledger, bank: Bank) -> Outcome:
    key = idempotency_key(payment)
    ref = payment.reference

    if ledger.claim(key, ref):
        log.info("%s: claimed key %s.. as PENDING, posting to bank", ref, key[:10])
        txn_id = bank.post(payment)  # if this raises, the key stays PENDING on purpose
        ledger.complete(key, txn_id)
        log.info("%s: bank confirmed %s -> COMPLETE", ref, txn_id)
        return Outcome.POSTED

    if ledger.status(key) is Status.COMPLETE:
        log.info("%s: already COMPLETE in ledger, skipping", ref)
        return Outcome.ALREADY_COMPLETE

    # PENDING: a previous run crashed or timed out between "sent" and "confirmed".
    log.warning("%s: found PENDING from a previous run -> reconciling, NOT reposting", ref)
    txn_id = bank.find_on_statement(payment)
    if txn_id:
        ledger.complete(key, txn_id)
        log.info("%s: found on bank statement as %s -> COMPLETE", ref, txn_id)
        return Outcome.RECONCILED
    log.error("%s: not on statement yet -> flagged for human review", ref)
    return Outcome.NEEDS_REVIEW


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
