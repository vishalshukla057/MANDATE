import abc
import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional
import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.config import settings

logger = logging.getLogger("paypal_client")


class PayPalError(Exception):
    def __init__(self, name: str, message: str, details: Any = None, debug_id: Optional[str] = None, status_code: int = 400):
        super().__init__(f"PayPalError: [{name}] {message} (Debug ID: {debug_id})")
        self.name = name
        self.message = message
        self.details = details or []
        self.debug_id = debug_id
        self.status_code = status_code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "message": self.message,
            "details": self.details,
            "debug_id": self.debug_id,
            "status_code": self.status_code,
        }


class PayPalGateway(abc.ABC):
    """Abstract interface for all PayPal API interactions."""

    # Invoicing
    @abc.abstractmethod
    async def generate_next_invoice_number(self) -> str:
        pass

    @abc.abstractmethod
    async def create_draft_invoice(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def send_invoice(self, invoice_id: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def get_invoice(self, invoice_id: str) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def update_invoice(self, invoice_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def send_reminder(self, invoice_id: str, subject: str, note: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def cancel_invoice(self, invoice_id: str, subject: str, note: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def record_external_payment(self, invoice_id: str, payment_data: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def search_invoices(self, search_criteria: Dict[str, Any]) -> Dict[str, Any]:
        pass

    # Orders / Checkout
    @abc.abstractmethod
    async def create_order(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def capture_order(self, order_id: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def get_order(self, order_id: str) -> Dict[str, Any]:
        pass

    # Payouts
    @abc.abstractmethod
    async def create_payout_batch(self, payload: Dict[str, Any], sender_batch_id: Optional[str] = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def get_payout_batch(self, batch_id: str) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def get_payout_item(self, item_id: str) -> Dict[str, Any]:
        pass

    # Disputes
    @abc.abstractmethod
    async def list_disputes(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def get_dispute(self, dispute_id: str) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def send_dispute_message(self, dispute_id: str, message: str) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def provide_dispute_evidence(self, dispute_id: str, evidence_data: Dict[str, Any]) -> Dict[str, Any]:
        pass

    @abc.abstractmethod
    async def accept_dispute_claim(self, dispute_id: str, note: Optional[str] = None) -> Dict[str, Any]:
        pass

    # Webhooks
    @abc.abstractmethod
    async def verify_webhook_signature(self, headers: Dict[str, str], raw_body: str, webhook_id: str) -> bool:
        pass


class LivePayPalGateway(PayPalGateway):
    """Live PayPal sandbox implementation with token caching, backoff, and tracing."""

    def __init__(self):
        self.base_url = settings.PAYPAL_BASE_URL.rstrip("/")
        self.client_id = settings.PAYPAL_CLIENT_ID
        self.client_secret = settings.PAYPAL_CLIENT_SECRET
        self._cached_token: Optional[str] = None
        self._token_expires_at: float = 0.0
        self._token_lock = asyncio.Lock()

    async def _get_access_token(self, force_refresh: bool = False) -> str:
        async with self._token_lock:
            now = time.time()
            if not force_refresh and self._cached_token and now < (self._token_expires_at - 60):
                return self._cached_token

            url = f"{self.base_url}/v1/oauth2/token"
            auth = (self.client_id, self.client_secret)
            data = {"grant_type": "client_credentials"}
            headers = {"Accept": "application/json", "Accept-Language": "en_US"}

            async with httpx.AsyncClient(timeout=15.0) as client:
                res = await client.post(url, auth=auth, data=data, headers=headers)
                if res.status_code != 200:
                    debug_id = res.headers.get("paypal-debug-id", "unknown")
                    raise PayPalError(
                        name="OAUTH_TOKEN_ERROR",
                        message=f"Failed to obtain OAuth2 token: {res.text}",
                        debug_id=debug_id,
                        status_code=res.status_code,
                    )
                payload = res.json()
                self._cached_token = payload["access_token"]
                self._token_expires_at = now + int(payload.get("expires_in", 3600))
                return self._cached_token

    async def _request(
        self,
        method: str,
        path: str,
        json_data: Optional[Dict[str, Any]] = None,
        params: Optional[Dict[str, Any]] = None,
        idempotency_key: Optional[str] = None,
        retry_count: int = 0,
    ) -> Dict[str, Any]:
        token = await self._get_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if idempotency_key:
            headers["PayPal-Request-Id"] = idempotency_key

        url = f"{self.base_url}{path}"
        start_time = time.time()

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.request(
                    method=method,
                    url=url,
                    json=json_data,
                    params=params,
                    headers=headers,
                )
        except httpx.RequestError as exc:
            if idempotency_key and retry_count < 2:
                await asyncio.sleep(2 ** retry_count)
                return await self._request(method, path, json_data, params, idempotency_key, retry_count + 1)
            raise PayPalError(name="NETWORK_ERROR", message=str(exc), status_code=503)

        latency_ms = (time.time() - start_time) * 1000
        debug_id = response.headers.get("paypal-debug-id")
        logger.info(
            f"PayPal {method} {path} -> {response.status_code} in {latency_ms:.1f}ms (Debug-ID: {debug_id})"
        )

        if response.status_code == 401 and retry_count == 0:
            await self._get_access_token(force_refresh=True)
            return await self._request(method, path, json_data, params, idempotency_key, retry_count + 1)

        if response.status_code >= 500 and idempotency_key and retry_count < 2:
            await asyncio.sleep(2 ** retry_count)
            return await self._request(method, path, json_data, params, idempotency_key, retry_count + 1)

        if response.status_code >= 400:
            err_data = response.json() if response.content else {}
            raise PayPalError(
                name=err_data.get("name", "PAYPAL_ERROR"),
                message=err_data.get("message", response.text),
                details=err_data.get("details"),
                debug_id=debug_id,
                status_code=response.status_code,
            )

        if response.status_code == 204 or not response.content:
            return {"success": True, "debug_id": debug_id}

        result = response.json()
        if isinstance(result, dict) and debug_id:
            result["_paypal_debug_id"] = debug_id
        return result

    # Invoicing Implementations
    async def generate_next_invoice_number(self) -> str:
        res = await self._request("POST", "/v2/invoicing/generate-next-invoice-number")
        return res.get("invoice_number", f"INV-{uuid.uuid4().hex[:6].upper()}")

    async def create_draft_invoice(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        return await self._request("POST", "/v2/invoicing/invoices", json_data=payload, idempotency_key=idempotency_key)

    async def send_invoice(self, invoice_id: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        return await self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/send", idempotency_key=idempotency_key)

    async def get_invoice(self, invoice_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v2/invoicing/invoices/{invoice_id}")

    async def update_invoice(self, invoice_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("PUT", f"/v2/invoicing/invoices/{invoice_id}", json_data=payload)

    async def send_reminder(self, invoice_id: str, subject: str, note: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        body = {"subject": subject, "note": note, "send_to_recipient": True}
        return await self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/remind", json_data=body, idempotency_key=idempotency_key)

    async def cancel_invoice(self, invoice_id: str, subject: str, note: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        body = {"subject": subject, "note": note, "send_to_recipient": True}
        return await self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/cancel", json_data=body, idempotency_key=idempotency_key)

    async def record_external_payment(self, invoice_id: str, payment_data: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        return await self._request("POST", f"/v2/invoicing/invoices/{invoice_id}/payments", json_data=payment_data, idempotency_key=idempotency_key)

    async def search_invoices(self, search_criteria: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", "/v2/invoicing/search-invoices", json_data=search_criteria)

    # Orders / Checkout
    async def create_order(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        return await self._request("POST", "/v2/checkout/orders", json_data=payload, idempotency_key=idempotency_key)

    async def capture_order(self, order_id: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        return await self._request("POST", f"/v2/checkout/orders/{order_id}/capture", idempotency_key=idempotency_key)

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v2/checkout/orders/{order_id}")

    # Payouts
    async def create_payout_batch(self, payload: Dict[str, Any], sender_batch_id: Optional[str] = None) -> Dict[str, Any]:
        return await self._request("POST", "/v1/payments/payouts", json_data=payload, idempotency_key=sender_batch_id)

    async def get_payout_batch(self, batch_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v1/payments/payouts/{batch_id}")

    async def get_payout_item(self, item_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v1/payments/payouts-item/{item_id}")

    # Disputes
    async def list_disputes(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return await self._request("GET", "/v1/customer/disputes", params=params)

    async def get_dispute(self, dispute_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/v1/customer/disputes/{dispute_id}")

    async def send_dispute_message(self, dispute_id: str, message: str) -> Dict[str, Any]:
        return await self._request("POST", f"/v1/customer/disputes/{dispute_id}/send-message", json_data={"message": message})

    async def provide_dispute_evidence(self, dispute_id: str, evidence_data: Dict[str, Any]) -> Dict[str, Any]:
        return await self._request("POST", f"/v1/customer/disputes/{dispute_id}/provide-evidence", json_data=evidence_data)

    async def accept_dispute_claim(self, dispute_id: str, note: Optional[str] = None) -> Dict[str, Any]:
        return await self._request("POST", f"/v1/customer/disputes/{dispute_id}/accept-claim", json_data={"note": note or "Accepted claim"})

    # Webhook Verification
    async def verify_webhook_signature(self, headers: Dict[str, str], raw_body: str, webhook_id: str) -> bool:
        verification_payload = {
            "auth_algo": headers.get("paypal-auth-algo", ""),
            "cert_url": headers.get("paypal-cert-url", ""),
            "transmission_id": headers.get("paypal-transmission-id", ""),
            "transmission_sig": headers.get("paypal-transmission-sig", ""),
            "transmission_time": headers.get("paypal-transmission-time", ""),
            "webhook_id": webhook_id,
            "webhook_event": httpx._types.json.loads(raw_body) if isinstance(raw_body, str) else raw_body,
        }
        res = await self._request("POST", "/v1/notifications/verify-webhook-signature", json_data=verification_payload)
        return res.get("verification_status") == "SUCCESS"


class FakePayPalGateway(PayPalGateway):
    """
    Deterministic in-memory PayPal simulator for hermetic tests and offline demo mode.
    Maintains simulated PayPal entities and responds with genuine PayPal schema shapes.
    """

    def __init__(self):
        self.invoices: Dict[str, Dict[str, Any]] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self.payout_batches: Dict[str, Dict[str, Any]] = {}
        self.payout_items: Dict[str, Dict[str, Any]] = {}
        self.disputes: Dict[str, Dict[str, Any]] = {}
        self._seq = 1000

    def _next_debug_id(self) -> str:
        return f"fake-dbg-{uuid.uuid4().hex[:8]}"

    async def generate_next_invoice_number(self) -> str:
        self._seq += 1
        return f"INV-{self._seq}"

    async def create_draft_invoice(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        inv_id = f"INV2-{uuid.uuid4().hex[:12].upper()}"
        debug_id = self._next_debug_id()

        total_amount = 0.0
        line_items = payload.get("items", [])
        for item in line_items:
            qty = float(item.get("quantity", 1))
            price = float(item.get("unit_amount", {}).get("value", 0.0))
            total_amount += qty * price

        invoice_record = {
            "id": inv_id,
            "status": "DRAFT",
            "detail": payload.get("detail", {}),
            "invoicer": payload.get("invoicer", {}),
            "primary_recipients": payload.get("primary_recipients", []),
            "items": line_items,
            "amount": {
                "currency_code": payload.get("detail", {}).get("currency_code", "USD"),
                "value": f"{total_amount:.2f}",
                "breakdown": {
                    "item_total": {"currency_code": "USD", "value": f"{total_amount:.2f}"}
                }
            },
            "due_amount": {
                "currency_code": "USD",
                "value": f"{total_amount:.2f}"
            },
            "links": [
                {
                    "rel": "payer-view",
                    "href": f"/invoices/{inv_id}/view",
                    "method": "GET"
                },
                {
                    "rel": "send",
                    "href": f"https://api-m.sandbox.paypal.com/v2/invoicing/invoices/{inv_id}/send",
                    "method": "POST"
                }
            ],
            "_paypal_debug_id": debug_id,
        }
        self.invoices[inv_id] = invoice_record
        return invoice_record

    async def send_invoice(self, invoice_id: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if invoice_id not in self.invoices:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Invoice {invoice_id} not found", debug_id=self._next_debug_id(), status_code=404)
        self.invoices[invoice_id]["status"] = "SENT"
        return {"href": f"https://api-m.sandbox.paypal.com/v2/invoicing/invoices/{invoice_id}", "rel": "self", "method": "GET", "_paypal_debug_id": self._next_debug_id()}

    async def get_invoice(self, invoice_id: str) -> Dict[str, Any]:
        if invoice_id not in self.invoices:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Invoice {invoice_id} not found", debug_id=self._next_debug_id(), status_code=404)
        return self.invoices[invoice_id]

    async def update_invoice(self, invoice_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        if invoice_id not in self.invoices:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Invoice {invoice_id} not found", debug_id=self._next_debug_id(), status_code=404)
        self.invoices[invoice_id].update(payload)
        return self.invoices[invoice_id]

    async def send_reminder(self, invoice_id: str, subject: str, note: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if invoice_id not in self.invoices:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Invoice {invoice_id} not found", debug_id=self._next_debug_id(), status_code=404)
        return {"success": True, "reminded": True, "subject": subject, "_paypal_debug_id": self._next_debug_id()}

    async def cancel_invoice(self, invoice_id: str, subject: str, note: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if invoice_id not in self.invoices:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Invoice {invoice_id} not found", debug_id=self._next_debug_id(), status_code=404)
        self.invoices[invoice_id]["status"] = "CANCELLED"
        return {"success": True, "cancelled": True, "_paypal_debug_id": self._next_debug_id()}

    async def record_external_payment(self, invoice_id: str, payment_data: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if invoice_id not in self.invoices:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Invoice {invoice_id} not found", debug_id=self._next_debug_id(), status_code=404)
        inv = self.invoices[invoice_id]
        inv["status"] = "PAID"
        inv["due_amount"]["value"] = "0.00"
        payment_id = f"PAY-{uuid.uuid4().hex[:10].upper()}"
        return {"payment_id": payment_id, "_paypal_debug_id": self._next_debug_id()}

    async def search_invoices(self, search_criteria: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "total_items": len(self.invoices),
            "items": list(self.invoices.values()),
            "_paypal_debug_id": self._next_debug_id(),
        }

    # Orders / Checkout
    async def create_order(self, payload: Dict[str, Any], idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        order_id = f"ORD-{uuid.uuid4().hex[:10].upper()}"
        order = {
            "id": order_id,
            "status": "CREATED",
            "intent": payload.get("intent", "CAPTURE"),
            "purchase_units": payload.get("purchase_units", []),
            "links": [
                {"rel": "approve", "href": f"https://www.sandbox.paypal.com/checkoutnow?token={order_id}"}
            ],
            "_paypal_debug_id": self._next_debug_id(),
        }
        self.orders[order_id] = order
        return order

    async def capture_order(self, order_id: str, idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        if order_id not in self.orders:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Order {order_id} not found", debug_id=self._next_debug_id(), status_code=404)
        order = self.orders[order_id]
        order["status"] = "COMPLETED"
        capture_id = f"CAP-{uuid.uuid4().hex[:10].upper()}"
        return {
            "id": order_id,
            "status": "COMPLETED",
            "purchase_units": [
                {
                    "payments": {
                        "captures": [
                            {
                                "id": capture_id,
                                "status": "COMPLETED",
                                "amount": order["purchase_units"][0]["amount"] if order["purchase_units"] else {"value": "0.00", "currency_code": "USD"},
                            }
                        ]
                    }
                }
            ],
            "_paypal_debug_id": self._next_debug_id(),
        }

    async def get_order(self, order_id: str) -> Dict[str, Any]:
        if order_id not in self.orders:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Order {order_id} not found", debug_id=self._next_debug_id(), status_code=404)
        return self.orders[order_id]

    # Payouts
    async def create_payout_batch(self, payload: Dict[str, Any], sender_batch_id: Optional[str] = None) -> Dict[str, Any]:
        batch_id = f"BATCH-{uuid.uuid4().hex[:10].upper()}"
        items = payload.get("items", [])
        stored_items = []
        for it in items:
            it_id = f"ITEM-{uuid.uuid4().hex[:8].upper()}"
            item_data = {
                "payout_item_id": it_id,
                "payout_batch_id": batch_id,
                "transaction_status": "SUCCESS",
                "payout_item": it,
            }
            self.payout_items[it_id] = item_data
            stored_items.append(item_data)

        batch_header = {
            "payout_batch_id": batch_id,
            "batch_status": "SUCCESS",
            "sender_batch_header": payload.get("sender_batch_header", {}),
            "amount": {"currency": "USD", "value": sum(float(x.get("amount", {}).get("value", 0)) for x in items)},
        }
        batch_record = {"batch_header": batch_header, "items": stored_items, "_paypal_debug_id": self._next_debug_id()}
        self.payout_batches[batch_id] = batch_record
        return batch_record

    async def get_payout_batch(self, batch_id: str) -> Dict[str, Any]:
        if batch_id not in self.payout_batches:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Payout batch {batch_id} not found", debug_id=self._next_debug_id(), status_code=404)
        return self.payout_batches[batch_id]

    async def get_payout_item(self, item_id: str) -> Dict[str, Any]:
        if item_id not in self.payout_items:
            raise PayPalError("RESOURCE_NOT_FOUND", f"Payout item {item_id} not found", debug_id=self._next_debug_id(), status_code=404)
        return self.payout_items[item_id]

    # Disputes
    async def list_disputes(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return {"items": list(self.disputes.values()), "_paypal_debug_id": self._next_debug_id()}

    async def get_dispute(self, dispute_id: str) -> Dict[str, Any]:
        if dispute_id not in self.disputes:
            # Generate realistic mock dispute if asked for demo
            self.disputes[dispute_id] = {
                "dispute_id": dispute_id,
                "status": "UNDER_REVIEW",
                "dispute_amount": {"currency_code": "USD", "value": "250.00"},
                "reason": "MERCHANDISE_OR_SERVICE_NOT_RECEIVED",
                "dispute_life_cycle_stage": "CHARGEBACK",
                "_paypal_debug_id": self._next_debug_id(),
            }
        return self.disputes[dispute_id]

    async def send_dispute_message(self, dispute_id: str, message: str) -> Dict[str, Any]:
        return {"success": True, "message_sent": True, "_paypal_debug_id": self._next_debug_id()}

    async def provide_dispute_evidence(self, dispute_id: str, evidence_data: Dict[str, Any]) -> Dict[str, Any]:
        return {"success": True, "evidence_received": True, "_paypal_debug_id": self._next_debug_id()}

    async def accept_dispute_claim(self, dispute_id: str, note: Optional[str] = None) -> Dict[str, Any]:
        if dispute_id in self.disputes:
            self.disputes[dispute_id]["status"] = "RESOLVED"
        return {"success": True, "claim_accepted": True, "_paypal_debug_id": self._next_debug_id()}

    # Webhook Verification
    async def verify_webhook_signature(self, headers: Dict[str, str], raw_body: str, webhook_id: str) -> bool:
        # In fake mode, always return True if signature header exists, or True by default
        return True


_fake_gateway_instance: Optional[FakePayPalGateway] = None
_live_gateway_instance: Optional[LivePayPalGateway] = None


def get_paypal_gateway() -> PayPalGateway:
    global _fake_gateway_instance, _live_gateway_instance
    if settings.PAYPAL_MODE.lower() == "live":
        if _live_gateway_instance is None:
            _live_gateway_instance = LivePayPalGateway()
        return _live_gateway_instance
    if _fake_gateway_instance is None:
        _fake_gateway_instance = FakePayPalGateway()
    return _fake_gateway_instance


def reset_fake_gateway() -> FakePayPalGateway:
    global _fake_gateway_instance
    _fake_gateway_instance = FakePayPalGateway()
    return _fake_gateway_instance

