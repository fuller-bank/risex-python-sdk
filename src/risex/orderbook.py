"""A complete WebSocket book with provider CRC32 validation."""

from __future__ import annotations

import binascii
from itertools import zip_longest

from .config import validate_market_id
from .exceptions import ChecksumMismatchError, StaleOrderbookError
from .models import ConnectionEvent, OrderbookEvent, OrderbookSnapshot, PriceLevel
from .units import to_wei


def compute_checksum(bids: tuple[PriceLevel, ...], asks: tuple[PriceLevel, ...]) -> int:
    """RISEx CRC32-IEEE: interleaved, sorted levels encoded as wei integers."""
    values: list[str] = []
    for bid, ask in zip_longest(
        sorted(bids, key=lambda level: level.price, reverse=True),
        sorted(asks, key=lambda level: level.price),
    ):
        for level in (bid, ask):
            if level is not None:
                values.extend((str(to_wei(level.price)), str(to_wei(level.quantity))))
    return binascii.crc32(":".join(values).encode("ascii")) & 0xFFFFFFFF


class LocalOrderbook:
    """Consume one market's snapshots/deltas; a reconnect always invalidates state."""

    def __init__(self, market_id: int) -> None:
        self.market_id = validate_market_id(market_id)
        self._bids: dict[str, PriceLevel] = {}
        self._asks: dict[str, PriceLevel] = {}
        self._valid = False

    @property
    def valid(self) -> bool:
        return self._valid

    def reset(self) -> None:
        self._valid = False
        self._bids.clear()
        self._asks.clear()

    @property
    def snapshot(self) -> OrderbookSnapshot:
        if not self._valid:
            raise StaleOrderbookError("A fresh WebSocket snapshot is required")
        return self._snapshot()

    def _snapshot(self) -> OrderbookSnapshot:
        return OrderbookSnapshot(
            market_id=self.market_id,
            bids=tuple(sorted(self._bids.values(), key=lambda level: level.price, reverse=True)),
            asks=tuple(sorted(self._asks.values(), key=lambda level: level.price)),
        )

    def apply(self, event: OrderbookEvent | ConnectionEvent) -> None:
        if isinstance(event, ConnectionEvent):
            self.reset()
            return
        if event.market_id != self.market_id:
            raise ValueError("Event belongs to another market")
        if event.type == "snapshot":
            self.reset()
        elif not self._valid:
            raise StaleOrderbookError("Received update before a fresh snapshot")
        try:
            for levels, target in ((event.data.bids, self._bids), (event.data.asks, self._asks)):
                for level in levels:
                    # Wei is an exact canonical key even if decimal representations differ.
                    key = str(to_wei(level.price))
                    if level.quantity == 0:
                        target.pop(key, None)
                    else:
                        to_wei(level.quantity)
                        target[key] = level
            if event.checksum is not None:
                book = self._snapshot()
                actual = compute_checksum(book.bids, book.asks)
                if actual != event.checksum:
                    raise ChecksumMismatchError(event.checksum, actual)
        except Exception:
            self.reset()
            raise
        self._valid = True
