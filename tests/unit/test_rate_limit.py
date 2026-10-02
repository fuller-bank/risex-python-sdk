import asyncio

import pytest

from risex.rate_limit import RateLimiter


async def test_concurrent_requests_are_paced_without_a_burst(monkeypatch):
    now = 100.0
    sent_at = []

    async def sleep(delay):
        nonlocal now
        now += delay

    monkeypatch.setattr("risex.rate_limit.time.monotonic", lambda: now)
    monkeypatch.setattr("risex.rate_limit.asyncio.sleep", sleep)
    limiter = RateLimiter(4)

    async def request():
        await limiter.acquire()
        sent_at.append(now)

    await asyncio.gather(*(request() for _ in range(5)))
    assert sent_at == [100, 100.25, 100.5, 100.75, 101]


async def test_disabled_limiter_does_not_sleep(monkeypatch):
    async def sleep(_):
        raise AssertionError("Disabled limiter must not sleep")

    monkeypatch.setattr("risex.rate_limit.asyncio.sleep", sleep)
    await RateLimiter(None).acquire()


@pytest.mark.parametrize("rate", [0, -1, True, float("nan"), float("inf")])
def test_invalid_pacing_is_rejected(rate):
    with pytest.raises(ValueError):
        RateLimiter(rate)
