import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from risex import (
    ConnectionEvent,
    OracleEvent,
    ProtocolError,
    PublicStream,
    ReconnectExhaustedError,
    RiseXClient,
    RiseXConfig,
    SubscriptionError,
    from_wei,
)


@asynccontextmanager
async def server_config(handler, **kwargs):
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield RiseXConfig(
            base_url="http://127.0.0.1",
            websocket_url=f"ws://127.0.0.1:{port}",
            reconnect_delay=0,
            **kwargs,
        )


def ack(channel="orderbook", market_ids=None, **kwargs):
    return {
        "type": "subscribed",
        "method": "subscribe",
        "status": "success",
        "channel": channel,
        "data": {"market_ids": [1] if market_ids is None else market_ids},
        **kwargs,
    }


async def test_snapshot_before_ack_is_retained(snapshot_message):
    async def handler(socket):
        request = json.loads(await socket.recv())
        assert request == {
            "method": "subscribe",
            "params": {"channel": "orderbook", "market_ids": [1]},
        }
        await socket.send(json.dumps(snapshot_message))
        await socket.send(json.dumps(ack()))
        await socket.wait_closed()

    async with server_config(handler) as config:
        async with PublicStream(config, "orderbook", market_ids=[1]) as stream:
            state = await anext(stream)
            snapshot = await anext(stream)
            assert state == ConnectionEvent(state="connected", stale=False)
            assert snapshot.type == "snapshot"
            assert snapshot.market_id == 1


@pytest.mark.parametrize(
    "reply",
    [
        ack(status="error", message="authentication required"),
        ack(market_ids=[]),
        ack(market_ids=["1"]),
        ack(channel="trades"),
    ],
)
async def test_rejected_or_wrong_subscription_never_yields_data(snapshot_message, reply):
    async def handler(socket):
        await socket.recv()
        await socket.send(json.dumps(snapshot_message))
        await socket.send(json.dumps(reply))
        await socket.wait_closed()

    async with server_config(handler) as config:
        async with PublicStream(config, "orderbook", market_ids=[1]) as stream:
            with pytest.raises(SubscriptionError):
                await anext(stream)


async def test_acknowledgement_timeout():
    async def handler(socket):
        await socket.recv()
        await socket.wait_closed()

    async with server_config(handler, subscription_timeout=0.05) as config:
        async with PublicStream(config, "orderbook") as stream:
            with pytest.raises(SubscriptionError, match="Timed out"):
                await anext(stream)


async def test_snapshot_buffer_is_bounded(snapshot_message):
    async def handler(socket):
        await socket.recv()
        await socket.send(json.dumps(snapshot_message))
        await socket.send(json.dumps(snapshot_message))
        await socket.wait_closed()

    async with server_config(handler, max_pending_messages=1) as config:
        async with PublicStream(config, "orderbook") as stream:
            with pytest.raises(ProtocolError, match="Too many"):
                await anext(stream)


async def test_reconnect_resubscribes_and_reports_staleness(snapshot_message):
    connections = 0

    async def handler(socket):
        nonlocal connections
        connections += 1
        assert json.loads(await socket.recv())["params"]["market_ids"] == [1]
        await socket.send(json.dumps(snapshot_message))
        await socket.send(json.dumps(ack()))
        if connections == 1:
            await socket.close()
        else:
            await socket.wait_closed()

    async with server_config(handler) as config:
        async with PublicStream(config, "orderbook", market_ids=[1]) as stream:
            events = [await anext(stream) for _ in range(6)]
    assert [
        getattr(event, "state", event.type if hasattr(event, "type") else None) for event in events
    ] == ["connected", "snapshot", "disconnected", "reconnecting", "connected", "snapshot"]
    assert events[2].stale and events[4].stale
    assert connections == 2


async def test_reconnect_budget_not_reset_by_ack_alone():
    connections = 0

    async def handler(socket):
        nonlocal connections
        connections += 1
        await socket.recv()
        await socket.send(json.dumps(ack(channel="trades")))
        await socket.close()

    async with server_config(handler, max_reconnect_attempts=1) as config:
        async with PublicStream(config, "trades", market_ids=[1]) as stream:
            with pytest.raises(ReconnectExhaustedError):
                async for _ in stream:
                    pass
    assert connections == 2


async def test_oracle_wei_is_not_decoded_as_decimal():
    async def handler(socket):
        await socket.recv()
        await socket.send(json.dumps(ack(channel="oracle")))
        await socket.send(
            json.dumps(
                {
                    "channel": "oracle",
                    "type": "update",
                    "market_id": None,
                    "worker_timestamp": "1786931315106267297",
                    "data": {
                        "prices": {"1": {"mark_price": "63126084821834306372811"}},
                        "timestamp": 1786931315,
                    },
                }
            )
        )
        await socket.wait_closed()

    async with server_config(handler) as config:
        async with PublicStream(config, "oracle", market_ids=[1]) as stream:
            await anext(stream)
            event = await anext(stream)
    assert isinstance(event, OracleEvent)
    price = event.data.prices[1]
    assert price.index_price is None
    assert price.mark_price == 63126084821834306372811
    assert str(from_wei(price.mark_price)) == "63126.084821834306372811"


@pytest.mark.parametrize("raw", ["broken JSON", "[]"])
async def test_malformed_message_fails_explicitly(raw):
    async def handler(socket):
        await socket.recv()
        await socket.send(raw)
        await socket.wait_closed()

    async with server_config(handler) as config:
        async with PublicStream(config, "trades") as stream:
            with pytest.raises(ProtocolError):
                await anext(stream)


async def test_client_shutdown_interrupts_waiting_reader():
    closed = asyncio.Event()

    async def handler(socket):
        await socket.recv()
        await socket.send(json.dumps(ack(channel="trades")))
        await socket.wait_closed()
        closed.set()

    async with server_config(handler) as config:
        client = RiseXClient(config)
        stream = client.stream_trades(market_ids=[1])
        await anext(stream)
        reader = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        await client.aclose()
        with pytest.raises(StopAsyncIteration):
            await reader
        await asyncio.wait_for(closed.wait(), timeout=1)


async def test_reader_cancellation_closes_connection():
    closed = asyncio.Event()

    async def handler(socket):
        await socket.recv()
        await socket.send(json.dumps(ack(channel="trades")))
        await socket.wait_closed()
        closed.set()

    async with server_config(handler) as config:
        async with PublicStream(config, "trades", market_ids=[1]) as stream:
            await anext(stream)
            reader = asyncio.create_task(anext(stream))
            await asyncio.sleep(0)
            reader.cancel()
            with pytest.raises(asyncio.CancelledError):
                await reader
        await asyncio.wait_for(closed.wait(), timeout=1)


async def test_failed_upgrade_preserves_server_error():
    async def handler(_):
        raise AssertionError("Rejected connection must not reach the handler")

    async def reject(connection, _):
        return connection.respond(503, "maintenance")

    async with serve(handler, "127.0.0.1", 0, process_request=reject) as server:
        port = server.sockets[0].getsockname()[1]
        config = RiseXConfig(
            base_url="http://127.0.0.1",
            websocket_url=f"ws://127.0.0.1:{port}",
            max_reconnect_attempts=0,
        )
        async with PublicStream(config, "trades") as stream:
            with pytest.raises(ReconnectExhaustedError) as error:
                async for _ in stream:
                    pass
    assert isinstance(error.value.__cause__, InvalidStatus)
    assert error.value.__cause__.response.status_code == 503
