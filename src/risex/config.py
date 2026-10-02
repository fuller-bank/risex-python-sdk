"""Explicit connection settings; imports never read credentials or make requests."""

from __future__ import annotations

import math
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class RiseXConfig:
    mainnet: bool = True
    base_url: str = ""
    websocket_url: str = ""
    request_timeout: float = 10.0
    max_read_retries: int = 2
    retry_delay: float = 0.25
    max_retry_delay: float = 5.0
    subscription_timeout: float = 10.0
    max_reconnect_attempts: int = 3
    reconnect_delay: float = 0.5
    max_reconnect_delay: float = 5.0
    websocket_max_queue: int = 32
    websocket_max_size: int = 8 * 1024 * 1024
    max_pending_messages: int = 128
    authentication_timeout: float = 20.0
    permit_ttl_seconds: int = 120
    rest_requests_per_second: float | None = 40.0
    websocket_requests_per_second: float | None = 8.0

    def __post_init__(self) -> None:
        if type(self.mainnet) is not bool:
            raise TypeError("mainnet must be bool; use False for testnet")
        if not self.base_url:
            object.__setattr__(
                self,
                "base_url",
                "https://api.rise.trade" if self.mainnet else "https://api.testnet.rise.trade",
            )
        if not self.websocket_url:
            object.__setattr__(
                self,
                "websocket_url",
                "wss://ws.rise.trade/ws" if self.mainnet else "wss://api.testnet.rise.trade/ws/",
            )
        for name, schemes in (
            ("base_url", {"http", "https"}),
            ("websocket_url", {"ws", "wss"}),
        ):
            url = urlsplit(getattr(self, name))
            if (
                url.scheme not in schemes
                or not url.hostname
                or url.username is not None
                or url.password is not None
                or url.query
                or url.fragment
            ):
                raise ValueError(f"{name} must be a URL without credentials, query or fragment")
        for name in (
            "request_timeout",
            "subscription_timeout",
            "max_retry_delay",
            "max_reconnect_delay",
            "authentication_timeout",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        for name in ("retry_delay", "reconnect_delay"):
            value = getattr(self, name)
            if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        for name in ("max_read_retries", "max_reconnect_attempts"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        for name in ("websocket_max_queue", "websocket_max_size", "max_pending_messages"):
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.permit_ttl_seconds) is not int or not 0 < self.permit_ttl_seconds < 2**32:
            raise ValueError("permit_ttl_seconds must be a positive uint32 integer")
        for name in ("rest_requests_per_second", "websocket_requests_per_second"):
            rate = getattr(self, name)
            if rate is not None and (
                isinstance(rate, bool) or not math.isfinite(rate) or rate <= 0
            ):
                raise ValueError(f"{name} must be finite and positive, or None")

    @classmethod
    def testnet(cls, **kwargs: float | int | None) -> RiseXConfig:
        return cls(
            mainnet=False,
            **kwargs,  # type: ignore[arg-type]
        )


def validate_market_id(market_id: int) -> int:
    if type(market_id) is not int or not 0 < market_id < 2**64:
        raise ValueError("market_id must be a positive uint64 integer")
    return market_id


def validate_market_ids(market_ids: tuple[int, ...]) -> tuple[int, ...]:
    for market_id in market_ids:
        validate_market_id(market_id)
    if len(set(market_ids)) != len(market_ids):
        raise ValueError("market_ids must not contain duplicates")
    return market_ids
