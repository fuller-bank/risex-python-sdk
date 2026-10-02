"""Observe fills and reconcile REST views after reconnect; mainnet by default."""

import argparse
import asyncio
import os

from risex import ConnectionEvent, FillsEvent, LocalSigner, RiseXClient


async def main(mainnet: bool) -> None:
    signer = LocalSigner(os.environ["RISEX_SIGNER_PRIVATE_KEY"])
    last_fill_ns: int | None = None
    observed: set[str] = set()
    async with RiseXClient(
        mainnet=mainnet, account=os.environ["RISEX_ACCOUNT"], signer=signer
    ) as client:
        async with client.stream_fills() as stream:
            async for event in stream:
                if isinstance(event, ConnectionEvent):
                    print(event.state, "stale:", event.stale)
                    if event.state == "connected":
                        snapshot = await client.get_account_snapshot()
                        print(
                            "Refreshed positions/open orders:",
                            snapshot.positions,
                            snapshot.open_orders,
                        )
                        if last_fill_ns is not None:
                            async for fill in client.iter_trade_history(start_time=last_fill_ns):
                                if fill.id not in observed:
                                    observed.add(fill.id)
                                    print("Recovered fill:", fill)
                                last_fill_ns = max(last_fill_ns, fill.time)
                    continue
                if isinstance(event, FillsEvent):
                    fill = event.data
                    if fill.id not in observed:
                        observed.add(fill.id)
                        print("Live fill:", fill)
                    last_fill_ns = max(last_fill_ns or 0, fill.time)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--testnet", action="store_true")
    asyncio.run(main(mainnet=not parser.parse_args().testnet))
