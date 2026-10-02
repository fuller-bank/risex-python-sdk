from decimal import Decimal

import pytest

from risex import (
    ChecksumMismatchError,
    ConnectionEvent,
    LocalOrderbook,
    OrderbookEvent,
    PriceLevel,
    StaleOrderbookError,
    compute_checksum,
)


def level(price, quantity):
    return PriceLevel(price=Decimal(price), quantity=Decimal(quantity), order_count=1)


def update(snapshot_message, *, checksum):
    return OrderbookEvent.model_validate(
        {
            **snapshot_message,
            "type": "update",
            "data": {
                "market_id": 1,
                "bids": [{"price": "63218.50", "quantity": "0", "order_count": 0}],
                "asks": [{"price": "63220", "quantity": "2", "order_count": 1}],
            },
            "checksum": checksum,
        }
    )


def test_checksum_uses_wei_interleaving_and_all_levels():
    bids = (level("2", "3"), level("1", "4"))
    asks = (level("5", "6"),)
    # Independent known vector: 2e18:3e18:5e18:6e18:1e18:4e18.
    assert compute_checksum(bids, asks) == 3008464340
    assert compute_checksum(tuple(reversed(bids)), asks) == 3008464340


def test_snapshot_update_and_deletion(snapshot_message):
    book = LocalOrderbook(1)
    book.apply(OrderbookEvent.model_validate(snapshot_message))
    expected_bids = (level("63215.2", "0.004744"),)
    expected_asks = (level("63220", "2"), level("63222.6", "0.004744"))
    book.apply(update(snapshot_message, checksum=compute_checksum(expected_bids, expected_asks)))
    assert book.valid
    assert len(book.snapshot.bids) == 1
    assert book.snapshot.bids[0].price == Decimal("63215.2")
    assert book.snapshot.asks[0].quantity == 2


def test_checksum_failure_invalidates_book(snapshot_message):
    book = LocalOrderbook(1)
    book.apply(OrderbookEvent.model_validate(snapshot_message))
    with pytest.raises(ChecksumMismatchError):
        book.apply(update(snapshot_message, checksum=0))
    assert not book.valid
    with pytest.raises(StaleOrderbookError):
        _ = book.snapshot
    with pytest.raises(StaleOrderbookError):
        book.apply(update(snapshot_message, checksum=0))
    book.apply(OrderbookEvent.model_validate(snapshot_message))
    assert book.valid


def test_disconnect_requires_fresh_snapshot(snapshot_message):
    book = LocalOrderbook(1)
    book.apply(OrderbookEvent.model_validate(snapshot_message))
    book.apply(ConnectionEvent(state="disconnected", stale=True))
    with pytest.raises(StaleOrderbookError):
        book.apply(update(snapshot_message, checksum=0))


def test_update_before_snapshot_is_rejected(snapshot_message):
    with pytest.raises(StaleOrderbookError):
        LocalOrderbook(1).apply(update(snapshot_message, checksum=0))
