import datetime
import decimal
import hashlib
import hmac
import json
import os
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import payitgo  # noqa: E402

KEY = "8334dfeae25b9b200b75995e6e55cf8c3602120c1920bfef7a4ff8900b620881"

# Signed by the gateway's own signer.
SECRET = "05bd6b428158318cc38756fa34aa60795af0fc5d9a316dcc9b060c8ca1863fc3"
HEADER = "t=1760000000,v1=b61376838fdc09d9241eb407eda645be1e67a88fed148fc057c56a1b784e01e5"
BODY = b'{"id":"b8e1f80bd70ae0784c7855a4","type":"payment.confirmed","livemode":false,"created_at":"2026-10-09T12:00:00Z","data":{"id":"040ffd5925d40e11c67b7238","order_id":"ORD-1","status":"confirmed","currency":"USD","amount":"10"}}'
OTHER_SECRET = "bd4106123e659e259c401119d0c7ef0a221df306a5a414b6fb6a579499962d26"


def sign(secret, t, body):
    return "t=%d,v1=%s" % (t, hmac.new(secret.encode(), str(t).encode() + b"." + body, hashlib.sha256).hexdigest())


class WebhookTest(unittest.TestCase):
    def test_accepts_gateway_signature(self):
        event = payitgo.Webhook.construct_event(BODY, HEADER, SECRET, tolerance=0)
        self.assertEqual(event.type, "payment.confirmed")
        self.assertEqual(event.data.order_id, "ORD-1")
        self.assertFalse(event.livemode)

    def test_accepts_str_body(self):
        self.assertEqual(payitgo.Webhook.construct_event(BODY.decode(), HEADER, SECRET, tolerance=0)["id"], "b8e1f80bd70ae0784c7855a4")

    def test_rejects(self):
        for body, header, secret in [
            (BODY.replace(b'"10"', b'"1000"'), HEADER, SECRET),
            (BODY, HEADER, OTHER_SECRET),
            (BODY, None, SECRET),
        ]:
            with self.assertRaises(payitgo.SignatureVerificationError):
                payitgo.Webhook.construct_event(body, header, secret, tolerance=0)

    def test_rejects_non_ascii_signature(self):
        with self.assertRaisesRegex(payitgo.SignatureVerificationError, "signature does not match"):
            payitgo.Webhook.construct_event(BODY, "t=1760000000,v1=" + "é" * 64, SECRET, tolerance=0)

    def test_rejects_non_numeric_timestamp(self):
        header = "t=soon,v1=" + hmac.new(SECRET.encode(), b"soon." + BODY, hashlib.sha256).hexdigest()
        with self.assertRaisesRegex(payitgo.SignatureVerificationError, "malformed timestamp"):
            payitgo.Webhook.construct_event(BODY, header, SECRET)
        self.assertEqual(payitgo.Webhook.construct_event(BODY, header, SECRET, tolerance=0).id, "b8e1f80bd70ae0784c7855a4")

    def test_tolerance(self):
        with self.assertRaises(payitgo.SignatureVerificationError):
            payitgo.Webhook.construct_event(BODY, HEADER, SECRET)
        fresh = sign(SECRET, int(time.time()), BODY)
        self.assertEqual(payitgo.Webhook.construct_event(BODY, fresh, SECRET).id, "b8e1f80bd70ae0784c7855a4")


class ClientTest(unittest.TestCase):
    def test_checks(self):
        os.environ.pop("PAYITGO_BASE_URL", None)
        for key in ("", "  ", None):
            with self.assertRaisesRegex(ValueError, "the API key is required"):
                payitgo.Client(key, base_url="http://x")
        # Without an address the client uses the release's default, or refuses.
        if payitgo.DEFAULT_BASE_URL:
            self.assertEqual(payitgo.Client(KEY)._base_url, payitgo.DEFAULT_BASE_URL.rstrip("/"))
        else:
            with self.assertRaises(ValueError):
                payitgo.Client(KEY)
        self.assertFalse(hasattr(payitgo.Client(KEY, base_url="http://x"), "livemode"))


WEBHOOK = {
    "livemode": False, "url": "https://shop.example/payitgo/ipn", "secret": "ab" * 32,
    "created_at": "2026-10-10T09:00:00Z", "updated_at": "2026-10-10T09:05:00Z",
}
DELIVERY = {
    "id": "whd_1", "merchant_id": "mch_1", "target": "account", "url": WEBHOOK["url"], "event_id": "evt_1",
    "event_type": "ping", "payload": '{"id":"evt_1","type":"ping"}', "status": "pending", "attempts": 0,
    "max_attempts": 7, "next_attempt_at": "2026-10-10T12:00:00Z", "created_at": "2026-10-10T12:00:00Z",
}
# A payment whose coin the customer chose on the merchant's own page.
CHOSEN = {
    "id": "pay_1", "merchant_id": "mch_1", "order_id": "ORD-7", "status": "pending", "livemode": False,
    "token": "XRP", "chain": "xrp", "deposit_address": "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe",
    "destination_tag": 1042, "crypto_amount": "20", "crypto_received": "0", "currency": "USD", "amount": "10",
    "amount_due": "10", "fees_paid_by_customer": False,
    "settlement": "custodial", "amount_received": "0", "rate_locked": "0.5", "deposits": [],
    "expires_at": "2026-10-10T12:15:00Z", "created_at": "2026-10-10T12:00:00Z",
    "network_fee_note": "Final in seconds. Include the destination tag: it is required.",
    "qr_payload": "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe",
    "checkout_token": "ct_abc", "checkout_url": "https://pay.example/ct_abc", "description": "Order 7",
    "metadata": {"cart": "42"},
}

# A test payment whose simulated outcome has paid it.
SIMULATED = {
    "id": "pay_2", "merchant_id": "mch_1", "order_id": "ORD-8", "status": "detecting", "livemode": False,
    "simulation": {"outcome": "confirmed", "state": "running"},
    "token": "USDT_TRC20", "chain": "tron", "deposit_address": "TPk2bpQqWRpXiaUHS9SXnZCDgvwrsuJ71o",
    "crypto_amount": "20", "crypto_received": "20", "currency": "USD", "amount": "20", "amount_due": "20",
    "fees_paid_by_customer": False, "settlement": "custodial",
    "amount_received": "20", "rate_locked": "1",
    "deposits": [{
        "tx_id": "8de0120d502929788a71a7b7cf60e5ef3c095cdbb05cd8a7ddabc0bc9ffa9582", "token": "USDT_TRC20", "chain": "tron", "amount": "20",
        "value": "20", "counted": True, "status": "pending", "seen_at": "2026-10-10T12:00:05Z", "simulated": True,
    }],
    "expires_at": "2026-10-10T12:15:00Z", "created_at": "2026-10-10T12:00:00Z",
    "network_fee_note": "Final in about a minute.",
    "qr_payload": "TPk2bpQqWRpXiaUHS9SXnZCDgvwrsuJ71o",
}


# A payment priced in euros: every figure in EUR, converted by the gateway at the rate of the moment.
PRICED = dict(CHOSEN, order_id="ORD-10", currency="EUR", amount="45.5", amount_due="45.5", rate_locked="0.46")


class _FakeGateway(BaseHTTPRequestHandler):
    """Records each request and answers with the next queued (status, body), or echoes a
    created payment's body back as the payment."""

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        body = json.loads(raw) if raw else None
        self.server.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                     "body": body})
        if self.server.replies:
            status, reply = self.server.replies.pop(0)
        elif self.path == "/v1/payments":
            status, reply = 201, dict(body, id="pay_1", status="pending")
        else:
            status, reply = 500, {"error": "no reply queued"}
        data = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = _handle

    def log_message(self, *args):
        pass


class _GatewayTest(unittest.TestCase):
    """A client talking to a _FakeGateway of its own."""

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGateway)
        self.server.requests, self.server.replies = [], []
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = self.make_client()

    def make_client(self, **options):
        return payitgo.Client(KEY, base_url="http://127.0.0.1:%d/" % self.server.server_port,
                              max_retries=0, **options)

    def reply(self, status, body):
        self.server.replies.append((status, body))

    def request(self, i=0):
        return self.server.requests[i]


class WebhookIPNTest(_GatewayTest):
    def test_tokens(self):
        tokens = [{"token": "USDT_BEP20", "chain": "bsc", "minimum": "1", "network_fee": "0.01", "amount_due": "40.43", "fee_note": ""}]
        self.reply(200, {"currency": "USD", "tokens": tokens})
        self.assertEqual(self.client.tokens.list(), tokens)
        self.assertEqual((self.request(0)["method"], self.request(0)["path"]), ("GET", "/v1/tokens"))
        self.reply(200, {"currency": "USD", "tokens": tokens})
        self.client.tokens.list(amount="49.90")
        self.assertEqual(self.request(1)["path"], "/v1/tokens?amount=49.90")
        # The figures in another currency; the amount goes as str(amount).
        self.reply(200, {"currency": "EUR", "tokens": tokens})
        self.client.tokens.list(decimal.Decimal("49.90"), currency="eur")
        self.assertEqual(self.request(2)["path"], "/v1/tokens?amount=49.90&currency=eur")

    def test_get(self):
        self.reply(200, {"webhook": WEBHOOK})
        webhook = self.client.webhooks.get()
        req = self.request()
        self.assertEqual((req["method"], req["path"], req["body"]), ("GET", "/v1/webhook", None))
        self.assertEqual(req["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual(webhook, WEBHOOK)
        self.assertEqual(webhook.secret, WEBHOOK["secret"])

    def test_is_live(self):
        for live in (True, False):
            client = self.make_client()
            self.server.requests.clear()
            self.reply(200, {"webhook": dict(WEBHOOK, livemode=live)})
            self.assertIs(client.is_live(), live)
            req = self.request()
            self.assertEqual((req["method"], req["path"]), ("GET", "/v1/webhook"))
            self.assertEqual(req["headers"]["Authorization"], "Bearer " + KEY)
            # The second call does not ask again (no reply is queued: asking would fail).
            self.assertIs(client.is_live(), live)
            self.assertEqual(len(self.server.requests), 1)

    def test_is_live_error(self):
        self.reply(401, {"error": "invalid API key"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.is_live()
        self.assertEqual((ctx.exception.status, ctx.exception.message), (401, "invalid API key"))
        # An error is not kept: the next call asks again.
        self.reply(200, {"webhook": WEBHOOK})
        self.assertIs(self.client.is_live(), False)
        self.assertEqual(len(self.server.requests), 2)

    def test_set(self):
        self.reply(200, {"webhook": WEBHOOK})
        webhook = self.client.webhooks.set(WEBHOOK["url"])
        req = self.request()
        self.assertEqual((req["method"], req["path"]), ("PUT", "/v1/webhook"))
        self.assertEqual(req["headers"]["Content-Type"], "application/json")
        self.assertEqual(req["body"], {"url": WEBHOOK["url"]})
        self.assertEqual(webhook.url, WEBHOOK["url"])

    def test_remove(self):
        self.reply(200, {"webhook": dict(WEBHOOK, url=None)})
        webhook = self.client.webhooks.remove()
        self.assertEqual((self.request()["method"], self.request()["path"]), ("DELETE", "/v1/webhook"))
        self.assertIsNone(webhook.url)
        self.assertEqual(webhook.secret, WEBHOOK["secret"])

    def test_test(self):
        self.reply(202, {"delivery": DELIVERY})
        delivery = self.client.webhooks.test()
        req = self.request()
        self.assertEqual((req["method"], req["path"]), ("POST", "/v1/webhook/test"))
        self.assertEqual(req["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual((delivery.event_type, delivery.status, delivery.target), ("ping", "pending", "account"))

    def test_errors(self):
        self.reply(400, {"error": "no webhook url is set for this mode: set one first"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.webhooks.test()
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (400, "no webhook url is set for this mode: set one first"))
        forbidden = "a storefront's key cannot change the account's webhook: set webhook_url on its payments instead"
        self.reply(403, {"error": forbidden})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.webhooks.set(WEBHOOK["url"])
        self.assertEqual((ctx.exception.status, ctx.exception.message), (403, forbidden))

    def test_payment_webhook_url(self):
        payment = self.client.payments.create("ORD-1", 10)
        self.assertEqual(self.request(0)["body"], {"order_id": "ORD-1", "amount": "10"})
        self.assertNotIn("webhook_url", payment)
        payment = self.client.payments.create("ORD-2", 10, webhook_url="https://shop.example/own")
        self.assertEqual(self.request(1)["body"]["webhook_url"], "https://shop.example/own")
        self.assertEqual(payment.webhook_url, "https://shop.example/own")

    def test_payment_webhook_url_default(self):
        client = self.make_client(webhook_url="https://shop.example/default")
        payment = client.payments.create("ORD-1", 10)
        self.assertEqual(self.request(0)["body"]["webhook_url"], "https://shop.example/default")
        self.assertEqual(payment.webhook_url, "https://shop.example/default")
        payment = client.payments.create("ORD-2", 10, webhook_url="https://shop.example/own")  # the call's own wins
        self.assertEqual(self.request(1)["body"]["webhook_url"], "https://shop.example/own")
        self.assertEqual(payment.webhook_url, "https://shop.example/own")

    def test_payment_select_token(self):
        self.reply(200, CHOSEN)
        payment = self.client.payments.select_token("pay_1", "XRP")
        req = self.request()
        self.assertEqual((req["method"], req["path"]), ("POST", "/v1/payments/pay_1/token"))
        self.assertEqual(req["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual(req["headers"]["Content-Type"], "application/json")
        self.assertEqual(req["body"], {"token": "XRP"})
        self.assertEqual(payment, CHOSEN)
        self.assertEqual((payment.deposit_address, payment.destination_tag, payment.metadata.cart),
                         (CHOSEN["deposit_address"], 1042, "42"))
        # An order id is escaped in the path, as payments.retrieve does.
        self.reply(200, CHOSEN)
        self.client.payments.select_token("ORD 7/1", "XRP")
        self.assertEqual(self.request(1)["path"], "/v1/payments/ORD%207%2F1/token")

    def test_payments_list(self):
        self.reply(200, {"payments": [], "total": 0, "limit": 10, "offset": 20})
        page = self.client.payments.list(status="confirmed", store_id="sto_1", limit=10, offset=20)
        req = self.request()
        self.assertEqual(req["method"], "GET")
        self.assertEqual(req["path"], "/v1/payments?status=confirmed&store_id=sto_1&limit=10&offset=20")
        self.assertEqual((page.total, page.limit, page.offset), (0, 10, 20))
        self.reply(200, {"payments": [], "total": 0, "limit": 50, "offset": 0})
        self.client.payments.list()
        self.assertEqual(self.request(1)["path"], "/v1/payments")

    def test_payment_select_token_chosen(self):
        self.reply(409, {"error": "the coin for this payment can no longer be changed"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payments.select_token("pay_1", "XRP")
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (409, "the coin for this payment can no longer be changed"))

    def test_payment_simulate(self):
        self.reply(200, SIMULATED)
        payment = self.client.payments.simulate("pay_2", "confirmed")
        req = self.request()
        self.assertEqual((req["method"], req["path"]), ("POST", "/v1/payments/pay_2/simulate"))
        self.assertEqual(req["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual(req["headers"]["Content-Type"], "application/json")
        self.assertEqual(req["body"], {"outcome": "confirmed"})
        self.assertEqual(payment, SIMULATED)
        self.assertEqual((payment.simulation.outcome, payment.simulation.state), ("confirmed", "running"))
        self.assertTrue(payment.deposits[0].simulated)
        # An order id is escaped in the path, as payments.retrieve does.
        self.reply(200, SIMULATED)
        self.client.payments.simulate("ORD 8/1", "expired")
        self.assertEqual(self.request(1)["path"], "/v1/payments/ORD%208%2F1/simulate")

    def test_payment_simulate_running(self):
        self.reply(409, {"error": "a simulation is already running for this payment"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payments.simulate("pay_2", "confirmed")
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (409, "a simulation is already running for this payment"))

    def test_payment_create_simulate(self):
        self.reply(201, SIMULATED)
        payment = self.client.payments.create("ORD-8", 20, token="USDT_TRC20", simulate="confirmed")
        self.assertEqual(self.request()["body"],
                         {"order_id": "ORD-8", "amount": "20", "token": "USDT_TRC20", "simulate": "confirmed"})
        self.assertEqual(payment.simulation.outcome, "confirmed")
        self.assertIn("confirmed", payitgo.SIMULATION_OUTCOMES)

    def test_payment_create_price(self):
        self.reply(201, PRICED)
        payment = self.client.payments.create("ORD-10", decimal.Decimal("45.50"), currency="eur")
        # The amount goes as a string, with its currency.
        self.assertEqual(self.request()["body"], {"order_id": "ORD-10", "amount": "45.50", "currency": "eur"})
        self.assertEqual(payment, PRICED)
        self.assertEqual((payment.currency, payment.amount, payment.amount_due), ("EUR", "45.5", "45.5"))
        # Without a currency the price is in US dollars, and none is sent.
        self.client.payments.create("ORD-11", "10", "USDT_TRC20")
        self.assertEqual(self.request(1)["body"], {"order_id": "ORD-11", "amount": "10", "token": "USDT_TRC20"})
        self.reply(200, CHOSEN)
        self.assertEqual(self.client.payments.retrieve("pay_1").currency, "USD")

    def test_payment_create_price_no_rate(self):
        self.reply(503, {"error": "exchange rate unavailable for currency: try again shortly"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payments.create("ORD-10", "45.50", currency="XAF")
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (503, "exchange rate unavailable for currency: try again shortly"))


# A client's payout request, checked and priced into the open list.
PAYOUT_ITEM = {
    "id": "4e6a8c0b2d4f6a1c3e5b7d9a", "merchant_id": "3f8a1c27b94e05d6a2c71e90", "livemode": False,
    "batch_id": "9c1e3a5b7d9f1b3d5f7a9c1e", "reference": "wd-1001", "row": 1,
    "recipient": {"name": "Ana Silva", "note": "withdrawal 1001"}, "asset": "USDT_BEP20", "ticker": "USDT",
    "icon_url": "https://api.payitgo.co/coins/USDT_BEP20.svg", "chain": "bsc",
    "address": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", "amount": "25.5", "send_amount": "25.5",
    "fee": "0.51183421", "debit": "26.01183421",
    "usd_rate": "1", "amount_usd": "25.5", "status": "ready", "errors": [], "warnings": [],
    "created_at": "2026-10-20T09:15:01.88Z", "updated_at": "2026-10-20T09:15:01.88Z",
}
# A request kept but never sent: the address is of another network.
PAYOUT_INVALID = dict(PAYOUT_ITEM, id="5f7b9d1c3e5a7b9d1f3a5c7e", reference="wd-1002",
                      address="TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7", status="invalid", send_amount="0",
                      fee="0", debit="0", usd_rate="0", amount_usd="0",
                      errors=[{"code": "wrong_network", "field": "address",
                               "message": "this address is for Tron, not BNB Smart Chain",
                               "hint": "pay it in a coin of that network, or check the address"}])
COUNTS = {"total": 1, "invalid": 0, "ready": 1, "cooling": 0, "queued": 0, "sending": 0, "sent": 0, "failed": 0,
          "canceled": 0, "claimable": 0, "expired": 0}
TOTALS = [{"asset": "USDT_BEP20", "ticker": "USDT", "icon_url": "https://api.payitgo.co/coins/USDT_BEP20.svg",
           "chain": "bsc", "network": "BNB Smart Chain (BEP-20)", "items": 1, "amount": "25.5",
           "send_amount": "25.5", "fee": "0.51183421",
           "debit": "26.01183421", "debit_usd": "26.01", "saved_usd": "0"}]
PAYOUT_BATCH = {
    "id": "6a1f3c8e2d9b4a7051c3e8f2", "merchant_id": "3f8a1c27b94e05d6a2c71e90", "livemode": False,
    "reference": "payroll-2026-10", "name": "October payroll", "source": "api", "inbox": False, "open": False,
    "created_by": "5e1a9c3d7b2f40e8a6c4d2b1", "by_api_key": True, "fee_paid_by": "merchant", "convert": "same_network",
    "status": "quoted",
    "totals": TOTALS, "counts": COUNTS, "total_usd": "26.01", "quote_expires_at": "2026-10-20T10:15:02.41Z",
    "approvals_needed": 0, "approvals": [], "created_at": "2026-10-20T09:15:01.87Z",
    "updated_at": "2026-10-20T09:15:02.41Z",
}
# Submitted with too little in the wallet: it says what to send, and where.
AWAITING = dict(PAYOUT_BATCH, status="awaiting_funds", submitted_by="5e1a9c3d7b2f40e8a6c4d2b1",
                submitted_at="2026-10-20T09:16:40.09Z", waiting={
                    "reason": "funds", "note": "top up the payout wallet: 26.01183421 USDT_BEP20 on BNB Smart Chain",
                    "since": "2026-10-20T09:16:40.12Z", "shortfall": [{
                        "asset": "USDT_BEP20", "network": "BNB Smart Chain (BEP-20)", "amount": "26.01183421",
                        "address": "0x8F3a6B2c9D1e4F7a0B5c8D2e6F9a1B4c7D0e3F6a"}]})
INBOX = {
    "inbox": dict(PAYOUT_BATCH, id="9c1e3a5b7d9f1b3d5f7a9c1e", reference=None, name="Payout requests",
                  source="requests", inbox=True, open=True, created_by="requests", by_api_key=False),
    "coverage": [{"asset": "USDT_BEP20", "ticker": "USDT", "network": "BNB Smart Chain (BEP-20)",
                  "needed": "26.01183421", "spendable": "10", "top_up": "16.01183421",
                  "address": "0x8F3a6B2c9D1e4F7a0B5c8D2e6F9a1B4c7D0e3F6a"}],
    "items": [PAYOUT_ITEM], "total": 1, "limit": 10, "offset": 0,
}
WALLETS = [
    {"chain": "tron", "network": "Tron", "address": "TMkq7dZ3vN8xR2pB6cH9sJ4fL1wY5tGaQe", "livemode": False,
     "assets": [{"asset": "USDT_TRC20", "ticker": "USDT", "icon_url": "https://api.payitgo.co/coins/USDT_TRC20.svg",
                 "balance": "1000", "reserved": "0", "spendable": "1000", "needed": "0"}]},
    {"chain": "bsc", "network": "BNB Smart Chain", "address": "0x8F3a6B2c9D1e4F7a0B5c8D2e6F9a1B4c7D0e3F6a",
     "livemode": False,
     "assets": [{"asset": "USDT_BEP20", "ticker": "USDT", "icon_url": "https://api.payitgo.co/coins/USDT_BEP20.svg",
                 "balance": "10", "reserved": "0", "spendable": "10", "needed": "16.01183421"}]},
]
ROW = {"reference": "payroll-2026-10-ana", "asset": "USDT_BEP20", "address": PAYOUT_ITEM["address"],
       "amount": decimal.Decimal("25.50"), "name": "Ana Silva", "email": None}
SENT_ROW = {"reference": "payroll-2026-10-ana", "asset": "USDT_BEP20", "address": PAYOUT_ITEM["address"],
            "amount": "25.50", "name": "Ana Silva"}  # the amount as a string, None left out


# A claim link emailed to its recipient, funded and waiting to be claimed.
CLAIMABLE = dict(PAYOUT_ITEM, id="7a9c1e3b5d7f9a1c3e5b7d9f", reference="prize-7", asset="USDT_TRC20", chain="tron",
                 address="", recipient={"email": "winner@example.com"}, amount="50", send_amount="50",
                 status="claimable", claim={
                     "email": "winner@example.com", "assets": ["USDT_BEP20", "USDC_BEP20"],
                     "expires_at": "2026-11-03T09:20:00Z", "emailed": 1, "last_email_at": "2026-10-20T09:20:01Z",
                     "extended": 0, "url": "https://payitgo.co/pay/claim/q2Zk9xPq4m7Lr0VbN8cT1sWfYh3dJ6uE"})
# Claimed in another coin than it was funded with.
CLAIMED = dict(CLAIMABLE, asset="USDT_BEP20", chain="bsc", address=PAYOUT_ITEM["address"], status="queued",
               claim=dict(CLAIMABLE["claim"], claimed_at="2026-10-21T15:02:44Z", funded_asset="USDT_TRC20",
                          funded_amount="52.31"))
# Paid in BTC from the wallet's USDT on Tron, converted on its way.
CONVERTED = dict(PAYOUT_ITEM, id="8b0d2f4a6c8e0b2d4f6a8c0e", reference="payroll-2026-10-dan", asset="USDT_TRC20",
                 chain="tron", address="bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", amount="65", send_amount="67.01",
                 fee="4.442417", debit="69.442417", usd_rate="1", amount_usd="65", convert={
                     "to": "BTC", "ticker": "BTC", "network": "Bitcoin", "to_amount": "0.001", "estimate": "0.00100502"})
# A request removed from the list by a team member: its record stays, with no batch, its reference given back.
PAYOUT_REMOVED = {k: v for k, v in dict(
    PAYOUT_ITEM, status="canceled", released_reference="wd-1001", cancel_reason="removed",
    canceled_by="5e1a9c3d7b2f40e8a6c4d2b1", canceled_at="2026-10-20T09:40:00Z",
    members={"5e1a9c3d7b2f40e8a6c4d2b1": "finance@acme.example"}, webhook_url="https://acme.example/withdrawals/ipn",
    updated_at="2026-10-20T09:40:00Z").items() if k not in ("batch_id", "reference")}
# A withdrawal payitgo gave up: its money stays in the wallet.
WITHDRAWAL_FAILED = {
    "id": "1d3f5a7c9e2b4d6f8a0c2e4b", "merchant_id": "3f8a1c27b94e05d6a2c71e90", "livemode": False,
    "asset": "USDT_TRC20", "chain": "tron", "amount": "377.797583", "fee": "3.71", "send_amount": "374.087583",
    "address_id": "3e7a1c9f5b2d4e8a6c0f2b1d", "address": "TWd4Kp9mR2xH7sN3vB6cJ8fL5qY1tZgEaU", "status": "failed",
    "fail_reason": "it could not be sent: its money stays in the payout wallet",
    "requested_by": "9b2e4c71d8a05f3e6c1a7b24", "members": {"9b2e4c71d8a05f3e6c1a7b24": "owner@acme.example"},
    "created_at": "2026-10-20T11:02:15.33Z", "updated_at": "2026-10-20T11:20:40.1Z",
}


class PayoutsTest(_GatewayTest):
    def check(self, i, method, path, body=None):
        req = self.request(i)
        self.assertEqual((req["method"], req["path"], req["body"]), (method, path, body))
        self.assertEqual(req["headers"]["Authorization"], "Bearer " + KEY)

    def test_request(self):
        self.reply(201, {"item": PAYOUT_ITEM})
        item = self.client.payouts.request("USDT_BEP20", PAYOUT_ITEM["address"], decimal.Decimal("25.50"),
                                           reference="wd-1001", name="Ana Silva", note="withdrawal 1001")
        body = {"asset": "USDT_BEP20", "address": PAYOUT_ITEM["address"], "amount": "25.50", "reference": "wd-1001",
                "name": "Ana Silva", "note": "withdrawal 1001"}
        self.check(0, "POST", "/v1/payouts", body)
        self.assertEqual(self.request()["headers"]["Content-Type"], "application/json")
        self.assertEqual(item, PAYOUT_ITEM)
        self.assertEqual((item.status, item.debit, item.recipient.name), ("ready", "26.01183421", "Ana Silva"))
        # Sent again (a retry after a timeout): the payment its reference names, 200 instead of 201.
        self.reply(200, {"item": PAYOUT_ITEM})
        again = self.client.payouts.request("USDT_BEP20", PAYOUT_ITEM["address"], decimal.Decimal("25.50"),
                                            reference="wd-1001", name="Ana Silva", note="withdrawal 1001")
        self.check(1, "POST", "/v1/payouts", body)
        self.assertEqual(again, item)
        # Only what is given is sent; a tag goes as a number.
        self.reply(201, {"item": PAYOUT_ITEM})
        self.client.payouts.request("XRP", "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe", 20, tag=1042)
        self.assertEqual(self.request(2)["body"],
                         {"asset": "XRP", "address": "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe", "amount": "20", "tag": 1042})

    def test_request_webhook_url(self):
        own = dict(PAYOUT_ITEM, webhook_url="https://acme.example/withdrawals/ipn")
        self.reply(201, {"item": own})
        item = self.client.payouts.request("USDT_BEP20", PAYOUT_ITEM["address"], "25.5", "wd-1001",
                                           webhook_url="https://acme.example/withdrawals/ipn")
        self.check(0, "POST", "/v1/payouts", {"asset": "USDT_BEP20", "address": PAYOUT_ITEM["address"], "amount": "25.5",
                                              "reference": "wd-1001",
                                              "webhook_url": "https://acme.example/withdrawals/ipn"})
        self.assertEqual(item.webhook_url, "https://acme.example/withdrawals/ipn")
        self.assertNotIn("webhook_url", PAYOUT_ITEM)  # absent when it has none
        # The client's webhook_url is for payments, not payout requests.
        client = self.make_client(webhook_url="https://acme.example/orders/ipn")
        self.reply(201, {"item": PAYOUT_ITEM})
        client.payouts.request("USDT_BEP20", PAYOUT_ITEM["address"], "25.5", "wd-1001")
        self.assertNotIn("webhook_url", self.request(1)["body"])
        # A batch row takes its own too.
        self.reply(201, {"batch": PAYOUT_BATCH})
        self.client.payout_batches.create([dict(ROW, webhook_url="https://acme.example/withdrawals/ipn")])
        self.check(2, "POST", "/v1/payout-batches",
                   {"items": [dict(SENT_ROW, webhook_url="https://acme.example/withdrawals/ipn")]})
        self.reply(400, {"error": "payments may name at most 10 different webhook_urls in one call"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payout_batches.create([dict(ROW, webhook_url="https://h%d.example/ipn" % i) for i in range(11)])
        self.assertEqual(ctx.exception.status, 400)

    def test_request_invalid(self):
        # An invalid request is kept and says why: no error is raised.
        self.reply(201, {"item": PAYOUT_INVALID})
        item = self.client.payouts.request("USDT_BEP20", PAYOUT_INVALID["address"], "25.5", "wd-1002")
        self.assertEqual(item.status, "invalid")
        self.assertEqual(item.errors[0].code, "wrong_network")
        self.assertEqual(self.request()["body"]["reference"], "wd-1002")

    def test_retrieve(self):
        self.reply(200, {"item": PAYOUT_ITEM})
        self.assertEqual(self.client.payouts.retrieve(PAYOUT_ITEM["id"]), PAYOUT_ITEM)
        self.check(0, "GET", "/v1/payouts/" + PAYOUT_ITEM["id"])
        self.reply(200, {"item": PAYOUT_ITEM})
        self.assertEqual(self.client.payouts.retrieve_by_reference("wd 1001/a&b"), PAYOUT_ITEM)
        self.check(1, "GET", "/v1/payouts?reference=wd+1001%2Fa%26b")
        self.reply(200, {"item": PAYOUT_ITEM})
        self.client.payouts.retrieve("a/b")  # escaped in the path
        self.assertEqual(self.request(2)["path"], "/v1/payouts/a%2Fb")
        # No reference would list every payment: refused before any request is made.
        for empty in ("", None):
            with self.assertRaisesRegex(ValueError, "reference is required"):
                self.client.payouts.retrieve_by_reference(empty)
        self.assertEqual(len(self.server.requests), 3)

    def test_inbox(self):
        self.reply(200, INBOX)
        page = self.client.payouts.inbox(status=["ready", "invalid"], limit=10, offset=0)
        self.check(0, "GET", "/v1/payouts/inbox?status=ready,invalid&limit=10&offset=0")
        self.assertEqual(page, INBOX)
        self.assertTrue(page.inbox.open and page.inbox.inbox)
        self.assertEqual(page.coverage[0].top_up, "16.01183421")
        self.assertEqual(page["items"][0].reference, "wd-1001")  # page.items is the dict's method
        self.reply(200, INBOX)
        self.client.payouts.inbox()
        self.assertEqual(self.request(1)["path"], "/v1/payouts/inbox")
        self.reply(200, INBOX)
        self.client.payouts.inbox(status="ready")
        self.assertEqual(self.request(2)["path"], "/v1/payouts/inbox?status=ready")

    def test_send(self):
        self.reply(200, {"batch": AWAITING})
        batch = self.client.payouts.send()
        self.check(0, "POST", "/v1/payouts/send")  # no body: every valid request
        self.assertEqual(batch, AWAITING)
        self.assertEqual(batch.waiting.shortfall[0].amount, "26.01183421")
        self.reply(200, {"batch": AWAITING})
        self.client.payouts.send(["4e6a8c0b2d4f6a1c3e5b7d9a", "5f7b9d1c3e5a7b9d1f3a5c7e"])
        self.check(1, "POST", "/v1/payouts/send", {"ids": ["4e6a8c0b2d4f6a1c3e5b7d9a", "5f7b9d1c3e5a7b9d1f3a5c7e"]})
        self.reply(200, {"batch": AWAITING})
        self.client.payouts.send("4e6a8c0b2d4f6a1c3e5b7d9a")  # one id
        self.check(2, "POST", "/v1/payouts/send", {"ids": ["4e6a8c0b2d4f6a1c3e5b7d9a"]})
        # An empty list would send every request: refused before any request is made.
        with self.assertRaisesRegex(ValueError, "ids is empty"):
            self.client.payouts.send([])
        self.assertEqual(len(self.server.requests), 3)

    def test_send_nothing(self):
        self.reply(400, {"error": "no valid payout request to send"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payouts.send()
        self.assertEqual((ctx.exception.status, ctx.exception.message), (400, "no valid payout request to send"))

    def test_cancel(self):
        self.reply(200, {"item": PAYOUT_REMOVED})
        item = self.client.payouts.cancel(PAYOUT_ITEM["id"])
        self.check(0, "DELETE", "/v1/payouts/" + PAYOUT_ITEM["id"])
        self.assertEqual((item.status, item.cancel_reason, item.released_reference), ("canceled", "removed", "wd-1001"))
        self.assertEqual(item.members[item.canceled_by], "finance@acme.example")
        self.assertNotIn("batch_id", item)  # out of the list, record kept
        self.assertNotIn("reference", item)
        # Again (a retry): the same payment, as it is.
        self.reply(200, {"item": PAYOUT_REMOVED})
        self.assertEqual(self.client.payouts.cancel(PAYOUT_ITEM["id"]), item)
        self.check(1, "DELETE", "/v1/payouts/" + PAYOUT_ITEM["id"])
        # Its reference given back still finds it.
        self.reply(200, {"item": PAYOUT_REMOVED})
        self.assertEqual(self.client.payouts.retrieve_by_reference("wd-1001").cancel_reason, "removed")
        self.check(2, "GET", "/v1/payouts?reference=wd-1001")

    def test_remove(self):
        sent = "5f7b9d1c3e5a7b9d1f3a5c7e"
        conflict = "not in your list of payout requests (sent already): cancel it with DELETE /v1/payouts/" + sent
        results = [{"id": PAYOUT_ITEM["id"], "item": PAYOUT_REMOVED},
                   {"id": sent, "error": {"status": 409, "message": conflict}},
                   {"id": "000000000000000000000000", "error": {"status": 404, "message": "not found"}}]
        self.reply(200, {"results": results})
        got = self.client.payouts.remove([PAYOUT_ITEM["id"], sent, "000000000000000000000000"])
        self.check(0, "POST", "/v1/payouts/remove", {"ids": [PAYOUT_ITEM["id"], sent, "000000000000000000000000"]})
        self.assertEqual(self.request()["headers"]["Content-Type"], "application/json")
        self.assertEqual(got, results)  # the list itself, one result per id in the order given
        self.assertEqual((got[0].id, got[0].item.cancel_reason, got[0].item.released_reference),
                         (PAYOUT_ITEM["id"], "removed", "wd-1001"))
        self.assertNotIn("batch_id", got[0].item)
        self.assertNotIn("error", got[0])
        self.assertEqual((got[1].error.status, got[1].error.message), (409, conflict))
        self.assertNotIn("item", got[1])
        self.assertEqual(got[2].error.status, 404)
        # One id, as a str; a tuple works too.
        self.reply(200, {"results": results[:1]})
        self.client.payouts.remove(PAYOUT_ITEM["id"])
        self.check(1, "POST", "/v1/payouts/remove", {"ids": [PAYOUT_ITEM["id"]]})
        self.reply(200, {"results": results[:1]})
        self.client.payouts.remove((PAYOUT_ITEM["id"],))
        self.check(2, "POST", "/v1/payouts/remove", {"ids": [PAYOUT_ITEM["id"]]})
        # No ids: refused before any request is made.
        for empty in ([], (), None):
            with self.assertRaisesRegex(ValueError, "ids is empty"):
                self.client.payouts.remove(empty)
        self.assertEqual(len(self.server.requests), 3)

    def test_remove_too_many(self):
        self.reply(400, {"error": "remove between 1 and 1000 payout requests at a time"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payouts.remove(["%024d" % i for i in range(1001)])
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (400, "remove between 1 and 1000 payout requests at a time"))

    def test_list(self):
        page = {"items": [PAYOUT_REMOVED, dict(PAYOUT_ITEM, status="sent", tx_id="ab" * 32)], "has_more": True,
                "next_cursor": "MjAyNi0xMC0yMFQwOTo0MDowMFp8NGU2YThjMGIyZDRmNmExYzNlNWI3ZDlh"}
        self.reply(200, page)
        result = self.client.payouts.list(status=["sent", "failed", "canceled", "expired"],
                                          updated_since=datetime.datetime(2026, 10, 20, 9, 0), limit=200)
        # A naive datetime is taken as UTC, sent as RFC 3339.
        self.check(0, "GET", "/v1/payouts?status=sent,failed,canceled,expired"
                             "&updated_since=2026-10-20T09%3A00%3A00Z&limit=200")
        self.assertEqual(result, page)
        self.assertEqual((result["items"][0].cancel_reason, result["items"][1].status), ("removed", "sent"))
        self.assertTrue(result.has_more)
        # Read on with the cursor; an aware datetime is sent in UTC.
        self.reply(200, {"items": [], "has_more": False})
        result = self.client.payouts.list(
            cursor=page["next_cursor"],
            updated_since=datetime.datetime(2026, 10, 20, 11, 0, 0, 250000,
                                            tzinfo=datetime.timezone(datetime.timedelta(hours=2))))
        self.check(1, "GET", "/v1/payouts?updated_since=2026-10-20T09%3A00%3A00.250000Z&cursor=" + page["next_cursor"])
        self.assertEqual((result["items"], result.has_more), ([], False))
        self.assertIsNone(result.get("next_cursor"))  # absent from an empty page
        # A str is sent as given; one status as a str; nothing set, no query.
        self.reply(200, page)
        self.client.payouts.list(status="canceled", updated_since="2026-10-20T09:00:00+02:00")
        self.check(2, "GET", "/v1/payouts?status=canceled&updated_since=2026-10-20T09%3A00%3A00%2B02%3A00")
        self.reply(200, page)
        self.client.payouts.list()
        self.check(3, "GET", "/v1/payouts")

    def test_list_bad_cursor(self):
        self.reply(400, {"error": "cursor is not one this API gave"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payouts.list(cursor="nope")
        self.assertEqual((ctx.exception.status, ctx.exception.message), (400, "cursor is not one this API gave"))

    def test_events_list(self):
        events = [{"id": "9b1e3c5a7d2f4b6e8a0c1d3f", "type": "payout_item.canceled", "livemode": False,
                   "created_at": "2026-10-20T09:40:00.312Z", "data": PAYOUT_REMOVED},
                  {"id": "9b1e3c5a7d2f4b6e8a0c1d40", "type": "payout_wallet.withdrawal_failed", "livemode": False,
                   "created_at": "2026-10-20T11:20:40.2Z", "data": WITHDRAWAL_FAILED}]
        self.reply(200, {"events": events, "has_more": True})
        page = self.client.events.list(type=["payout_item.*", "payout_wallet.withdrawal_failed"],
                                       after="4f1c2a9e7b3d5f80a2c4e6b8", limit=2)
        self.check(0, "GET", "/v1/events?type=payout_item.%2A,payout_wallet.withdrawal_failed"
                             "&after=4f1c2a9e7b3d5f80a2c4e6b8&limit=2")
        self.assertEqual(page, {"events": events, "has_more": True})
        self.assertTrue(page.has_more)
        self.assertEqual((page.events[0].type, page.events[0].data.cancel_reason), ("payout_item.canceled", "removed"))
        self.assertEqual(page.events[1].data.fail_reason, WITHDRAWAL_FAILED["fail_reason"])
        # From a time: a datetime in UTC; one type as a str; nothing set, no query.
        self.reply(200, {"events": [], "has_more": False})
        self.client.events.list(type="payout_item.sent", created_after=datetime.datetime(2026, 10, 20, 9, 0))
        self.check(1, "GET", "/v1/events?type=payout_item.sent&created_after=2026-10-20T09%3A00%3A00Z")
        self.reply(200, {"events": [], "has_more": False})
        self.client.events.list(created_after="2026-10-20T09:00:00Z")
        self.check(2, "GET", "/v1/events?created_after=2026-10-20T09%3A00%3A00Z")
        self.reply(200, {"events": [], "has_more": False})
        self.assertEqual(self.client.events.list().events, [])
        self.check(3, "GET", "/v1/events")

    def test_events_list_storefront(self):
        message = "a storefront's key cannot read the account's events: use an account key"
        self.reply(403, {"error": message})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.events.list()
        self.assertEqual((ctx.exception.status, ctx.exception.message), (403, message))

    def test_quote(self):
        quote = {"quote": {"livemode": False, "fee_paid_by": "recipient", "totals": TOTALS, "total_usd": "25.5",
                           "counts": COUNTS},
                 "items": [{k: v for k, v in PAYOUT_ITEM.items() if k not in ("id", "merchant_id", "batch_id")}]}
        self.reply(200, quote)
        result = self.client.payouts.quote([ROW], fee_paid_by="recipient")
        self.check(0, "POST", "/v1/payout-quotes", {"items": [SENT_ROW], "fee_paid_by": "recipient"})
        self.assertEqual(result, quote)
        self.assertEqual(result.quote.totals[0].fee, "0.51183421")
        self.assertNotIn("id", result["items"][0])
        self.reply(200, quote)
        self.client.payouts.quote([{"asset": "TRX", "address": "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7", "amount": 100}])
        self.assertEqual(self.request(1)["body"],
                         {"items": [{"asset": "TRX", "address": "TLa2f6VPqDgRE67v1736s7bJ8Ray5wYjU7", "amount": "100"}]})

    def test_forbidden_key(self):
        message = "this api key cannot make payouts: turn payouts on for it in the dashboard"
        self.reply(403, {"error": message})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payouts.request("USDT_BEP20", PAYOUT_ITEM["address"], "25.5")
        self.assertEqual((ctx.exception.status, ctx.exception.message), (403, message))

    def test_batch_create(self):
        self.reply(201, {"batch": PAYOUT_BATCH})
        batch = self.client.payout_batches.create([ROW], "payroll-2026-10", name="October payroll",
                                                  webhook_url="https://acme.example/payouts/ipn")
        self.check(0, "POST", "/v1/payout-batches", {"items": [SENT_ROW], "reference": "payroll-2026-10",
                                                     "name": "October payroll",
                                                     "webhook_url": "https://acme.example/payouts/ipn"})
        self.assertEqual(batch, PAYOUT_BATCH)
        self.assertEqual((batch.status, batch.counts.ready, batch.totals[0].debit), ("quoted", 1, "26.01183421"))
        # The same reference again: that batch, 200 instead of 201.
        self.reply(200, {"batch": PAYOUT_BATCH})
        self.assertEqual(self.client.payout_batches.create([ROW], "payroll-2026-10", fee_paid_by="merchant"),
                         PAYOUT_BATCH)
        self.check(1, "POST", "/v1/payout-batches", {"items": [SENT_ROW], "reference": "payroll-2026-10",
                                                     "fee_paid_by": "merchant"})
        # The client's webhook_url is for payments, not batches.
        client = self.make_client(webhook_url="https://acme.example/orders/ipn")
        self.reply(201, {"batch": PAYOUT_BATCH})
        client.payout_batches.create([ROW])
        self.check(2, "POST", "/v1/payout-batches", {"items": [SENT_ROW]})

    def test_batch_list(self):
        page = {"batches": [PAYOUT_BATCH], "total": 1, "limit": 20, "offset": 40}
        self.reply(200, page)
        self.assertEqual(self.client.payout_batches.list(status="awaiting_funds", limit=20, offset=40), page)
        self.check(0, "GET", "/v1/payout-batches?status=awaiting_funds&limit=20&offset=40")
        self.reply(200, page)
        result = self.client.payout_batches.list()
        self.check(1, "GET", "/v1/payout-batches")
        self.assertEqual((result.batches[0].id, result.total), (PAYOUT_BATCH["id"], 1))

    def test_batch_retrieve(self):
        self.reply(200, {"batch": PAYOUT_BATCH})
        self.assertEqual(self.client.payout_batches.retrieve(PAYOUT_BATCH["id"]), PAYOUT_BATCH)
        self.check(0, "GET", "/v1/payout-batches/" + PAYOUT_BATCH["id"])

    def test_batch_items(self):
        page = {"items": [PAYOUT_INVALID], "total": 1, "limit": 50, "offset": 0}
        self.reply(200, page)
        result = self.client.payout_batches.items(PAYOUT_BATCH["id"], status=("sent", "failed"), limit=50)
        self.check(0, "GET", "/v1/payout-batches/%s/items?status=sent,failed&limit=50" % PAYOUT_BATCH["id"])
        self.assertEqual(result, page)
        self.assertEqual(result["items"][0].errors[0].field, "address")
        self.reply(200, page)
        self.client.payout_batches.items(PAYOUT_BATCH["id"], status="invalid")
        self.check(1, "GET", "/v1/payout-batches/%s/items?status=invalid" % PAYOUT_BATCH["id"])
        self.reply(200, page)
        self.client.payout_batches.items(PAYOUT_BATCH["id"])
        self.check(2, "GET", "/v1/payout-batches/%s/items" % PAYOUT_BATCH["id"])

    def test_batch_add_items(self):
        self.reply(200, {"batch": dict(PAYOUT_BATCH, counts=dict(COUNTS, total=2, ready=2))})
        batch = self.client.payout_batches.add_items(PAYOUT_BATCH["id"], [ROW])
        self.check(0, "POST", "/v1/payout-batches/%s/items" % PAYOUT_BATCH["id"], {"items": [SENT_ROW]})
        self.assertEqual(batch.counts.total, 2)

    def test_batch_actions(self):
        for i, (action, reply) in enumerate([("quote", PAYOUT_BATCH), ("submit", AWAITING),
                                             ("cancel", dict(PAYOUT_BATCH, status="canceled"))]):
            self.reply(200, {"batch": reply})
            batch = getattr(self.client.payout_batches, action)(PAYOUT_BATCH["id"])
            self.check(i, "POST", "/v1/payout-batches/%s/%s" % (PAYOUT_BATCH["id"], action))
            self.assertEqual(batch, reply)

    def test_batch_submit_expired(self):
        self.reply(409, {"error": "its prices expired: get a new quote first"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payout_batches.submit(PAYOUT_BATCH["id"])
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (409, "its prices expired: get a new quote first"))

    def test_wallets(self):
        self.reply(200, {"wallets": WALLETS})
        wallets = self.client.payout_wallets.list()
        self.check(0, "GET", "/v1/payout-wallets")
        self.assertEqual(wallets, WALLETS)
        self.assertEqual((wallets[1].chain, wallets[1].assets[0].needed), ("bsc", "16.01183421"))

    def test_simulate_top_up(self):
        self.reply(200, {"wallets": WALLETS})
        wallets = self.client.payout_wallets.simulate_top_up("USDT_TRC20", decimal.Decimal("1000"))
        self.check(0, "POST", "/v1/payout-wallets/USDT_TRC20/simulate-top-up", {"amount": "1000"})
        self.assertEqual(wallets, WALLETS)
        self.reply(400, {"error": "only a test wallet is topped up on paper: use a test key"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payout_wallets.simulate_top_up("USDT_TRC20", "1000")
        self.assertEqual(ctx.exception.status, 400)

    def test_events(self):
        for event in ("payout_batch.quoted", "payout_batch.pending_approval", "payout_batch.approved",
                      "payout_batch.rejected", "payout_batch.awaiting_funds", "payout_batch.processing",
                      "payout_batch.completed", "payout_batch.canceled", "payout_item.processing",
                      "payout_item.returned", "payout_item.sent", "payout_item.failed", "payout_item.canceled",
                      "payout_wallet.withdrawn", "payout_wallet.withdrawal_failed"):
            self.assertIn(event, payitgo.WEBHOOK_EVENTS)
        # In the API's order, ping last.
        self.assertEqual(payitgo.WEBHOOK_EVENTS[payitgo.WEBHOOK_EVENTS.index("payout_batch.canceled"):], (
            "payout_batch.canceled", "payout_item.processing", "payout_item.returned", "payout_item.sent",
            "payout_item.failed", "payout_item.canceled", "payout_item.claimed", "payout_item.claim_expired",
            "payout_wallet.withdrawn", "payout_wallet.withdrawal_failed", "payout_schedule.run",
            "payout_schedule.funding_needed", "ping"))
        self.assertEqual(len(payitgo.WEBHOOK_EVENTS), len(set(payitgo.WEBHOOK_EVENTS)))
        body = json.dumps({"id": "evt_9", "type": "payout_item.sent", "livemode": False,
                           "created_at": "2026-10-20T09:31:12Z",
                           "data": dict(PAYOUT_ITEM, status="sent", tx_id="ab" * 32)}).encode()
        event = payitgo.Webhook.construct_event(body, sign(SECRET, int(time.time()), body), SECRET)
        self.assertEqual((event.type, event.data.reference, event.data.status), ("payout_item.sent", "wd-1001", "sent"))

    def test_request_claim_link(self):
        self.reply(201, {"item": dict(CLAIMABLE, status="ready")})
        item = self.client.payouts.request("USDT_TRC20", amount="50", reference="prize-7", claim=True,
                                           email="winner@example.com", claim_assets=["USDT_BEP20", "USDC_BEP20"])
        # No address: the recipient chooses one.
        self.check(0, "POST", "/v1/payouts", {"asset": "USDT_TRC20", "amount": "50", "reference": "prize-7",
                                              "claim": True, "email": "winner@example.com",
                                              "claim_assets": ["USDT_BEP20", "USDC_BEP20"]})
        self.assertEqual((item.address, item.claim.email, item.claim.assets),
                         ("", "winner@example.com", ["USDT_BEP20", "USDC_BEP20"]))
        # A link to share yourself: claim=True, no email.
        self.reply(201, {"item": dict(CLAIMABLE, status="ready")})
        self.client.payouts.request(asset="USDT_TRC20", amount=decimal.Decimal("50"), claim=True)
        self.check(1, "POST", "/v1/payouts", {"asset": "USDT_TRC20", "amount": "50", "claim": True})
        # An email alone is no claim link: sent as given, the gateway finds no address.
        missing = dict(PAYOUT_INVALID, address="", recipient={"email": "winner@example.com"},
                       errors=[{"code": "invalid_address", "field": "address", "message": "address is required"}])
        self.reply(201, {"item": missing})
        item = self.client.payouts.request(asset="USDT_TRC20", amount="50", email="winner@example.com")
        self.check(2, "POST", "/v1/payouts", {"asset": "USDT_TRC20", "amount": "50", "email": "winner@example.com"})
        self.assertEqual((item.status, item.errors[0].code), ("invalid", "invalid_address"))
        self.assertNotIn("claim", item)
        # The amount is required: refused before any request is made.
        with self.assertRaisesRegex(TypeError, "amount"):
            self.client.payouts.request("USDT_TRC20", claim=True)
        self.assertEqual(len(self.server.requests), 3)

    def test_claim_fields(self):
        self.reply(200, {"item": CLAIMABLE})
        item = self.client.payouts.retrieve_by_reference("prize-7")
        self.assertEqual(item, CLAIMABLE)
        self.assertEqual((item.status, item.claim.url, item.claim.expires_at, item.claim.emailed),
                         ("claimable", CLAIMABLE["claim"]["url"], "2026-11-03T09:20:00Z", 1))
        self.assertNotIn("claimed_at", item.claim)
        self.reply(200, {"item": CLAIMED})
        item = self.client.payouts.retrieve(CLAIMED["id"])
        self.assertEqual((item.asset, item.address, item.claim.funded_asset, item.claim.funded_amount),
                         ("USDT_BEP20", PAYOUT_ITEM["address"], "USDT_TRC20", "52.31"))

    def test_batch_claims_and_conversion(self):
        rows = [{"reference": "prize-7", "asset": "USDT_TRC20", "amount": "50", "email": "winner@example.com",
                 "claim": True, "claim_assets": ["USDT_BEP20"], "address": None},
                {"reference": "payroll-2026-10-dan", "asset": "BTC", "address": CONVERTED["address"],
                 "amount": decimal.Decimal("0.001")}]
        batch = dict(PAYOUT_BATCH, convert="convert", convert_from="USDT_TRC20",
                     counts=dict(COUNTS, total=2, ready=2), quote_expires_at="2026-10-20T09:30:02.41Z")
        self.reply(201, {"batch": batch})
        result = self.client.payout_batches.create(rows, "prizes-2026-10", convert="convert", convert_from="USDT_TRC20")
        self.check(0, "POST", "/v1/payout-batches", {
            "items": [{"reference": "prize-7", "asset": "USDT_TRC20", "amount": "50", "email": "winner@example.com",
                       "claim": True, "claim_assets": ["USDT_BEP20"]},
                      {"reference": "payroll-2026-10-dan", "asset": "BTC", "address": CONVERTED["address"],
                       "amount": "0.001"}],
            "reference": "prizes-2026-10", "convert": "convert", "convert_from": "USDT_TRC20"})
        self.assertEqual((result.convert, result.convert_from, result.counts.claimable, result.counts.expired),
                         ("convert", "USDT_TRC20", 0, 0))
        page = {"items": [CONVERTED], "total": 1, "limit": 50, "offset": 0}
        self.reply(200, page)
        item = self.client.payout_batches.items(result.id)["items"][0]
        self.assertEqual((item.asset, item.fee, item.convert.to, item.convert.to_amount,
                          item.convert.estimate), ("USDT_TRC20", "4.442417", "BTC", "0.001", "0.00100502"))
        self.assertNotIn("amount_out", item.convert)  # once sent

    def test_quote_convert(self):
        quote = {"quote": {"livemode": False, "fee_paid_by": "merchant", "totals": TOTALS, "total_usd": "69.44",
                           "counts": COUNTS},
                 "items": [{k: v for k, v in CONVERTED.items() if k not in ("id", "merchant_id", "batch_id")}]}
        self.reply(200, quote)
        result = self.client.payouts.quote([{"asset": "BTC", "address": CONVERTED["address"], "amount": "0.001"}],
                                           convert="convert", convert_from="USDT_TRC20")
        self.check(0, "POST", "/v1/payout-quotes", {
            "items": [{"asset": "BTC", "address": CONVERTED["address"], "amount": "0.001"}],
            "convert": "convert", "convert_from": "USDT_TRC20"})
        self.assertEqual(result["items"][0].convert.estimate, "0.00100502")
        self.reply(200, quote)
        self.client.payouts.quote([ROW], convert="same_network")
        self.assertEqual(self.request(1)["body"], {"items": [SENT_ROW], "convert": "same_network"})

    def test_new_events(self):
        for event in ("payout_item.claimed", "payout_item.claim_expired", "payout_schedule.run",
                      "payout_schedule.funding_needed"):
            self.assertIn(event, payitgo.WEBHOOK_EVENTS)
        # A summary: no items (its rows stay in the dashboard).
        schedule = {"id": "2c4e6a8c0e2b4d6f8a0c2e4a", "livemode": False, "name": "Monthly payroll",
                    "status": "active", "cadence": "monthly", "weekday": 0, "day": -1, "at": "09:00",
                    "timezone": "Africa/Harare", "fee_paid_by": "merchant", "auto_submit": True, "remind_days": 3,
                    "totals": [{"asset": "USDT_TRC20", "ticker": "USDT", "items": 12, "amount": "18000"}],
                    "next_run_at": "2026-10-31T07:00:00Z", "last_run_at": "2026-09-30T07:00:00Z",
                    "last_batch_id": PAYOUT_BATCH["id"], "last_error": "", "runs": 4}
        shortfall = [{"asset": "USDT_TRC20", "network": "Tron (TRC-20)", "amount": "1520.5",
                      "address": "TMkq7dZ3vN8xR2pB6cH9sJ4fL1wY5tGaQe"}]
        for type_, data in (("payout_schedule.run", schedule),
                            ("payout_schedule.funding_needed", {"schedule": schedule, "shortfall": shortfall}),
                            ("payout_item.claim_expired", dict(CLAIMABLE, status="expired"))):
            body = json.dumps({"id": "evt_" + type_, "type": type_, "livemode": False,
                               "created_at": "2026-10-28T07:00:00Z", "data": data}).encode()
            event = payitgo.Webhook.construct_event(body, sign(SECRET, int(time.time()), body), SECRET)
            self.assertEqual((event.type, event.data), (type_, data))
            if type_ == "payout_schedule.funding_needed":
                self.assertEqual((event.data.schedule.totals[0].amount, event.data.shortfall[0].amount),
                                 ("18000", "1520.5"))
                self.assertNotIn("items", event.data.schedule)
        self.assertEqual((event.data.claim.url, event.data.status), (CLAIMABLE["claim"]["url"], "expired"))

    def test_round_trip_events(self):
        processing = dict(PAYOUT_ITEM, batch_id=PAYOUT_BATCH["id"])  # into a batch of its own
        returned = dict(PAYOUT_INVALID, errors=[{"code": "below_minimum", "field": "amount",
                                                 "message": "the amount is under the minimum right now"}])
        for type_, data in (("payout_item.processing", processing), ("payout_item.returned", returned),
                            ("payout_item.canceled", PAYOUT_REMOVED),
                            ("payout_wallet.withdrawal_failed", WITHDRAWAL_FAILED)):
            self.assertIn(type_, payitgo.WEBHOOK_EVENTS)
            body = json.dumps({"id": "evt_" + type_, "type": type_, "livemode": False,
                               "created_at": "2026-10-20T09:40:00Z", "data": data}).encode()
            event = payitgo.Webhook.construct_event(body, sign(SECRET, int(time.time()), body), SECRET)
            self.assertEqual((event.type, event.data), (type_, data))
        self.assertEqual((event.data.status, event.data.fail_reason),
                         ("failed", "it could not be sent: its money stays in the payout wallet"))


class ClientConstructEventTest(unittest.TestCase):
    def test_client_secret(self):
        client = payitgo.Client(KEY, base_url="http://x", webhook_secret=SECRET)
        self.assertEqual(client.webhooks.construct_event(BODY, HEADER, tolerance=0).id, "b8e1f80bd70ae0784c7855a4")
        fresh = sign(SECRET, int(time.time()), BODY)
        self.assertEqual(client.webhooks.construct_event(BODY, fresh).id, "b8e1f80bd70ae0784c7855a4")

    def test_explicit_secret_wins(self):
        client = payitgo.Client(KEY, base_url="http://x", webhook_secret=OTHER_SECRET)
        with self.assertRaises(payitgo.SignatureVerificationError):
            client.webhooks.construct_event(BODY, HEADER, tolerance=0)
        self.assertEqual(client.webhooks.construct_event(BODY, HEADER, SECRET, tolerance=0).id, "b8e1f80bd70ae0784c7855a4")

    def test_no_secret(self):
        client = payitgo.Client(KEY, base_url="http://x")
        with self.assertRaisesRegex(payitgo.SignatureVerificationError, "no webhook secret"):
            client.webhooks.construct_event(BODY, HEADER)


if __name__ == "__main__":
    unittest.main()
