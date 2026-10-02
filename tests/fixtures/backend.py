"""Deterministic contract-shaped HTTP server for offline lifecycle tests."""

import json
import time

import httpx

AUTH = "0x0D919DAA3f12AE715744Eb648c00066c5DBd66f0"
ROUTER = "0xaadde0cea454f2bcb26f46ed54c5709b7bb34a7e"
USDC = "0xe436820ba0c69702c1d3e601d421c0ef38262739"


class Backend:
    def __init__(self, account, signer, market):
        self.account = account
        self.signer = signer
        self.market = market
        self.domain = {
            "name": "RISEx",
            "version": "1",
            "chain_id": "4153",
            "verifying_contract": AUTH,
        }
        self.system = {
            "chain": {"chain_id": "4153", "name": "Fixture chain"},
            "addresses": {"router": ROUTER, "auth": AUTH, "usdc": USDC},
            "is_maintenance_mode": False,
        }
        self.anchor = 0
        self.bitmap = 0
        self.requests = []
        self.posts = []
        self.orders = {}
        self.positions = []
        self.fills = []
        self.nonce_counter = 0
        self.post_behavior = "success"
        self.transform = None

    def response(self, data, status=200):
        return httpx.Response(status, json={"data": data, "request_id": "offline-request"})

    def order(self, order_id, client_id="123", status="ORDER_STATUS_OPEN"):
        return {
            "id": order_id,
            "market_id": "1",
            "sender": self.account,
            "side": "BUY",
            "type": "LIMIT",
            "time_in_force": "GTC",
            "status": status,
            "price": "60000",
            "size": "0.001",
            "filled_size": "0",
            "avg_price": "0",
            "post_only": False,
            "reduce_only": False,
            "client_order_id": str(client_id),
            "wide_order_id": str(int(order_id[2:18], 16)),
            "resting_order_id": str(int(order_id[2:18], 16) >> 1),
            "created_at": str(time.time_ns()),
        }

    async def __call__(self, request):
        self.requests.append(request)
        path = request.url.path
        query = request.url.params
        if request.method == "POST":
            body = json.loads(request.content)
            self.posts.append((path, body))
            if self.post_behavior == "reject":
                return httpx.Response(
                    400, json={"error": {"code": "InvalidArgument", "message": "rejected"}}
                )
            nonce = body.get("permit", body)
            anchor, bit = int(nonce["nonce_anchor"]), int(nonce["nonce_bitmap_index"])
            if anchor > self.anchor:
                self.anchor, self.bitmap = anchor, 0
            self.bitmap |= 1 << bit
            if path == "/v1/orders/place":
                sequence = len(self.posts) * 2
                order_id = "0x" + sequence.to_bytes(8, "big").hex() + "00" * 16
                row = self.order(order_id, body["client_order_id"])
                self.orders[order_id] = row
                data = {
                    "order_id": order_id,
                    "tx_hash": "0x" + "ab" * 32,
                    "block_number": "500",
                    "sc_order_id": str(sequence),
                    "filled_quantity": "",
                    "filled_percent": "",
                    "message": "",
                }
            elif path in ("/v1/orders/cancel", "/v1/orders/cancel-all"):
                if "order_id" in body:
                    self.orders[body["order_id"]]["status"] = "ORDER_STATUS_CANCELLED"
                else:
                    for row in self.orders.values():
                        if int(row["market_id"]) == body["market_id"]:
                            row["status"] = "ORDER_STATUS_CANCELLED"
                data = {"success": True, "tx_hash": "0x" + "cd" * 32, "block_number": "501"}
            else:
                data = {
                    "success": True,
                    "transaction_hash": "0x" + "ef" * 32,
                    "status": 1,
                    "block_number": "499",
                }
            if self.post_behavior == "timeout":
                raise httpx.ReadTimeout("response lost after execution", request=request)
            if self.post_behavior == "server_error":
                return httpx.Response(
                    503, json={"error": {"code": "Internal"}, "request_id": "uncertain-1"}
                )
            if self.post_behavior == "malformed":
                return httpx.Response(200, json={"data": {"unexpected": True}})
            return self.response(data)
        if path == "/v1/auth/eip712-domain":
            data = self.domain
        elif path == "/v1/system/config":
            data = self.system
        elif path.startswith("/v1/nonce-state/"):
            data = {
                "nonce_anchor": str(self.anchor),
                "current_bitmap_index": self.bitmap.bit_length(),
                "bitmap": hex(self.bitmap),
            }
        elif path == "/v1/markets":
            data = {"markets": [self.market]}
        elif path == "/v1/auth/session-key-status":
            data = {"status": 1, "status_description": "Active"}
        elif path == "/v1/auth/nonce":
            self.nonce_counter += 1
            data = {"nonce": f"{self.nonce_counter:064x}"}
        elif path == "/v1/account/balance":
            data = {"balance": "12.696037480899793696"}
        elif path == "/v1/account/cross-margin-balance":
            data = {"balance": "-0.100000000000000001"}
        elif path == "/v1/account/position":
            data = {
                "position": {
                    "market_id": "0",
                    "size": "0",
                    "quote_amount": "0",
                    "last_funding_payment": "-95.786475745565619854",
                    "margin_mode": 0,
                    "side": 0,
                    "isolated_usdc_balance": "0",
                    "avg_entry_price": "",
                    "mark_price": "",
                    "index_price": "",
                    "leverage": "25",
                    "unrealized_pnl": "",
                }
            }
        elif path == "/v1/positions":
            page, limit = int(query["page"]), int(query["page_size"])
            data = {
                "positions": self.positions[(page - 1) * limit : page * limit],
                "total_count": len(self.positions),
                "page": page,
                "page_size": limit,
                "has_next_page": page * limit < len(self.positions),
            }
        elif path == "/v1/orders/open":
            rows = [row for row in self.orders.values() if row["status"] == "ORDER_STATUS_OPEN"]
            if "order_ids" in query:
                rows = [row for row in rows if row["id"] in query.get_list("order_ids")]
            start, limit = int(query["start_index"]), int(query["limit"])
            data = {
                "market_id": query.get("market_id", "0"),
                "account": self.account,
                "total_orders": str(len(rows)),
                "orders": [
                    {
                        "order_id": row["id"],
                        "market_id": 1,
                        "account": self.account,
                        "wide_order_id": row["wide_order_id"],
                        "resting_order_id": row["resting_order_id"],
                        "side": 0,
                        "size_steps": 1000,
                        "price_ticks": 600000,
                        "order_type": 1,
                        "time_in_force": 0,
                        "post_only": False,
                        "reduce_only": False,
                        "client_order_id": row["client_order_id"],
                    }
                    for row in rows[start : start + limit]
                ],
            }
        elif path.startswith("/v1/orders/by-id/"):
            order_id = path.split("/")[-1]
            if order_id not in self.orders:
                return httpx.Response(404, json={"message": "not found"})
            data = {"order": self.orders[order_id]}
        elif path == "/v1/orders":
            page, limit = int(query["page"]), int(query["limit"])
            rows = list(self.orders.values())
            if "order_ids" in query:
                rows = [row for row in rows if row["id"] in query.get_list("order_ids")]
            data = {
                "orders": rows[(page - 1) * limit : page * limit],
                "page": page,
                "has_next_page": page * limit < len(rows),
            }
        elif path == "/v1/trade-history":
            page, limit = int(query["page"]), int(query["limit"])
            data = {
                "trades": self.fills[(page - 1) * limit : page * limit],
                "page": page,
                "has_next_page": page * limit < len(self.fills),
                "market_id": query.get("market_id", "0"),
                "wallet_address": self.account,
            }
        else:
            raise AssertionError(f"Unexpected offline endpoint: {path}")
        if self.transform is not None:
            data = self.transform(path, data)
        return self.response(data)
