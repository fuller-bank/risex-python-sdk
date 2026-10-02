import json
from decimal import Decimal
from pathlib import Path

import pytest

from risex import LocalSigner, OrderRequest, OrderSide, RiseXConfig

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def markets_payload():
    return json.loads((FIXTURES / "markets.json").read_text())


@pytest.fixture
def book_payload():
    return json.loads((FIXTURES / "orderbook.json").read_text())


@pytest.fixture
def snapshot_message():
    return json.loads((FIXTURES / "ws_orderbook_snapshot.json").read_text())


@pytest.fixture
def config():
    return RiseXConfig.testnet(
        retry_delay=0,
        reconnect_delay=0,
        rest_requests_per_second=None,
        websocket_requests_per_second=None,
    )


@pytest.fixture
def account_signer():
    # Public deterministic test vector keys; never used for network writes.
    return LocalSigner("0x" + "11" * 32)


@pytest.fixture
def signer():
    return LocalSigner("0x" + "22" * 32)


@pytest.fixture
def backend(account_signer, signer, markets_payload):
    from fixtures.backend import Backend

    return Backend(account_signer.address, signer.address, markets_payload["data"]["markets"][0])


@pytest.fixture
def order_request():
    return OrderRequest(
        market_id=1, side=OrderSide.BUY, quantity=Decimal("0.001"), price=Decimal("60000")
    )
