import pytest

from examples.fakes import FakeClock
from patterns.circuit_breaker import CircuitBreaker, CircuitOpenError, State


def fail():
    raise ConnectionError("down")


def ok():
    return "ok"


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def breaker(clock):
    return CircuitBreaker("api", failure_threshold=3, cooldown=30, clock=clock)


def trip(breaker):
    for _ in range(breaker.failure_threshold):
        with pytest.raises(ConnectionError):
            breaker.call(fail)


def test_opens_after_n_consecutive_failures(breaker):
    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(fail)
    assert breaker.state is State.CLOSED
    with pytest.raises(ConnectionError):
        breaker.call(fail)
    assert breaker.state is State.OPEN


def test_success_resets_failure_count(breaker):
    for _ in range(2):
        with pytest.raises(ConnectionError):
            breaker.call(fail)
    breaker.call(ok)
    with pytest.raises(ConnectionError):
        breaker.call(fail)
    assert breaker.state is State.CLOSED


def test_open_rejects_without_calling(breaker, clock):
    trip(breaker)
    calls = []
    clock.now += 10
    with pytest.raises(CircuitOpenError) as err:
        breaker.call(lambda: calls.append(1))
    assert calls == []
    assert err.value.retry_in == pytest.approx(20)


def test_half_open_after_cooldown_then_success_closes(breaker, clock):
    trip(breaker)
    clock.now += 30
    assert breaker.state is State.HALF_OPEN
    assert breaker.call(ok) == "ok"
    assert breaker.state is State.CLOSED


def test_half_open_failure_reopens_with_new_cooldown(breaker, clock):
    trip(breaker)
    clock.now += 30
    with pytest.raises(ConnectionError):
        breaker.call(fail)  # single trial call fails
    assert breaker.state is State.OPEN
    clock.now += 29
    assert breaker.state is State.OPEN
    clock.now += 1
    assert breaker.state is State.HALF_OPEN
