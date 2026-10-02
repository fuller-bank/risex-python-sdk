import asyncio
from decimal import Decimal

import httpx
import pytest

from risex import (
    APIError,
    ClientClosedError,
    MutationContext,
    ProtocolError,
    RateLimitError,
    RiseXClient,
    TransportError,
)
from risex.rest import RestClient


async def test_market_request_and_exact_models(config, markets_payload):
    def handler(request):
        assert request.url.path == "/v1/markets"
        assert request.url.params.get_list("market_ids") == ["1", "2"]
        assert request.url.params["force_refresh"] == "true"
        return httpx.Response(200, json=markets_payload)

    async with RiseXClient(config, transport=httpx.MockTransport(handler)) as client:
        response = await client.get_markets(market_ids=[1, 2], force_refresh=True)
    market = response.markets[0]
    assert market.market_id == 1
    assert market.mark_price == Decimal("84533.372074747582246946")
    assert market.model_extra["display_at"] == "0"


async def test_orderbook_request(config, book_payload):
    def handler(request):
        assert request.url.params["market_id"] == "1"
        assert request.url.params["limit"] == "2"
        return httpx.Response(200, json=book_payload)

    async with RiseXClient(config, transport=httpx.MockTransport(handler)) as client:
        book = await client.get_orderbook(1, limit=2)
    assert book.bids[0].price == Decimal("84567.8")
    assert book.total_bids == 55


async def test_schema_bare_response(config, markets_payload):
    transport = httpx.MockTransport(lambda _: httpx.Response(200, json=markets_payload["data"]))
    async with RiseXClient(config, transport=transport) as client:
        assert (await client.get_markets()).markets[0].market_id == 1


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"data": []}),
        httpx.Response(200, json={"data": {"markets": [{"market_id": "1"}]}}),
    ],
)
async def test_bad_response_is_a_protocol_error(config, response):
    async with RiseXClient(config, transport=httpx.MockTransport(lambda _: response)) as client:
        with pytest.raises(ProtocolError):
            await client.get_markets()


async def test_read_retries_are_bounded(config, markets_payload):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("timed out", request=request)
        if calls == 2:
            return httpx.Response(503)
        return httpx.Response(200, json=markets_payload)

    async with RiseXClient(config, transport=httpx.MockTransport(handler)) as client:
        assert (await client.get_markets()).markets
    assert calls == 3


async def test_transport_retry_exhaustion(config):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    async with RiseXClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(TransportError):
            await client.get_markets()
    assert calls == 3


async def test_rate_limit_does_not_retry_early(config):
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            headers={"Retry-After": "60"},
            json={"error": {"code": 8, "message": "rate limited"}, "request_id": "req-1"},
        )

    async with RiseXClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RateLimitError) as error:
            await client.get_markets()
    assert calls == 1
    assert error.value.retry_after == 60
    assert error.value.code == 8
    assert error.value.request_id == "req-1"
    assert error.value.status_code == 429


async def test_short_retry_after_is_honored(config, markets_payload, monkeypatch):
    calls = 0
    delays = []

    async def sleep(delay):
        delays.append(delay)

    def handler(_):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "1.5"})
        return httpx.Response(200, json=markets_payload)

    monkeypatch.setattr("risex.rest.asyncio.sleep", sleep)
    async with RiseXClient(config, transport=httpx.MockTransport(handler)) as client:
        await client.get_markets()
    assert delays == [1.5]


async def test_error_preserves_provider_identifiers(config):
    calls = 0

    def handler(_):
        nonlocal calls
        calls += 1
        return httpx.Response(400, json={"code": 3, "message": "invalid", "request_id": "req-2"})

    async with RiseXClient(config, transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(APIError) as error:
            await client.get_markets()
    assert calls == 1
    assert error.value.code == 3
    assert error.value.request_id == "req-2"


async def test_wrong_orderbook_market_is_rejected(config, book_payload):
    book_payload["data"]["market_id"] = "2"
    async with RiseXClient(
        config, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=book_payload))
    ) as client:
        with pytest.raises(ProtocolError):
            await client.get_orderbook(1)


async def test_closed_client(config):
    client = RiseXClient(config)
    await client.aclose()
    await client.aclose()
    with pytest.raises(ClientClosedError):
        await client.get_markets()
    with pytest.raises(ClientClosedError):
        client.stream_orderbook()


async def test_cancel_before_send_slot_is_not_an_uncertain_mutation(config, monkeypatch):
    waiting = asyncio.Event()
    calls = []

    async def blocked_slot(_):
        waiting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr("risex.rate_limit.RateLimiter.acquire", blocked_slot)
    transport = httpx.MockTransport(lambda request: calls.append(request))
    rest = RestClient(config, transport=transport)
    try:
        task = asyncio.create_task(
            rest.post(
                "/v1/orders/place",
                payload={},
                context=MutationContext("place_order", "0x" + "00" * 20, 1, 0),
            )
        )
        await asyncio.wait_for(waiting.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not calls
    finally:
        await rest.aclose()


@pytest.mark.parametrize("market_id", [True, 0, -1, 1.0, "1", 2**64])
async def test_invalid_ids_rejected_before_network(config, market_id):
    async with RiseXClient(config) as client:
        with pytest.raises(ValueError):
            await client.get_orderbook(market_id)


@pytest.mark.parametrize("limit", [0, 251, True, 1.5])
async def test_invalid_depth_rejected(config, limit):
    async with RiseXClient(config) as client:
        with pytest.raises(ValueError):
            await client.get_orderbook(1, limit=limit)
