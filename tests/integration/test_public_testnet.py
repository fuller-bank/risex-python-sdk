import asyncio
import os
from dataclasses import replace

import pytest

from risex import (
    ConnectionEvent,
    LocalOrderbook,
    OracleEvent,
    OrderbookEvent,
    RiseXClient,
    RiseXConfig,
    TradeEvent,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RISEX_RUN_INTEGRATION") != "1",
        reason="Set RISEX_RUN_INTEGRATION=1 to use the public testnet",
    ),
]


async def test_public_rest():
    async with RiseXClient(RiseXConfig.testnet()) as client:
        response = await client.get_markets(market_ids=[1])
        assert response.markets
        assert response.markets[0].market_id == 1
        book = await client.get_orderbook(1, limit=5)
        assert book.market_id == 1
        assert len(book.bids) <= 5 and len(book.asks) <= 5


async def test_orderbook_snapshot_and_checksum_update():
    book = LocalOrderbook(1)
    async with RiseXClient(websocket_config()) as client:
        async with client.stream_orderbook(market_ids=[1]) as stream:
            async with asyncio.timeout(30):
                async for event in stream:
                    book.apply(event)
                    if isinstance(event, OrderbookEvent) and event.type == "update":
                        assert book.valid
                        return


def websocket_config():
    config = RiseXConfig.testnet()
    override = os.getenv("RISEX_INTEGRATION_WS_URL")
    return replace(config, websocket_url=override) if override else config


async def test_trade_and_oracle_updates():
    async def first_update(channel, expected_type):
        async with RiseXClient(websocket_config()) as client:
            # Testnet BTC may be idle; matched trades have no initial snapshot.
            ids = [] if channel == "trades" else [1]
            async with client.stream(channel, market_ids=ids) as stream:
                async with asyncio.timeout(30):
                    async for event in stream:
                        if isinstance(event, ConnectionEvent):
                            continue
                        assert isinstance(event, expected_type)
                        assert event.worker_timestamp > 0
                        return

    await asyncio.gather(
        first_update("trades", TradeEvent),
        first_update("oracle", OracleEvent),
    )


@pytest.mark.parametrize("mainnet", [True, False])
async def test_runtime_signing_metadata(mainnet):
    async with RiseXClient(mainnet=mainnet) as client:
        metadata = await client.initialize()
        assert metadata.domain.chain_id == metadata.system.chain.chain_id
        assert metadata.domain.verifying_contract == metadata.system.addresses.auth
        assert int(metadata.system.addresses.router, 16) != 0
