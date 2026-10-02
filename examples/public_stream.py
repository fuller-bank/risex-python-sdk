"""Consume a bounded number of public events, then close cleanly."""

import argparse
import asyncio
from dataclasses import replace

from risex import (
    ConnectionEvent,
    LocalOrderbook,
    OrderbookEvent,
    RiseXClient,
    RiseXConfig,
)


async def main(mainnet: bool = True, websocket_url: str | None = None) -> None:
    book = LocalOrderbook(1)
    config = RiseXConfig(mainnet=mainnet)
    if websocket_url is not None:
        config = replace(config, websocket_url=websocket_url)
    async with RiseXClient(config) as client:
        async with client.stream_orderbook(market_ids=[1]) as stream:
            async with asyncio.timeout(30):
                count = 0
                async for event in stream:
                    if isinstance(event, (ConnectionEvent, OrderbookEvent)):
                        book.apply(event)
                    if isinstance(event, ConnectionEvent):
                        print(event.state)
                    else:
                        count += 1
                        print(event.type, book.snapshot.bids[:1], book.snapshot.asks[:1])
                        if count == 5:
                            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--websocket-url", help="Explicit public feed URL")
    args = parser.parse_args()
    asyncio.run(main(mainnet=not args.testnet, websocket_url=args.websocket_url))
