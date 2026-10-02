"""Public and authenticated streams with explicit stale-state and bounded recovery."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Awaitable, Callable, Sequence
from contextlib import suppress
from typing import Any, Self

from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus

from .config import RiseXConfig, validate_market_ids
from .exceptions import (
    AuthenticationError,
    ProtocolError,
    ReconnectExhaustedError,
    SubscriptionError,
    TransportError,
)
from .models import (
    Channel,
    ConnectionEvent,
    DataEvent,
    FillsEvent,
    OracleEvent,
    Order,
    OrderbookEvent,
    OrdersEvent,
    PositionsEvent,
    StreamEvent,
    TradeEvent,
)
from .rate_limit import RateLimiter
from .signing import address


def _message(raw: str | bytes) -> dict[str, Any]:
    try:
        message = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ProtocolError("WebSocket message is not valid JSON") from exc
    if not isinstance(message, dict):
        raise ProtocolError("WebSocket message must be an object")
    return message


def _data_event(
    message: dict[str, Any],
    channel: Channel,
    market_ids: tuple[int, ...],
    account: str | None = None,
) -> DataEvent:
    if message.get("channel") != channel:
        raise ProtocolError("Received data for an unexpected channel")
    try:
        event: DataEvent
        if channel == "orderbook":
            event = OrderbookEvent.model_validate(message)
        elif channel == "trades":
            event = TradeEvent.model_validate(message)
        elif channel == "oracle":
            event = OracleEvent.model_validate(message)
        elif channel == "orders":
            event = OrdersEvent.model_validate(message)
        elif channel == "positions":
            event = PositionsEvent.model_validate(message)
        else:
            event = FillsEvent.model_validate(message)
    except ValidationError as exc:
        raise ProtocolError(f"Invalid {channel} WebSocket payload") from exc
    if isinstance(event, (OrdersEvent, PositionsEvent)):
        received_ids = {row.market_id for row in event.data}
        if account is None:
            raise ProtocolError("Private data received without an authenticated account")
        for row in event.data:
            row_account = row.sender if isinstance(row, Order) else row.account
            if address(row_account) != address(account):
                raise ProtocolError("Private stream returned data for a different account")
            if event.market_id is not None and row.market_id != event.market_id:
                raise ProtocolError("Private stream envelope and payload market IDs disagree")
    elif isinstance(event, OracleEvent):
        received_ids = set(event.data.prices)
    else:
        received_ids = {event.market_id}
        if isinstance(event, FillsEvent) and event.market_id != event.data.market_id:
            raise ProtocolError("Fill envelope and payload market IDs disagree")
    if market_ids:
        if not received_ids.issubset(market_ids):
            raise ProtocolError("Received data outside the requested market filter")
    return event


class RiseXStream:
    """An async iterator/context manager. Each stream owns a separate socket.

    Consume connection events as well as data: disconnected/reconnecting means
    old state is stale. Data-only channels cannot replay missed events.
    """

    def __init__(
        self,
        config: RiseXConfig,
        channel: Channel,
        *,
        market_ids: Sequence[int] = (),
        on_close: Callable[[RiseXStream], None] | None = None,
        authenticator: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        account: str | None = None,
        request_limiter: RateLimiter | None = None,
    ) -> None:
        if channel not in ("orderbook", "trades", "oracle", "orders", "positions", "fills"):
            raise ValueError("Unsupported channel")
        if channel in ("orders", "positions", "fills") and (
            authenticator is None or account is None
        ):
            raise AuthenticationError("Private streams require an account and authenticator")
        self.config = config
        self.channel = channel
        self.market_ids = validate_market_ids(tuple(market_ids))
        self._on_close = on_close
        self._authenticator = authenticator
        self._account = address(account) if account is not None else None
        self._request_limiter = request_limiter or RateLimiter(config.websocket_requests_per_second)
        self._closed = False
        self._socket: ClientConnection | None = None
        self._next_task: asyncio.Task[StreamEvent] | None = None
        self._iterator: AsyncGenerator[StreamEvent, None] = self._run()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> StreamEvent:
        if self._closed:
            raise StopAsyncIteration
        if self._next_task is not None:
            raise RuntimeError("A stream supports only one concurrent reader")
        task = asyncio.create_task(self._iterator.__anext__())
        self._next_task = task
        try:
            return await task
        except asyncio.CancelledError:
            was_closed = self._closed
            self._mark_closed()
            if was_closed:
                raise StopAsyncIteration from None
            raise
        except BaseException:
            self._mark_closed()
            raise
        finally:
            self._next_task = None

    async def __aenter__(self) -> Self:
        if self._closed:
            raise RuntimeError("Stream is closed")
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    def _mark_closed(self) -> None:
        self._closed = True
        if self._on_close is not None:
            self._on_close(self)

    async def aclose(self) -> None:
        self._mark_closed()
        task = self._next_task
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError, StopAsyncIteration):
                await task
        await self._iterator.aclose()

    async def _authenticate(self, socket: ClientConnection) -> None:
        if self._authenticator is None:
            return
        try:
            async with asyncio.timeout(self.config.authentication_timeout):
                frame = await self._authenticator()
                await self._request_limiter.acquire()
                await socket.send(json.dumps(frame))
                while True:
                    message = _message(await socket.recv())
                    if message.get("method") == "pong":
                        continue
                    if message.get("method") != "auth_v2":
                        raise AuthenticationError("Unexpected WebSocket authentication reply")
                    if message.get("status") != "success":
                        raise AuthenticationError(
                            str(message.get("message", "WebSocket authentication rejected"))
                        )
                    data = message.get("data")
                    if not isinstance(data, dict):
                        raise AuthenticationError("Authentication reply omitted identity")
                    try:
                        matches = address(data["account"]) == self._account and address(
                            data["signer"]
                        ) == address(frame["params"]["signer"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise AuthenticationError("Invalid authenticated identity") from exc
                    if not matches:
                        raise AuthenticationError("Server authenticated a different account/signer")
                    return
        except TimeoutError as exc:
            raise AuthenticationError("WebSocket authentication timed out") from exc

    async def _subscribe(self, socket: ClientConnection) -> list[DataEvent]:
        params: dict[str, Any] = {"channel": self.channel}
        if self.market_ids:
            params["market_ids"] = list(self.market_ids)
        await self._request_limiter.acquire()
        await socket.send(json.dumps({"method": "subscribe", "params": params}))
        pending: list[DataEvent] = []
        try:
            async with asyncio.timeout(self.config.subscription_timeout):
                while True:
                    message = _message(await socket.recv())
                    if message.get("method") == "subscribe":
                        if message.get("channel") != self.channel:
                            raise SubscriptionError("Acknowledgement has an unexpected channel")
                        if message.get("status") != "success":
                            raise SubscriptionError(
                                str(message.get("message", "Subscription rejected"))
                            )
                        if self.market_ids:
                            data = message.get("data")
                            if not isinstance(data, dict):
                                raise SubscriptionError("Acknowledgement omitted the market filter")
                            echoed = data.get("market_ids")
                            if (
                                not isinstance(echoed, list)
                                or any(type(value) is not int for value in echoed)
                                or set(echoed) != set(self.market_ids)
                            ):
                                raise SubscriptionError("Requested market filter was not applied")
                        return pending
                    if message.get("type") in ("snapshot", "update"):
                        if len(pending) >= self.config.max_pending_messages:
                            raise ProtocolError(
                                "Too many messages before subscription acknowledgement"
                            )
                        pending.append(
                            _data_event(message, self.channel, self.market_ids, self._account)
                        )
                    elif message.get("method") != "pong":
                        raise ProtocolError(
                            "Unexpected message before subscription acknowledgement"
                        )
        except TimeoutError as exc:
            raise SubscriptionError("Timed out waiting for subscription acknowledgement") from exc

    async def _run(self) -> AsyncGenerator[StreamEvent, None]:
        reconnects = 0
        previously_connected = False
        last_error: Exception | None = None
        while not self._closed:
            try:
                async with connect(
                    self.config.websocket_url,
                    open_timeout=self.config.request_timeout,
                    close_timeout=2,
                    ping_interval=20,
                    ping_timeout=20,
                    max_queue=self.config.websocket_max_queue,
                    max_size=self.config.websocket_max_size,
                ) as socket:
                    self._socket = socket
                    await self._authenticate(socket)
                    pending = await self._subscribe(socket)
                    yield ConnectionEvent(
                        state="connected",
                        stale=previously_connected,
                        attempt=reconnects,
                    )
                    previously_connected = True
                    for event in pending:
                        reconnects = 0
                        yield event
                    while not self._closed:
                        message = _message(await socket.recv())
                        if message.get("type") in ("snapshot", "update"):
                            event = _data_event(
                                message, self.channel, self.market_ids, self._account
                            )
                            reconnects = 0
                            yield event
                        elif message.get("method") == "pong":
                            continue
                        elif message.get("method") == "auth_v2":
                            raise AuthenticationError("WebSocket authorization is no longer valid")
                        elif message.get("status") == "error":
                            raise SubscriptionError(str(message.get("message", "Stream rejected")))
                        else:
                            raise ProtocolError("Unexpected WebSocket message")
            except InvalidStatus as exc:
                if 400 <= exc.response.status_code < 500 and exc.response.status_code != 429:
                    raise TransportError(
                        f"WebSocket upgrade rejected with HTTP {exc.response.status_code}"
                    ) from exc
                last_error = exc
            except (ConnectionClosed, OSError, TimeoutError, InvalidHandshake) as exc:
                last_error = exc
            finally:
                self._socket = None
            if self._closed:
                return
            yield ConnectionEvent(state="disconnected", stale=True, attempt=reconnects)
            if reconnects >= self.config.max_reconnect_attempts:
                raise ReconnectExhaustedError(
                    "WebSocket reconnect budget exhausted"
                ) from last_error
            reconnects += 1
            yield ConnectionEvent(state="reconnecting", stale=True, attempt=reconnects)
            await asyncio.sleep(
                min(
                    self.config.reconnect_delay * 2 ** (reconnects - 1),
                    self.config.max_reconnect_delay,
                )
            )


# Preserve the initial alpha's name; all streams use the same lifecycle implementation.
PublicStream = RiseXStream
