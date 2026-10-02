from decimal import Decimal

import httpx
import pytest

from risex import (
    AuthenticationError,
    OrderSubmission,
    OrderWaitTimeoutError,
    ProtocolError,
    RiseXClient,
    UnknownOutcomeError,
)
from risex.exceptions import MutationContext


def client_for(config, backend, signer=None):
    return RiseXClient(
        config, account=backend.account, signer=signer, transport=httpx.MockTransport(backend)
    )


def position(account, market_id=1):
    return {
        "account": account,
        "market_id": str(market_id),
        "size": "-0.020463",
        "quote_amount": "1456.615970721655103383",
        "side": "SELL",
        "margin_mode": 0,
        "isolated_usdc_balance": "0",
        "last_funding_payment": "-1343.280402365544640442",
        "unsettled_funding": "3.82702329635071426",
        "leverage": "25",
        "avg_entry_price": "66819.6",
        "block_number": "0",
        "log_index": "0",
        "worker_timestamp": "0",
    }


async def test_balances_and_flat_position_keep_exact_units(config, backend):
    async with client_for(config, backend) as client:
        balances = await client.get_balances()
        result = await client.get_position(1)
    assert balances.collateral.balance == Decimal("12.696037480899793696")
    assert balances.cross_margin.balance == Decimal("-0.100000000000000001")
    assert result.position.size == 0
    assert result.position.market_id == 0
    assert result.position.avg_entry_price is None
    assert result.position.last_funding_payment == Decimal("-95.786475745565619854")
    assert not backend.posts


async def test_account_snapshot_reads_all_position_and_order_pages(config, backend, signer):
    backend.positions = [position(backend.account, market_id) for market_id in (1, 2, 3)]
    for sequence in (2, 4, 6):
        order_id = "0x" + sequence.to_bytes(8, "big").hex() + "00" * 16
        backend.orders[order_id] = backend.order(order_id)
    async with client_for(config, backend, signer) as client:
        positions = [row async for row in client.iter_positions(page_size=1)]
        orders = [row async for row in client.iter_open_orders(page_size=1)]
        history = [row async for row in client.iter_order_history(page_size=1)]
        snapshot = await client.get_account_snapshot()
    assert len(positions) == len(orders) == len(history) == 3
    assert positions[0].size == Decimal("-0.020463")
    assert len(snapshot.positions) == len(snapshot.open_orders) == 3
    assert not backend.posts


async def test_trade_history_pagination_preserves_partial_fills(config, backend):
    order_id = "0x" + "00" * 23 + "02"
    backend.fills = [
        {
            "id": f"{order_id}-{index}",
            "market_id": "1",
            "order_id": order_id,
            "side": "BUY",
            "price": "60000",
            "size": "0.0001",
            "fee": "0.000001",
            "time": str(index + 1),
            "liquidity_indicator": "TAKER",
            "realized_pnl": "",
            "avg_price": "",
            "leverage": "",
            "client_order_id": "",
        }
        for index in range(3)
    ]
    async with client_for(config, backend) as client:
        fills = [row async for row in client.iter_trade_history(page_size=1)]
    assert len(fills) == 3
    assert fills[0].size == Decimal("0.0001")
    assert fills[0].realized_pnl is None and fills[0].client_order_id is None


async def test_incorrect_account_scope_is_never_treated_as_own_positions(config, backend):
    backend.positions = [position("0x" + "33" * 20)]
    async with client_for(config, backend) as client:
        with pytest.raises(ProtocolError, match="another account"):
            await client.get_positions()


async def test_pagination_guard_reports_incomplete_results(config, backend):
    backend.positions = [position(backend.account, 1), position(backend.account, 2)]
    async with client_for(config, backend) as client:
        with pytest.raises(ProtocolError, match="max_pages"):
            _ = [row async for row in client.iter_positions(page_size=1, max_pages=1)]


async def test_nonadvancing_page_is_rejected(config, backend):
    backend.positions = [position(backend.account, 1), position(backend.account, 2)]
    backend.transform = lambda path, data: {**data, "page": 1} if path == "/v1/positions" else data
    async with client_for(config, backend) as client:
        with pytest.raises(ProtocolError, match="advance"):
            _ = [row async for row in client.iter_positions(page_size=1)]


async def test_unknown_order_absence_is_not_reported_as_failure(config, backend, signer):
    error = UnknownOutcomeError(
        MutationContext("place_order", backend.account, 1, 0, market_id=1, client_order_id=123)
    )
    async with client_for(config, backend, signer) as client:
        result = await client.reconcile_submission(error)
    assert not result.resolved and result.nonce_consumed is False
    assert not backend.posts


async def test_order_wait_timeout_does_not_submit_a_cancellation(config, backend):
    order_id = "0x" + "00" * 23 + "02"
    backend.orders[order_id] = backend.order(order_id)
    async with client_for(config, backend) as client:
        with pytest.raises(OrderWaitTimeoutError):
            await client.wait_for_order(order_id, timeout=0.02, poll_interval=0.001)
    assert not backend.posts


def test_ioc_fill_quantity_is_wei_and_acknowledgement_is_not_an_order_status():
    result = OrderSubmission.model_validate(
        {
            "order_id": "0x" + "00" * 23 + "02",
            "tx_hash": "0x" + "ab" * 32,
            "block_number": "500",
            "sc_order_id": "2",
            "filled_quantity": "500000000000000",
            "filled_percent": "50.00",
            "message": "Order partially filled",
        }
    )
    assert result.filled_quantity == Decimal("0.0005")
    assert result.filled_quantity_wei == 500000000000000
    assert result.filled_percent == Decimal("50")
    assert not hasattr(result, "status")


async def test_private_apis_require_credentials_without_connecting(config):
    async with RiseXClient(config) as client:
        with pytest.raises(AuthenticationError):
            client.stream_positions()
        with pytest.raises(AuthenticationError):
            await client.get_balances()
