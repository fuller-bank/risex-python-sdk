import asyncio

import pytest

from risex import NonceExhaustedError, NonceManager, NonceState


async def test_concurrent_reservations_never_reuse_a_pair():
    calls = 0

    async def state():
        nonlocal calls
        calls += 1
        return NonceState(nonce_anchor=5, current_bitmap_index=8, bitmap="0xff")

    manager = NonceManager(state)
    nonces = await asyncio.gather(*(manager.reserve() for _ in range(208)))
    assert calls == 1
    assert {nonce.anchor for nonce in nonces} == {6}
    assert {nonce.bitmap_index for nonce in nonces} == set(range(208))
    with pytest.raises(NonceExhaustedError):
        await manager.reserve()
    assert calls == 2


async def test_rollover_requires_authoritative_anchor_advancement():
    anchor = 0

    async def state():
        return NonceState(nonce_anchor=anchor, current_bitmap_index=0, bitmap="0x0")

    manager = NonceManager(state)
    for _ in range(208):
        await manager.reserve()
    with pytest.raises(NonceExhaustedError):
        await manager.reserve()
    anchor = 1
    next_nonce = await manager.reserve()
    assert (next_nonce.anchor, next_nonce.bitmap_index) == (2, 0)


async def test_anchor_cannot_overflow():
    async def state():
        return NonceState(nonce_anchor=2**48 - 1, current_bitmap_index=208, bitmap="0x0")

    with pytest.raises(NonceExhaustedError):
        await NonceManager(state).reserve()


async def test_failed_state_read_does_not_allocate():
    calls = 0

    async def state():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("unavailable")
        return NonceState(nonce_anchor=0, current_bitmap_index=0, bitmap="0")

    manager = NonceManager(state)
    with pytest.raises(OSError):
        await manager.reserve()
    assert (await manager.reserve()).bitmap_index == 0
