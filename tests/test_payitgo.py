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

# Signed by the gateway's own signer.
SECRET = "whsec_test_secret"
HEADER = "t=1760000000,v1=6abd292fdf2ecc51779c161f5d4be2d47236f7a43fbaa9dd7093303904b91a7b"
BODY = b'{"id":"evt_test_123","type":"payment.confirmed","livemode":false,"created_at":"2026-10-09T12:00:00Z","data":{"id":"pay_123","order_id":"ORD-1","status":"confirmed","usd_amount":"10"}}'


def sign(secret, t, body):
    return "t=%d,v1=%s" % (t, hmac.new(secret.encode(), str(t).encode() + b"." + body, hashlib.sha256).hexdigest())


class WebhookTest(unittest.TestCase):
    def test_accepts_gateway_signature(self):
        event = payitgo.Webhook.construct_event(BODY, HEADER, SECRET, tolerance=0)
        self.assertEqual(event.type, "payment.confirmed")
        self.assertEqual(event.data.order_id, "ORD-1")
        self.assertFalse(event.livemode)

    def test_accepts_str_body(self):
        self.assertEqual(payitgo.Webhook.construct_event(BODY.decode(), HEADER, SECRET, tolerance=0)["id"], "evt_test_123")

    def test_rejects(self):
        for body, header, secret in [
            (BODY.replace(b'"10"', b'"1000"'), HEADER, SECRET),
            (BODY, HEADER, "whsec_other"),
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
        self.assertEqual(payitgo.Webhook.construct_event(BODY, header, SECRET, tolerance=0).id, "evt_test_123")

    def test_tolerance(self):
        with self.assertRaises(payitgo.SignatureVerificationError):
            payitgo.Webhook.construct_event(BODY, HEADER, SECRET)
        fresh = sign(SECRET, int(time.time()), BODY)
        self.assertEqual(payitgo.Webhook.construct_event(BODY, fresh, SECRET).id, "evt_test_123")


class ClientTest(unittest.TestCase):
    def test_checks(self):
        os.environ.pop("PAYITGO_BASE_URL", None)
        with self.assertRaises(ValueError):
            payitgo.Client("pk_123", base_url="http://x")
        # Without an address the client uses the release's default, or refuses.
        if payitgo.DEFAULT_BASE_URL:
            self.assertEqual(payitgo.Client("sk_test_abc")._base_url, payitgo.DEFAULT_BASE_URL.rstrip("/"))
        else:
            with self.assertRaises(ValueError):
                payitgo.Client("sk_test_abc")
        self.assertFalse(payitgo.Client("sk_test_abc", base_url="http://x").livemode)
        self.assertTrue(payitgo.Client("sk_live_abc", base_url="http://x").livemode)


WEBHOOK = {
    "livemode": False, "url": "https://shop.example/payitgo/ipn", "secret": "whsec_" + "ab" * 32,
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
    "destination_tag": 1042, "crypto_amount": "20", "received_amount": "0", "usd_amount": "10",
    "amount_due_usd": "10", "fees_paid_by_customer": False, "network_fee": "0.000012", "network_fee_usd": "0.01",
    "settlement": "custodial", "received_usd": "0", "rate_locked": "0.5", "deposits": [],
    "expires_at": "2026-10-10T12:15:00Z", "created_at": "2026-10-10T12:00:00Z",
    "network_fee_note": "Near-zero fees. Include destination tag — required.",
    "qr_payload": "rPT1Sjq2YGrBMTttX4GZHjKu9dyfzbpAYe",
    "checkout_token": "ct_abc", "checkout_url": "https://pay.example/ct_abc", "description": "Order 7",
    "metadata": {"cart": "42"},
}

# A test payment whose simulated outcome has paid it.
SIMULATED = {
    "id": "pay_2", "merchant_id": "mch_1", "order_id": "ORD-8", "status": "detecting", "livemode": False,
    "simulation": {"outcome": "confirmed", "state": "running"},
    "token": "USDT_TRC20", "chain": "tron", "deposit_address": "TPk2bpQqWRpXiaUHS9SXnZCDgvwrsuJ71o",
    "crypto_amount": "20", "received_amount": "20", "usd_amount": "20", "amount_due_usd": "20",
    "fees_paid_by_customer": False, "network_fee": "1", "network_fee_usd": "0.3", "settlement": "custodial",
    "received_usd": "20", "rate_locked": "1",
    "deposits": [{
        "tx_id": "sim_pay_2_1791817211348_0", "token": "USDT_TRC20", "chain": "tron", "amount": "20",
        "usd_value": "20", "counted": True, "status": "pending", "seen_at": "2026-10-10T12:00:05Z", "simulated": True,
    }],
    "expires_at": "2026-10-10T12:15:00Z", "created_at": "2026-10-10T12:00:00Z",
    "network_fee_note": "Near-zero fees on Tron. Fastest settlement.",
    "qr_payload": "TPk2bpQqWRpXiaUHS9SXnZCDgvwrsuJ71o",
}


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


class WebhookIPNTest(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGateway)
        self.server.requests, self.server.replies = [], []
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.client = self.make_client()

    def make_client(self, **options):
        return payitgo.Client("sk_test_abc", base_url="http://127.0.0.1:%d/" % self.server.server_port,
                            max_retries=0, **options)

    def reply(self, status, body):
        self.server.replies.append((status, body))

    def request(self, i=0):
        return self.server.requests[i]

    def test_tokens(self):
        tokens = [{"token": "USDT_BEP20", "chain": "bsc", "min_usd": "1", "network_fee_usd": "0.01", "amount_due_usd": "40.43", "fee_note": ""}]
        self.reply(200, {"tokens": tokens})
        self.assertEqual(self.client.tokens.list(), tokens)
        self.assertEqual((self.request(0)["method"], self.request(0)["path"]), ("GET", "/v1/tokens"))
        self.reply(200, {"tokens": tokens})
        self.client.tokens.list(amount_usd="49.90")
        self.assertEqual(self.request(1)["path"], "/v1/tokens?amount_usd=49.90")

    def test_get(self):
        self.reply(200, {"webhook": WEBHOOK})
        webhook = self.client.webhooks.get()
        req = self.request()
        self.assertEqual((req["method"], req["path"], req["body"]), ("GET", "/v1/webhook", None))
        self.assertEqual(req["headers"]["Authorization"], "Bearer sk_test_abc")
        self.assertEqual(webhook, WEBHOOK)
        self.assertEqual(webhook.secret, WEBHOOK["secret"])

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
        self.assertEqual(req["headers"]["Authorization"], "Bearer sk_test_abc")
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
        self.assertEqual(self.request(0)["body"], {"order_id": "ORD-1", "amount_usd": "10"})
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
        self.assertEqual(req["headers"]["Authorization"], "Bearer sk_test_abc")
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
        self.reply(409, {"error": "the coin for this payment has already been chosen"})
        with self.assertRaises(payitgo.PayitgoError) as ctx:
            self.client.payments.select_token("pay_1", "XRP")
        self.assertEqual((ctx.exception.status, ctx.exception.message),
                         (409, "the coin for this payment has already been chosen"))

    def test_payment_simulate(self):
        self.reply(200, SIMULATED)
        payment = self.client.payments.simulate("pay_2", "confirmed")
        req = self.request()
        self.assertEqual((req["method"], req["path"]), ("POST", "/v1/payments/pay_2/simulate"))
        self.assertEqual(req["headers"]["Authorization"], "Bearer sk_test_abc")
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
                         {"order_id": "ORD-8", "amount_usd": "20", "token": "USDT_TRC20", "simulate": "confirmed"})
        self.assertEqual(payment.simulation.outcome, "confirmed")
        self.assertIn("confirmed", payitgo.SIMULATION_OUTCOMES)


class ClientConstructEventTest(unittest.TestCase):
    def test_client_secret(self):
        client = payitgo.Client("sk_test_abc", base_url="http://x", webhook_secret=SECRET)
        self.assertEqual(client.webhooks.construct_event(BODY, HEADER, tolerance=0).id, "evt_test_123")
        fresh = sign(SECRET, int(time.time()), BODY)
        self.assertEqual(client.webhooks.construct_event(BODY, fresh).id, "evt_test_123")

    def test_explicit_secret_wins(self):
        client = payitgo.Client("sk_test_abc", base_url="http://x", webhook_secret="whsec_other")
        with self.assertRaises(payitgo.SignatureVerificationError):
            client.webhooks.construct_event(BODY, HEADER, tolerance=0)
        self.assertEqual(client.webhooks.construct_event(BODY, HEADER, SECRET, tolerance=0).id, "evt_test_123")

    def test_no_secret(self):
        client = payitgo.Client("sk_test_abc", base_url="http://x")
        with self.assertRaisesRegex(payitgo.SignatureVerificationError, "no webhook secret"):
            client.webhooks.construct_event(BODY, HEADER)


if __name__ == "__main__":
    unittest.main()
