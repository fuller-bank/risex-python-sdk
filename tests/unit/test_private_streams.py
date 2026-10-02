import json
from contextlib import asynccontextmanager

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data
from websockets.asyncio.server import serve

from risex import (
    AuthenticationError,
    FillsEvent,
    OrdersEvent,
    PositionsEvent,
    ProtocolError,
    RiseXClient,
    RiseXConfig,
)
from risex.signing import typed_data


@asynccontextmanager
async def private_client(handler, backend, signer):
    async with serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        config = RiseXConfig(
            mainnet=False, websocket_url=f"ws://127.0.0.1:{port}", reconnect_delay=0
        )
        async with RiseXClient(
            config, account=backend.account, signer=signer, transport=httpx.MockTransport(backend)
        ) as client:
            yield client


async def accept_auth(socket, backend, signer):
    frame = json.loads(await socket.recv())
    assert frame["method"] == "auth_v2"
    params = frame["params"]
    domain = {
        "name": "RISEx",
        "version": "1",
        "chainId": 4153,
        "verifyingContract": backend.domain["verifying_contract"],
    }
    data = typed_data(
        domain,
        "RegisterV2",
        {"signer": signer.address, "message": params["message"], "nonce": int(params["nonce"], 16)},
    )
    assert (
        Account.recover_message(encode_typed_data(full_message=data), signature=params["signature"])
        == signer.address
    )
    await socket.send(
        json.dumps(
            {
                "method": "auth_v2",
                "status": "success",
                "data": {"account": backend.account, "signer": signer.address},
            }
        )
    )
    return params


def ack(channel):
    return {
        "method": "subscribe",
        "type": "subscribed",
        "status": "success",
        "channel": channel,
        "data": {"market_ids": [1]},
    }


async def test_reconnect_signs_fresh_one_time_auth_before_resubscribing(backend, signer):
    nonces = []
    order_id = "0x" + "00" * 23 + "02"
    row = backend.order(order_id)

    async def handler(socket):
        params = await accept_auth(socket, backend, signer)
        nonces.append(params["nonce"])
        request = json.loads(await socket.recv())
        assert request["params"] == {"channel": "orders", "market_ids": [1]}
        await socket.send(
            json.dumps(
                {
                    "channel": "orders",
                    "type": "snapshot",
                    "data": [row],
                    "worker_timestamp": "1786935049123456789",
                }
            )
        )
        await socket.send(json.dumps(ack("orders")))
        if len(nonces) == 1:
            await socket.close()
        else:
            await socket.wait_closed()

    async with private_client(handler, backend, signer) as client:
        async with client.stream_orders(market_ids=[1]) as stream:
            events = [await anext(stream) for _ in range(6)]
    assert isinstance(events[1], OrdersEvent) and isinstance(events[5], OrdersEvent)
    assert events[4].stale
    assert len(nonces) == len(set(nonces)) == 2
    assert backend.nonce_counter == 2
    assert not backend.posts


@pytest.mark.parametrize("wrong_identity", [False, True])
async def test_rejected_or_mismatched_authentication_fails_terminally(
    backend, signer, wrong_identity
):
    connections = 0

    async def handler(socket):
        nonlocal connections
        connections += 1
        await socket.recv()
        response = (
            {
                "method": "auth_v2",
                "status": "success",
                "data": {"account": "0x" + "33" * 20, "signer": signer.address},
            }
            if wrong_identity
            else {"method": "auth_v2", "status": "error", "message": "session key inactive"}
        )
        await socket.send(json.dumps(response))
        await socket.wait_closed()

    async with private_client(handler, backend, signer) as client:
        async with client.stream_positions(market_ids=[1]) as stream:
            with pytest.raises(AuthenticationError):
                await anext(stream)
    assert connections == 1


async def test_account_data_for_another_owner_is_rejected(backend, signer):
    row = backend.order("0x" + "00" * 23 + "02")
    row["sender"] = "0x" + "33" * 20

    async def handler(socket):
        await accept_auth(socket, backend, signer)
        await socket.recv()
        await socket.send(json.dumps(ack("orders")))
        await socket.send(
            json.dumps(
                {"channel": "orders", "type": "snapshot", "data": [row], "worker_timestamp": "1"}
            )
        )
        await socket.wait_closed()

    async with private_client(handler, backend, signer) as client:
        async with client.stream_orders(market_ids=[1]) as stream:
            await anext(stream)
            with pytest.raises(ProtocolError, match="different account"):
                await anext(stream)


async def test_fill_payload_keeps_exact_partial_size_and_optional_pnl(backend, signer):
    async def handler(socket):
        await accept_auth(socket, backend, signer)
        await socket.recv()
        await socket.send(json.dumps(ack("fills")))
        await socket.send(
            json.dumps(
                {
                    "channel": "fills",
                    "type": "update",
                    "market_id": "1",
                    "worker_timestamp": "1786935049200112233",
                    "data": {
                        "id": "fill-1",
                        "market_id": "1",
                        "order_id": "0x" + "00" * 23 + "02",
                        "side": "BUY",
                        "price": "63232.6",
                        "size": "0.000273",
                        "fee": "0.005178",
                        "liquidity_indicator": "TAKER",
                        "time": "1786935048000000000",
                    },
                }
            )
        )
        await socket.wait_closed()

    async with private_client(handler, backend, signer) as client:
        async with client.stream_fills(market_ids=[1]) as stream:
            await anext(stream)
            event = await anext(stream)
    assert isinstance(event, FillsEvent)
    assert str(event.data.size) == "0.000273"
    assert event.data.realized_pnl is None


async def test_closed_position_retains_side_but_zero_size_is_authoritative(backend, signer):
    row = {
        "account": backend.account,
        "market_id": "1",
        "size": "0",
        "quote_amount": "0",
        "side": "SELL",
        "margin_mode": 0,
        "isolated_usdc_balance": "0",
        "last_funding_payment": "-1.2",
        "leverage": "25",
        "avg_entry_price": "0",
    }

    async def handler(socket):
        await accept_auth(socket, backend, signer)
        await socket.recv()
        await socket.send(json.dumps(ack("positions")))
        await socket.send(
            json.dumps(
                {
                    "channel": "positions",
                    "type": "update",
                    "market_id": "1",
                    "data": [row],
                    "worker_timestamp": "1",
                }
            )
        )
        await socket.wait_closed()

    async with private_client(handler, backend, signer) as client:
        async with client.stream_positions(market_ids=[1]) as stream:
            await anext(stream)
            event = await anext(stream)
    assert isinstance(event, PositionsEvent)
    assert event.data[0].side == "SELL" and event.data[0].size == 0
