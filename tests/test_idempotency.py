import pytest

from examples.fakes import FakeBank
from patterns.idempotency import Ledger, Outcome, Payment, Status, idempotency_key, post_once

P = Payment("ACC-1", "100.00", "2026-01-15", "INV-9")


def test_key_is_deterministic_and_normalized():
    same = Payment(" ACC-1 ", "100", "2026-01-15", "inv-9")
    assert idempotency_key(P) == idempotency_key(same)
    assert idempotency_key(P) != idempotency_key(Payment("ACC-1", "100.01", "2026-01-15", "INV-9"))


def test_same_payment_twice_posts_once():
    bank, ledger = FakeBank(), Ledger()
    assert post_once(P, ledger, bank) is Outcome.POSTED
    assert post_once(P, ledger, bank) is Outcome.ALREADY_COMPLETE
    assert bank.times_posted("INV-9") == 1


def test_ledger_unique_constraint_blocks_second_claim():
    ledger = Ledger()
    assert ledger.claim("k", "INV-9") is True
    assert ledger.claim("k", "INV-9") is False


def test_timeout_after_accept_reconciles_instead_of_reposting():
    bank, ledger = FakeBank(timeout_after_accept={"INV-9"}), Ledger()
    with pytest.raises(TimeoutError):
        post_once(P, ledger, bank)
    assert ledger.status(idempotency_key(P)) is Status.PENDING

    assert post_once(P, ledger, bank) is Outcome.RECONCILED
    assert ledger.status(idempotency_key(P)) is Status.COMPLETE
    assert bank.times_posted("INV-9") == 1


def test_pending_not_on_statement_is_flagged_not_reposted():
    bank, ledger = FakeBank(timeout_before_accept={"INV-9"}), Ledger()
    with pytest.raises(TimeoutError):
        post_once(P, ledger, bank)

    assert post_once(P, ledger, bank) is Outcome.NEEDS_REVIEW
    assert bank.times_posted("INV-9") == 0
    assert ledger.status(idempotency_key(P)) is Status.PENDING


def test_ledger_survives_restart(tmp_path):
    db = str(tmp_path / "ledger.db")
    bank = FakeBank()
    post_once(P, Ledger(db), bank)
    assert post_once(P, Ledger(db), bank) is Outcome.ALREADY_COMPLETE
    assert bank.times_posted("INV-9") == 1
