"""Explicit wallet gates; these tests never select mainnet or acquire funds."""

import asyncio
import math
import os
from decimal import Decimal
from fractions import Fraction

import pytest

from risex import (
    LocalSigner,
    OrderRequest,
    OrdersEvent,
    OrderSide,
    PositionsEvent,
    RiseXClient,
    from_steps,
)

pytestmark = pytest.mark.integration


def required(name):
    value = os.getenv(name)
    if not value:
        pytest.fail(f"Explicitly enabled wallet test requires {name}")
    return value


def wallet_client():
    return RiseXClient(
        mainnet=False,
        account=required("RISEX_TEST_ACCOUNT"),
        signer=LocalSigner(required("RISEX_TEST_SIGNER_PRIVATE_KEY")),
    )


account_gate = pytest.mark.skipif(
    os.getenv("RISEX_RUN_ACCOUNT_INTEGRATION") != "1",
    reason="Set RISEX_RUN_ACCOUNT_INTEGRATION=1 with a registered testnet signer",
)


@account_gate
async def test_account_reads():
    async with wallet_client() as client:
        assert (await client.get_signer_status()).active
        snapshot = await client.get_account_snapshot()
        assert snapshot.balances.account == client.account
        assert (await client.get_position(1)).position.size.is_finite()
        await client.get_order_history(limit=5)
        await client.get_trade_history(limit=5)


@account_gate
@pytest.mark.parametrize(
    ("channel", "event_type"), [("orders", OrdersEvent), ("positions", PositionsEvent)]
)
async def test_private_initial_snapshot(channel, event_type):
    async with wallet_client() as client:
        assert (await client.get_signer_status()).active
        async with client.stream(channel) as stream:
            async with asyncio.timeout(30):
                async for event in stream:
                    if isinstance(event, event_type) and event.type == "snapshot":
                        return
    pytest.fail("Private stream closed before its initial snapshot")


@pytest.mark.skipif(
    os.getenv("RISEX_RUN_TRADE_INTEGRATION") != "1",
    reason="Set RISEX_RUN_TRADE_INTEGRATION=1 to submit a funded testnet order",
)
async def test_bounded_post_only_order_lifecycle():
    market_id = int(os.getenv("RISEX_TEST_MARKET_ID", "1"))
    maximum_notional = Decimal(os.getenv("RISEX_TEST_MAXIMUM_NOTIONAL", "20"))
    if not maximum_notional.is_finite() or maximum_notional <= 0:
        pytest.fail("RISEX_TEST_MAXIMUM_NOTIONAL must be finite and positive")
    async with wallet_client() as client:
        assert (await client.get_signer_status()).active
        market = (await client.get_markets(market_ids=[market_id])).markets[0]
        assert market.mark_price is not None and market.mark_price > 0
        price = from_steps(
            math.floor(
                Fraction(market.mark_price) * Fraction(85, 100) / Fraction(market.config.step_price)
            ),
            market.config.step_price,
        )
        quantity = from_steps(
            max(
                1,
                math.ceil(
                    Fraction(market.config.min_order_size) / Fraction(market.config.step_size)
                ),
            ),
            market.config.step_size,
        )
        assert Fraction(price) * Fraction(quantity) <= Fraction(maximum_notional), (
            "Minimum order exceeds the explicit test notional bound"
        )
        # Unknown placement has no trustworthy order ID: propagate it without a second POST.
        submission = await client.place_order(
            OrderRequest(
                market_id=market_id,
                side=OrderSide.BUY,
                quantity=quantity,
                price=price,
                post_only=True,
            )
        )
        current = None
        try:
            current = await client.get_order(submission.order_id, market_id=market_id)
            assert current.sender == client.account
            assert current.filled_size == 0, "Unexpected fill: reconcile testnet exposure"
        finally:
            if current is None or not current.terminal:
                # One cancellation attempt; an uncertain cancellation propagates for recovery.
                assert (await client.cancel_order(submission.order_id, market_id=market_id)).success
        terminal = await client.wait_for_order(submission.order_id)
        assert terminal.status == "ORDER_STATUS_CANCELLED"
        assert terminal.filled_size == 0, "Unexpected fill: reconcile testnet exposure"
