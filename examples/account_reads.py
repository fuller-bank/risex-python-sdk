"""Read your RISEx account; mainnet is the default, --testnet selects testnet."""

import argparse
import asyncio
import os

from risex import RiseXClient


async def main(mainnet: bool) -> None:
    account = os.environ["RISEX_ACCOUNT"]
    async with RiseXClient(mainnet=mainnet, account=account) as client:
        snapshot = await client.get_account_snapshot()
        print("Collateral:", snapshot.balances.collateral.balance)
        print("Cross margin:", snapshot.balances.cross_margin.balance)
        print("Positions:", snapshot.positions)
        print("Open orders:", snapshot.open_orders)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--testnet", action="store_true")
    asyncio.run(main(mainnet=not parser.parse_args().testnet))
