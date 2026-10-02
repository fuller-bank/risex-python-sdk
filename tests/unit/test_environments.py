import pytest

from risex import RiseXClient, RiseXConfig


async def test_mainnet_is_the_default():
    assert RiseXConfig().mainnet is True
    assert RiseXConfig().base_url == "https://api.rise.trade"
    async with RiseXClient() as client:
        assert client.config.mainnet is True
        assert client.config.websocket_url == "wss://ws.rise.trade/ws"


async def test_boolean_selects_testnet_for_both_transports():
    config = RiseXConfig(mainnet=False)
    assert config == RiseXConfig.testnet()
    assert config.base_url == "https://api.testnet.rise.trade"
    assert config.websocket_url == "wss://api.testnet.rise.trade/ws/"
    async with RiseXClient(mainnet=False) as client:
        assert client.config == config


@pytest.mark.parametrize("value", ["False", 0, 1, None])
def test_config_does_not_coerce_environment_booleans(value):
    with pytest.raises(TypeError):
        RiseXConfig(mainnet=value)


def test_explicit_config_and_boolean_cannot_disagree():
    with pytest.raises(ValueError, match="conflicts"):
        RiseXClient(RiseXConfig.testnet(), mainnet=True)
