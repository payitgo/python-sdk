"""Python client for the payitgo crypto payment gateway.

    import payitgo

    client = payitgo.Client(os.environ["PAYITGO_KEY"], base_url="https://api.example.com")
    payment = client.payments.create(order_id="ORD-1001", amount_usd=10, token="USDT_TRC20")
    # send the customer to payment.checkout_url; fulfil on the payment.confirmed webhook

Use the client from your server only: the API key is a secret.
"""

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

__version__ = "0.1.1"
__all__ = ["Client", "Webhook", "PayitgoError", "SignatureVerificationError", "PayitgoObject", "WEBHOOK_EVENTS"]

# Used when neither base_url nor PAYITGO_BASE_URL is set.
DEFAULT_BASE_URL = "https://api.payitgo.co"

#: The events payitgo sends. A test (``client.webhooks.test()``) sends "ping".
WEBHOOK_EVENTS = (
    "payment.partially_paid", "payment.detecting", "payment.confirmed", "payment.underpaid",
    "payment.expired", "payment.failed", "payment.wrong_asset", "payment.late_deposit",
    "payout.approved", "payout.rejected", "payout.sent", "settlement.completed", "settlement.failed",
    "ping",
)


class PayitgoError(Exception):
    """The API refused the request. ``status`` is the HTTP status, or 0 for a network error."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.message = message
        self.status = status


class SignatureVerificationError(Exception):
    """A webhook request whose signature or timestamp did not check out."""


class PayitgoObject(dict):
    """A dict whose keys can also be read as attributes: ``payment.status``."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError:
            raise AttributeError(name) from None


def _wrap(value):
    if isinstance(value, dict):
        return PayitgoObject({k: _wrap(v) for k, v in value.items()})
    if isinstance(value, list):
        return [_wrap(v) for v in value]
    return value


class Client:
    """The payitgo client, for one secret key (``sk_test_…`` or ``sk_live_…``).

    ``webhook_secret`` is the signing secret of the key's mode (``whsec_…``), which
    ``webhooks.construct_event`` uses when given none; ``webhook_url`` is sent as the
    ``webhook_url`` of every payment this client creates without its own.
    """

    def __init__(self, api_key, base_url=None, timeout=30, max_retries=2, webhook_secret=None, webhook_url=None):
        if not isinstance(api_key, str) or not api_key.startswith(("sk_test_", "sk_live_")):
            raise ValueError("payitgo: the API key must start with sk_test_ or sk_live_")
        base_url = base_url or os.environ.get("PAYITGO_BASE_URL") or DEFAULT_BASE_URL
        if not base_url:
            raise ValueError("payitgo: set base_url (or PAYITGO_BASE_URL) to your gateway's address")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._webhook_secret = webhook_secret or None
        self._webhook_url = webhook_url or None
        #: False for a test key, True for a live key.
        self.livemode = api_key.startswith("sk_live_")
        self.payments = _Payments(self)
        self.tokens = _Tokens(self)
        self.checkout = _Checkout(self)
        self.webhooks = _Webhooks(self)

    def _request(self, method, path, body=None, authenticated=True):
        headers = {"Accept": "application/json", "User-Agent": "payitgo-python/" + __version__}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        if authenticated:
            headers["Authorization"] = "Bearer " + self._api_key

        attempt = 0
        while True:
            req = urllib.request.Request(self._base_url + path, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                    raw = resp.read()
                    return _wrap(json.loads(raw)) if raw else None
            except urllib.error.HTTPError as e:
                with e:
                    raw = e.read()
                if (e.code == 429 or e.code >= 500) and attempt < self._max_retries:
                    attempt += 1
                    time.sleep(0.5 * 2 ** (attempt - 1))
                    continue
                try:
                    message = json.loads(raw).get("error") or e.reason
                except (ValueError, AttributeError):
                    message = e.reason or "HTTP %d" % e.code
                raise PayitgoError(message, e.code) from None
            except (urllib.error.URLError, OSError) as e:
                if attempt < self._max_retries:
                    attempt += 1
                    time.sleep(0.5 * 2 ** (attempt - 1))
                    continue
                raise PayitgoError("request failed: %s" % e, 0) from None


class _Payments:
    def __init__(self, client):
        self._client = client

    def create(self, order_id, amount_usd, token=None, webhook_url=None, **optional):
        """Creates a payment. Safe to retry: an order with an open payment returns that payment.

        Leave token out to let the customer choose the coin on the checkout page.
        webhook_url (https) gets the payment's events instead of the webhook's URL,
        signed with the same secret (default: the client's webhook_url).
        Optional fields: tokens (the coins to offer), description, metadata,
        customer_email, success_url, cancel_url.
        """
        body = {"order_id": order_id, "amount_usd": str(amount_usd)}
        if token:
            body["token"] = token
        webhook_url = webhook_url if webhook_url is not None else self._client._webhook_url
        if webhook_url:
            body["webhook_url"] = webhook_url
        body.update({k: v for k, v in optional.items() if v is not None})
        return self._client._request("POST", "/v1/payments", body)

    def retrieve(self, id):
        """A payment by its id (pay_…) or by your order id (the order's open payment)."""
        return self._client._request("GET", "/v1/payments/" + urllib.parse.quote(id, safe=""))

    def select_token(self, payment_id, token):
        """Sets the coin of a payment created without one, when the customer chose it on your
        own page: one of its token_options. payment_id is the payment's id (pay_…) or your order id.

        Returns the whole payment, now with its address, amount, locked rate and a deadline
        15 minutes away. The coin is chosen once: choosing again is a 409.
        """
        return self._client._request("POST", "/v1/payments/" + urllib.parse.quote(payment_id, safe="") + "/token",
                                     {"token": token})

    def list(self, status=None, chain=None, token=None, limit=None, offset=None, store_id=None):
        """Payments of the key's mode, newest first: {payments, total, limit, offset}.

        store_id keeps the payments of one store (ignored for a storefront's key, which
        sees its own).
        """
        params = {k: v for k, v in {"status": status, "chain": chain, "token": token, "store_id": store_id,
                                    "limit": limit, "offset": offset}.items() if v is not None}
        query = "?" + urllib.parse.urlencode(params) if params else ""
        return self._client._request("GET", "/v1/payments" + query)


class _Tokens:
    def __init__(self, client):
        self._client = client

    def list(self, amount_usd=None):
        """The tokens a customer can pay with in the key's mode. With amount_usd,
        only the ones a payment of that amount offers (its token_options), each
        with amount_due_usd when fees are passed on."""
        query = "?" + urllib.parse.urlencode({"amount_usd": str(amount_usd)}) if amount_usd is not None else ""
        return self._client._request("GET", "/v1/tokens" + query)["tokens"]


class _Checkout:
    def __init__(self, client):
        self._client = client

    def retrieve(self, checkout_token):
        """The payer's view of a payment, by its checkout token. Public: sent without the API key."""
        return self._client._request("GET", "/checkout/" + urllib.parse.quote(checkout_token, safe=""),
                                     authenticated=False)

    def select_token(self, checkout_token, token):
        """The customer's choice of coin for a payment created without one. Public."""
        return self._client._request("POST", "/checkout/" + urllib.parse.quote(checkout_token, safe="") + "/token",
                                     {"token": token}, authenticated=False)


class _Webhooks:
    """The webhook (IPN) of the key's mode: a test key manages the test webhook, a live key
    the live one. A storefront's key may read and test it, not change it."""

    def __init__(self, client):
        self._client = client

    def get(self):
        """The webhook: {livemode, url (None when not set), secret, created_at, updated_at}."""
        return self._client._request("GET", "/v1/webhook")["webhook"]

    def set(self, url):
        """Sets the URL every event of the mode is POSTed to: https, public host, accepting POST
        requests. Safe to retry."""
        return self._client._request("PUT", "/v1/webhook", {"url": url})["webhook"]

    def remove(self):
        """Stops POSTing to the URL (payments with their own webhook_url still get theirs); the
        secret stays."""
        return self._client._request("DELETE", "/v1/webhook")["webhook"]

    def test(self):
        """Queues a signed "ping" event to the URL and returns its delivery."""
        return self._client._request("POST", "/v1/webhook/test")["delivery"]

    def construct_event(self, payload, signature_header, secret=None, tolerance=None):
        """Verifies a webhook request and returns its event; see ``Webhook.construct_event``.

        ``secret`` defaults to the client's ``webhook_secret``; without either it raises
        SignatureVerificationError.
        """
        secret = secret or self._client._webhook_secret
        if not secret:
            raise SignatureVerificationError("no webhook secret: pass one, or set the client's webhook_secret")
        if tolerance is None:
            tolerance = Webhook.DEFAULT_TOLERANCE
        return Webhook.construct_event(payload, signature_header, secret, tolerance)


class Webhook:
    DEFAULT_TOLERANCE = 300

    @staticmethod
    def construct_event(payload, signature_header, secret, tolerance=DEFAULT_TOLERANCE):
        """Verifies a webhook request and returns its event.

        Pass the raw request body (bytes, before any JSON parsing), the
        X-Webhook-Signature header and the mode's signing secret (the webhook's
        ``secret``, from ``client.webhooks.get()`` or the dashboard). Raises
        SignatureVerificationError when the signature is wrong or the timestamp
        is more than ``tolerance`` seconds old (0 disables the timestamp check).
        """
        if isinstance(payload, str):
            payload = payload.encode()
        parts = dict(p.strip().split("=", 1) for p in (signature_header or "").split(",") if "=" in p)
        t, v1 = parts.get("t", ""), parts.get("v1", "")
        if not t or not v1:
            raise SignatureVerificationError("missing or malformed X-Webhook-Signature header")
        expected = hmac.new(secret.encode(), t.encode() + b"." + payload, hashlib.sha256).hexdigest()
        # Compared as bytes: a header with non-ASCII characters is a mismatch, not a TypeError.
        if not hmac.compare_digest(expected.encode(), v1.encode()):
            raise SignatureVerificationError("signature does not match")
        if tolerance and tolerance > 0:
            try:
                if abs(time.time() - int(t)) > tolerance:
                    raise SignatureVerificationError("timestamp outside the tolerance")
            except ValueError:
                raise SignatureVerificationError("malformed timestamp") from None
        return _wrap(json.loads(payload))
