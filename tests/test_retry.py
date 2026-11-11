import random

import pytest

from examples.fakes import FakeClock
from patterns.checkpoint import Checkpoint
from patterns.retry import (DataError, ErrorKind, RetryPolicy, SystemFailure, TransientError,
                            call_with_retry, classify, process_units)

NO_JITTER = RetryPolicy(max_attempts=4, base_delay=1.0, jitter=0.0)


@pytest.mark.parametrize("exc, kind", [
    (TimeoutError(), ErrorKind.TRANSIENT),
    (PermissionError("locked"), ErrorKind.TRANSIENT),
    (TransientError(), ErrorKind.TRANSIENT),
    (DataError(), ErrorKind.DATA),
    (ValueError(), ErrorKind.DATA),
    (SystemFailure(), ErrorKind.SYSTEM),
    (RuntimeError("unknown"), ErrorKind.SYSTEM),
])
def test_classify(exc, kind):
    assert classify(exc) is kind


def flaky(failures, exc=TimeoutError):
    calls = []

    def fn():
        calls.append(1)
        if len(calls) <= failures:
            raise exc("boom")
        return "ok"
    return fn, calls


def test_transient_is_retried_with_exponential_backoff():
    clock = FakeClock()
    fn, calls = flaky(3)
    assert call_with_retry(fn, NO_JITTER, clock.sleep) == "ok"
    assert len(calls) == 4
    assert clock.sleeps == [1.0, 2.0, 4.0]


def test_jitter_stays_within_bounds():
    policy = RetryPolicy(base_delay=10, jitter=0.5)
    delays = [policy.delay(1, random.Random(s)) for s in range(50)]
    assert all(10 <= d <= 15 for d in delays)


@pytest.mark.parametrize("exc", [DataError, SystemFailure])
def test_data_and_system_errors_are_not_retried(exc):
    clock = FakeClock()
    fn, calls = flaky(1, exc)
    with pytest.raises(exc):
        call_with_retry(fn, NO_JITTER, clock.sleep)
    assert len(calls) == 1 and clock.sleeps == []


def run(units, handler, cp, alerts=None):
    return process_units(units, handler, cp, policy=NO_JITTER, sleep=FakeClock().sleep,
                         alert=(alerts.append if alerts is not None else lambda m: None))


def test_data_error_skips_unit_and_continues(tmp_path):
    def handler(u):
        if u == "B":
            raise DataError("missing file")
    summary = run(["A", "B", "C"], handler, Checkpoint(tmp_path / "cp.json"))
    assert summary.succeeded == ["A", "C"]
    assert "missing file" in summary.skipped["B"]
    assert summary.stopped_at is None


def test_system_error_stops_run_and_alerts(tmp_path):
    seen, alerts = [], []

    def handler(u):
        seen.append(u)
        if u == "B":
            raise SystemFailure("credentials expired")
    summary = run(["A", "B", "C"], handler, Checkpoint(tmp_path / "cp.json"), alerts)
    assert seen == ["A", "B"]
    assert summary.stopped_at == "B"
    assert len(alerts) == 1 and "credentials expired" in alerts[0]


def test_exhausted_transient_is_skipped_not_checkpointed(tmp_path):
    cp = Checkpoint(tmp_path / "cp.json")

    def handler(u):
        if u == "B":
            raise TimeoutError("still down")
    summary = run(["A", "B"], handler, cp)
    assert "retries exhausted" in summary.skipped["B"]
    assert not cp.is_done("B")


def test_resume_does_not_redo_completed_units(tmp_path):
    path = tmp_path / "cp.json"
    seen = []
    broken = {"down": True}

    def handler(u):
        seen.append(u)
        if u == "U27" and broken["down"]:
            raise SystemFailure("app down")

    units = [f"U{i}" for i in range(1, 33)]
    first = run(units, handler, Checkpoint(path))
    assert first.stopped_at == "U27" and len(first.succeeded) == 26

    broken["down"] = False
    seen.clear()
    second = run(units, handler, Checkpoint(path))
    assert seen == [f"U{i}" for i in range(27, 33)]
    assert len(second.resumed) == 26 and len(second.succeeded) == 6
