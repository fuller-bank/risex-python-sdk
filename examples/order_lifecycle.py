"""Explicit bounded limit placement/cancellation; no automatic flattening or resubmission."""

import argparse
import asyncio
import math
import os
from decimal import Decimal
from fractions import Fraction

from risex import (
    LocalSigner,
    OrderRequest,
    OrderSide,
    RiseXClient,
    UnknownOutcomeError,
    from_steps,
)


async def main(mainnet: bool, market_id: int, maximum_notional: Decimal) -> None:
    if not maximum_notional.is_finite() or maximum_notional <= 0:
        raise ValueError("maximum_notional must be finite and positive")
    signer = LocalSigner(os.environ["RISEX_SIGNER_PRIVATE_KEY"])
    async with RiseXClient(
        mainnet=mainnet, account=os.environ["RISEX_ACCOUNT"], signer=signer
    ) as client:
        if not (await client.get_signer_status()).active:
            raise RuntimeError("Register an active signer before placing an order")
        market = (await client.get_markets(market_ids=[market_id])).markets[0]
        if market.mark_price is None or market.mark_price <= 0:
            raise RuntimeError("No mark price available")
        # This example chooses an explicit rounding policy for its own limit price.
        target_ticks = (
            Fraction(market.mark_price) * Fraction(85, 100) / Fraction(market.config.step_price)
        )
        price = from_steps(math.floor(target_ticks), market.config.step_price)
        size_steps = max(
            1, math.ceil(Fraction(market.config.min_order_size) / Fraction(market.config.step_size))
        )
        quantity = from_steps(size_steps, market.config.step_size)
        if Fraction(price) * Fraction(quantity) > Fraction(maximum_notional):
            raise ValueError("Minimum order exceeds this example's maximum notional")
        request = OrderRequest(
            market_id=market_id,
            side=OrderSide.BUY,
            quantity=quantity,
            price=price,
            post_only=True,
        )
        try:
            result = await client.place_order(request)
        except UnknownOutcomeError as error:
            resolution = await client.reconcile_submission(error)
            print("Uncertain placement:", error.context)
            print("Authoritative lookup:", resolution)
            raise
        print("Submitted order:", result.order_id)
        # Cancellation also has an explicit uncertainty path; never retry blindly.
        try:
            cancelled = await client.cancel_order(result.order_id, market_id=market_id)
        except UnknownOutcomeError as error:
            print("Uncertain cancellation:", error.context)
            print("Authoritative lookup:", await client.reconcile_submission(error))
            raise
        print("Cancellation receipt:", cancelled.success)
        print("Final order:", await client.wait_for_order(result.order_id))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--testnet", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--market-id", type=int, default=1)
    parser.add_argument("--maximum-notional", type=Decimal, default=Decimal("20"))
    args = parser.parse_args()
    if not args.execute:
        parser.error("Pass --execute to submit a real order and cancellation")
    asyncio.run(
        main(
            mainnet=not args.testnet,
            market_id=args.market_id,
            maximum_notional=args.maximum_notional,
        )
    )
