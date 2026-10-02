"""Public reads, using mainnet by default; pass --testnet to select testnet."""

import argparse
import asyncio

from risex import RiseXClient


async def main(mainnet: bool = True) -> None:
    async with RiseXClient(mainnet=mainnet) as client:
        response = await client.get_markets(market_ids=[1])
        for market in response.markets:
            print(market.market_id, market.config.name, market.mark_price)
            book = await client.get_orderbook(market.market_id, limit=5)
            print("Bids:", [(level.price, level.quantity) for level in book.bids])
            print("Asks:", [(level.price, level.quantity) for level in book.asks])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--testnet", action="store_true")
    asyncio.run(main(mainnet=not parser.parse_args().testnet))
