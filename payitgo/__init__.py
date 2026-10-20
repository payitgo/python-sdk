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

__version__ = "0.6.0"
__all__ = ["Client", "Webhook", "PayitgoError", "SignatureVerificationError", "PayitgoObject", "WEBHOOK_EVENTS",
           "SIMULATION_OUTCOMES"]

# Used when neither base_url nor PAYITGO_BASE_URL is set.
DEFAULT_BASE_URL = "https://api.payitgo.co"

#: The events payitgo sends. A test (``client.webhooks.test()``) sends "ping".
WEBHOOK_EVENTS = (
    "payment.partially_paid", "payment.detecting", "payment.confirmed", "payment.underpaid",
    "payment.expired", "payment.failed", "payment.wrong_asset", "payment.late_deposit",
    "payout.approved", "payout.rejected", "payout.sent", "settlement.completed", "settlement.failed",
    "payout_batch.quoted", "payout_batch.pending_approval", "payout_batch.approved", "payout_batch.rejected",
    "payout_batch.awaiting_funds", "payout_batch.processing", "payout_batch.completed", "payout_batch.canceled",
    "payout_item.sent", "payout_item.failed", "payout_item.canceled", "payout_item.claimed",
    "payout_item.claim_expired", "payout_wallet.withdrawn", "payout_schedule.run", "payout_schedule.funding_needed",
    "ping",
)

#: The outcomes a test payment can be made to go through: ``client.payments.simulate()``.
SIMULATION_OUTCOMES = (
    "confirmed", "overpaid", "partially_paid", "underpaid", "expired", "failed", "wrong_asset", "paid_late",
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


def _path(value):
    """An id (or order id, coin) escaped as one segment of a path."""
    return urllib.parse.quote(value, safe="")


def _query(**params):
    """The query string of the params that are not None ("" when none). A list or tuple
    is sent comma-separated: status=sent,failed."""
    params = {k: ",".join(v) if isinstance(v, (list, tuple)) else v for k, v in params.items() if v is not None}
    return "?" + urllib.parse.urlencode(params, safe=",") if params else ""


def _payout_items(items):
    """Payments as the API takes them: each amount as str(amount), keys set to None left out."""
    return [{k: str(v) if k == "amount" else v for k, v in item.items() if v is not None} for item in items]


class Client:
    """The payitgo client, for one secret key, test or live (``is_live()`` says which).

    ``webhook_secret`` is the signing secret of the key's mode, which
    ``webhooks.construct_event`` uses when given none; ``webhook_url`` is sent as the
    ``webhook_url`` of every payment this client creates without its own.
    """

    def __init__(self, api_key, base_url=None, timeout=30, max_retries=2, webhook_secret=None, webhook_url=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("payitgo: the API key is required")
        base_url = base_url or os.environ.get("PAYITGO_BASE_URL") or DEFAULT_BASE_URL
        if not base_url:
            raise ValueError("payitgo: set base_url (or PAYITGO_BASE_URL) to your gateway's address")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._max_retries = max_retries
        self._webhook_secret = webhook_secret or None
        self._webhook_url = webhook_url or None
        self._live = None  # the key's mode, once payitgo has said it
        self.payments = _Payments(self)
        self.tokens = _Tokens(self)
        self.checkout = _Checkout(self)
        self.webhooks = _Webhooks(self)
        self.payouts = _Payouts(self)
        self.payout_batches = _PayoutBatches(self)
        self.payout_wallets = _PayoutWallets(self)

    def is_live(self):
        """Whether the key is a live key (its payments are real) rather than a test key.

        payitgo says which (``GET /v1/webhook``'s ``livemode``) the first time; the answer is
        kept for the client's life. Raises PayitgoError when payitgo refuses the key or cannot
        be reached.
        """
        if self._live is None:
            self._live = bool(self.webhooks.get()["livemode"])
        return self._live

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

    def create(self, order_id, amount_usd=None, token=None, webhook_url=None, *, price_amount=None,
               price_currency=None, **optional):
        """Creates a payment. Safe to retry: an order with an open payment returns that payment.

        Give the price as amount_usd, or as price_amount in price_currency (an ISO 4217
        code such as "EUR", "GBP" or "NGN"), which payitgo converts into dollars at the
        rate of the moment. Leave token out to let the customer choose the coin on the
        checkout page. webhook_url (https) gets the payment's events instead of the
        webhook's URL, signed with the same secret (default: the client's webhook_url).
        Optional fields: tokens (the coins to offer), description, metadata,
        customer_email, success_url, cancel_url, and simulate (test keys only: the
        outcome to simulate, as payments.simulate does, once the payment has its coin).
        """
        body = {"order_id": order_id}
        if amount_usd is not None:
            body["amount_usd"] = str(amount_usd)
        if price_amount is not None:
            body["price_amount"] = str(price_amount)
        if price_currency is not None:
            body["price_currency"] = price_currency
        if token:
            body["token"] = token
        webhook_url = webhook_url if webhook_url is not None else self._client._webhook_url
        if webhook_url:
            body["webhook_url"] = webhook_url
        body.update({k: v for k, v in optional.items() if v is not None})
        return self._client._request("POST", "/v1/payments", body)

    def retrieve(self, id):
        """A payment by its id or by your order id (the order's open payment)."""
        return self._client._request("GET", "/v1/payments/" + _path(id))

    def select_token(self, payment_id, token):
        """Sets the coin of a payment created without one, when the customer chose it on your
        own page: one of its token_options. payment_id is the payment's id or your order id.

        Returns the whole payment, now with its address, amount, locked rate and a deadline
        15 minutes away. Another coin changes it while the payment still has token_options;
        once it can no longer change, a 409.
        """
        return self._client._request("POST", "/v1/payments/" + _path(payment_id) + "/token", {"token": token})

    def simulate(self, payment_id, outcome):
        """Plays an outcome (one of SIMULATION_OUTCOMES) on a test payment: payitgo sends its
        deposits, status changes and webhooks a few seconds apart, with no coins. payment_id
        is the payment's id or your order id.

        Returns the whole payment, with its simulation. A live payment is a 400; a simulation
        that is still running, a 409.
        """
        return self._client._request("POST", "/v1/payments/" + _path(payment_id) + "/simulate", {"outcome": outcome})

    def list(self, status=None, chain=None, token=None, limit=None, offset=None, store_id=None):
        """Payments of the key's mode, newest first: {payments, total, limit, offset}.

        store_id keeps the payments of one store (ignored for a storefront's key, which
        sees its own).
        """
        query = _query(status=status, chain=chain, token=token, store_id=store_id, limit=limit, offset=offset)
        return self._client._request("GET", "/v1/payments" + query)


class _Tokens:
    def __init__(self, client):
        self._client = client

    def list(self, amount_usd=None):
        """The tokens a customer can pay with in the key's mode. With amount_usd,
        only the ones a payment of that amount offers (its token_options), each
        with amount_due_usd when fees are passed on."""
        return self._client._request("GET", "/v1/tokens" + _query(amount_usd=amount_usd))["tokens"]


class _Checkout:
    def __init__(self, client):
        self._client = client

    def retrieve(self, checkout_token):
        """The payer's view of a payment, by its checkout token. Public: sent without the API key."""
        return self._client._request("GET", "/checkout/" + _path(checkout_token), authenticated=False)

    def select_token(self, checkout_token, token):
        """The customer's choice of coin for a payment created without one. Public."""
        return self._client._request("POST", "/checkout/" + _path(checkout_token) + "/token", {"token": token},
                                     authenticated=False)


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


class _Payouts:
    """Payout requests: your clients' payouts, sent one by one as they come in. payitgo checks
    and prices each into your open list, and sends the list when you say (or by itself, with
    auto-send in your payout settings) from your payout wallet. Writes need a key with
    payouts turned on in the dashboard; reads work with any key."""

    def __init__(self, client):
        self._client = client

    def request(self, asset, address=None, amount=None, reference=None, *, tag=None, name=None, email=None,
                note=None, claim=None, claim_assets=None):
        """Adds one client's payout request to your open list, checked and priced at once.

        Safe to retry with a reference: one that another payment carries already returns that
        payment instead of a second one. An invalid request is still kept (status "invalid",
        its errors say why) and is never sent. amount (required) is sent as str(amount): give a
        str or a Decimal. Returns the payment.

        With claim=True (or claim_assets) and no address it is a claim link: its recipient
        chooses where to receive it, in asset or one of claim_assets (at the same dollar value).
        With an email the link is emailed there; without one, share the payment's claim.url.
        An email alone makes no claim link: without an address the request is invalid.
        """
        if amount is None:
            raise TypeError("payouts.request() missing required argument: 'amount'")
        body = {"asset": asset, "amount": str(amount)}
        body.update({k: v for k, v in {"address": address, "reference": reference, "tag": tag, "name": name,
                                       "email": email, "note": note, "claim": claim,
                                       "claim_assets": claim_assets}.items() if v is not None})
        return self._client._request("POST", "/v1/payouts", body)["item"]

    def retrieve(self, id):
        """A payment by its id: a payout request, or a payment of a batch."""
        return self._client._request("GET", "/v1/payouts/" + _path(id))["item"]

    def retrieve_by_reference(self, reference):
        """A payment by your reference for it: a payout request, or a payment of a batch."""
        return self._client._request("GET", "/v1/payouts" + _query(reference=reference))["item"]

    def inbox(self, status=None, limit=None, offset=None):
        """Your open list: {inbox, coverage, items, total, limit, offset}.

        inbox is the list as a batch (its totals and counts), coverage what sending its valid
        requests needs of each coin against what the wallet can spend (top_up: what to send to
        address first), items the requests themselves, read as page["items"]. status is one
        status or a list of them.
        """
        return self._client._request("GET", "/v1/payouts/inbox" + _query(status=status, limit=limit, offset=offset))

    def send(self, ids=None):
        """Sends your open list: every valid request, or only those of ids. They move into a
        batch of their own, priced again and submitted (it then waits for approvals or funds,
        or starts). Returns that batch; invalid requests stay in the list.
        """
        body = None
        if ids is not None:
            ids = [ids] if isinstance(ids, str) else list(ids)
            if not ids:
                raise ValueError("payitgo: ids is empty: pass None to send every valid request")
            body = {"ids": ids}
        return self._client._request("POST", "/v1/payouts/send", body)["batch"]

    def cancel(self, id):
        """Takes a request out: one still in your open list is removed (its reference free
        again); one sent but not on its way yet is canceled. Returns the payment."""
        return self._client._request("DELETE", "/v1/payouts/" + _path(id))["item"]

    def quote(self, items, fee_paid_by=None, *, convert=None, convert_from=None):
        """Prices up to 100 payments exactly as a batch would, keeping nothing: {quote, items}.

        Each item is a dict with asset, address and amount, and optionally reference, tag, name,
        email, note, claim and claim_assets (claim=True and no address: a claim link). fee_paid_by is "merchant"
        (fees on top) or "recipient" (fees out of each amount); convert is "same_network" or
        "convert" (payments in another coin than convert_from paid from convert_from, converted
        on their way). Each defaults to your payout settings'.
        """
        body = {"items": _payout_items(items)}
        body.update({k: v for k, v in {"fee_paid_by": fee_paid_by, "convert": convert,
                                       "convert_from": convert_from}.items() if v is not None})
        return self._client._request("POST", "/v1/payout-quotes", body)


class _PayoutBatches:
    """Batches: a whole list of payments at once, checked and priced, then submitted.
    Writes need a key with payouts turned on in the dashboard; reads work with any key."""

    def __init__(self, client):
        self._client = client

    def create(self, items, reference=None, *, name=None, fee_paid_by=None, webhook_url=None, convert=None,
               convert_from=None):
        """Makes a batch of 1 to 1000 payments (each a dict, as payouts.quote takes them).

        Safe to retry with a reference: one used already returns that batch, with nothing
        added. webhook_url (https) gets the batch's events instead of the webhook's URL.
        fee_paid_by, convert and convert_from are as payouts.quote takes them. Returns the
        batch: "quoted", or "validating" past 100 payments.
        """
        body = {"items": _payout_items(items)}
        body.update({k: v for k, v in {"reference": reference, "name": name, "fee_paid_by": fee_paid_by,
                                       "webhook_url": webhook_url, "convert": convert,
                                       "convert_from": convert_from}.items() if v is not None})
        return self._client._request("POST", "/v1/payout-batches", body)["batch"]

    def list(self, status=None, limit=None, offset=None):
        """Batches of the key's mode, newest first: {batches, total, limit, offset}."""
        return self._client._request("GET", "/v1/payout-batches" + _query(status=status, limit=limit, offset=offset))

    def retrieve(self, id):
        """A batch by its id."""
        return self._client._request("GET", "/v1/payout-batches/" + _path(id))["batch"]

    def items(self, id, status=None, limit=None, offset=None):
        """A batch's payments in row order: {items, total, limit, offset}, read as page["items"].
        status is one status or a list of them."""
        query = _query(status=status, limit=limit, offset=offset)
        return self._client._request("GET", "/v1/payout-batches/" + _path(id) + "/items" + query)

    def add_items(self, id, items):
        """Adds 1 to 1000 payments to a batch not submitted yet, which is priced again.
        Returns the batch."""
        return self._client._request("POST", "/v1/payout-batches/" + _path(id) + "/items",
                                     {"items": _payout_items(items)})["batch"]

    def quote(self, id):
        """Prices a batch not submitted yet again, at today's prices (they hold for an hour).
        Returns the batch."""
        return self._client._request("POST", "/v1/payout-batches/" + _path(id) + "/quote")["batch"]

    def submit(self, id):
        """Submits a quoted batch: it waits for its approvals and its funds, or starts.
        Returns the batch."""
        return self._client._request("POST", "/v1/payout-batches/" + _path(id) + "/submit")["batch"]

    def cancel(self, id):
        """Cancels a batch before it is funded, or its payments not sent yet once it is.
        Returns the batch."""
        return self._client._request("POST", "/v1/payout-batches/" + _path(id) + "/cancel")["batch"]


class _PayoutWallets:
    """Your payout wallet: one address per network, yours alone, that you top up."""

    def __init__(self, client):
        self._client = client

    def list(self):
        """The wallet of the key's mode, one per network, each with what it holds of each coin
        (balance, reserved, spendable, needed)."""
        return self._client._request("GET", "/v1/payout-wallets")["wallets"]

    def simulate_top_up(self, asset, amount):
        """Test keys only: adds amount (sent as str(amount)) of asset to the test wallet on
        paper, and funds any test batch waiting for it. Returns the wallets."""
        return self._client._request("POST", "/v1/payout-wallets/" + _path(asset) + "/simulate-top-up",
                                     {"amount": str(amount)})["wallets"]


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
