"""Explicitly register the supplied session signer after funding/deposit setup."""

import argparse
import asyncio
import os
import time

from risex import LocalSigner, RiseXClient


async def main(mainnet: bool, lifetime_seconds: int) -> None:
    account_signer = LocalSigner(os.environ["RISEX_ACCOUNT_PRIVATE_KEY"])
    signer = LocalSigner(os.environ["RISEX_SIGNER_PRIVATE_KEY"])
    async with RiseXClient(
        mainnet=mainnet, account=account_signer.address, signer=signer
    ) as client:
        result = await client.register_signer(
            account_signer, expiration=int(time.time()) + lifetime_seconds
        )
        print("Registration:", result.success, result.transaction_hash)
        print("Signer status:", (await client.get_signer_status()).status_description)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--lifetime-seconds", type=int, default=86400)
    args = parser.parse_args()
    if not args.execute:
        parser.error("Pass --execute to submit the signer registration")
    asyncio.run(main(mainnet=not args.testnet, lifetime_seconds=args.lifetime_seconds))
