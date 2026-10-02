"""Per-client nonce allocation, with no reuse after rejection or uncertain submission."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .exceptions import NonceExhaustedError
from .models import NonceState
from .signing import uint


@dataclass(frozen=True, slots=True)
class Nonce:
    anchor: int
    bitmap_index: int

    def __post_init__(self) -> None:
        uint(self.anchor, 48, "anchor")
        if type(self.bitmap_index) is not int or not 0 <= self.bitmap_index < 208:
            raise ValueError("bitmap_index must be between 0 and 207")


class NonceManager:
    """Use an exclusive client per account, or provide external coordination.

    A fresh client starts at chain anchor + 1. Locally reserved pairs are never
    reused. Rollover requires the server to have observed the preceding anchor.
    """

    def __init__(self, fetch_state: Callable[[], Awaitable[NonceState]]) -> None:
        self._fetch_state = fetch_state
        self._lock = asyncio.Lock()
        self.operation_lock = asyncio.Lock()
        self._anchor: int | None = None
        self._next_index = 0

    async def reserve(self) -> Nonce:
        async with self._lock:
            if self._anchor is None or self._next_index >= 208:
                state = await self._fetch_state()
                if self._anchor is not None and state.nonce_anchor < self._anchor:
                    raise NonceExhaustedError(
                        "All 208 slots are reserved; wait for authoritative anchor advancement"
                    )
                anchor = state.nonce_anchor + 1
                if anchor >= 2**48:
                    raise NonceExhaustedError("uint48 nonce anchor space exhausted")
                self._anchor = anchor
                self._next_index = 0
            nonce = Nonce(self._anchor, self._next_index)
            self._next_index += 1
            return nonce
