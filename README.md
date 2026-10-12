# payitgo for Python

Accept crypto payments with [payitgo](https://api.payitgo.co/docs) from Python 3.8+. No dependencies.

Full reference, with every method, option, type and error: [api.payitgo.co/docs/sdk-python](https://api.payitgo.co/docs/sdk-python).

```bash
pip install payitgo
```

The package is `payitgo` on PyPI; you import it as `payitgo`.

## Accept a payment in three steps

```python
import os
import payitgo
from flask import Flask, abort, redirect, request

client = payitgo.Client(
    os.environ["PAYITGO_KEY"],
    base_url=os.environ["PAYITGO_BASE_URL"],
    webhook_secret=os.environ["PAYITGO_WEBHOOK_SECRET"],  # "whsec_…": see Webhooks (IPN) below
)
app = Flask(__name__)

# 1. Create a payment and send the customer to the checkout page.
@app.post("/checkout")
def checkout():
    payment = client.payments.create(
        order_id=request.form["order_id"],   # your order id: safe to retry
        amount_usd=request.form["total"],    # e.g. "19.99"
        # token="USDT_TRC20",                # optional: without it the customer picks the coin
        success_url="https://shop.example/thanks",
    )
    return redirect(payment.checkout_url)

# 2. Receive the notification (the complete handler is under Webhooks (IPN) below).
#    Use the raw body: the signature covers its exact bytes.
@app.post("/payitgo/ipn")
def ipn():
    try:
        event = client.webhooks.construct_event(request.get_data(), request.headers.get("X-Webhook-Signature"))
    except payitgo.SignatureVerificationError:
        abort(400)
    # 3. Fulfil the order once the payment is final.
    if event.type == "payment.confirmed":
        mark_order_paid(event.data.order_id)  # make this idempotent: events can arrive twice
    return "", 200
```

Start with your test key (`sk_test_…`), where you [simulate](#test-mode) what the customer
pays; switch to the live key (`sk_live_…`) when the account owner has turned live mode on.

## Your own payment page

`checkout_url` is only for payitgo's hosted page. To show the payment on your own page,
render the payment your server gets back (`deposit_address`, `crypto_amount`,
`destination_tag` for XRP, a QR code of `qr_payload`, `expires_at`); no `checkout` calls
are needed.

- **You know the coin:** create the payment with `token` and render the payment it returns.
- **The customer chooses it:** create the payment without `token`, show
  `payment.token_options` on your page, then call
  `client.payments.select_token(payment.id, chosen)` from your server and render the
  payment it returns: it now has its coin, address, amount, locked rate and a deadline 15
  minutes away.

```python
payment = client.payments.create(order_id="ORD-1", amount_usd=10)
# your page shows payment.token_options; the customer picks one
chosen = client.payments.select_token(payment.id, "USDT_TRC20")  # or your order id
# render chosen.deposit_address, chosen.crypto_amount, chosen.qr_payload, chosen.expires_at
```

The coin is chosen once: choosing again is a `409` (`payments.retrieve` then returns the
payment with its coin). A coin outside `token_options`, or a payment that can no longer be
paid, is a `400`.

## Test mode

A test payment gets an address like a live one, but no coins are involved: you choose an
outcome and payitgo plays its deposits, status changes and webhooks a few seconds apart
([Test mode](https://api.payitgo.co/docs/testing)).

```python
# Choose the outcome when you create the payment (with token it starts at once,
# without it once the coin is chosen)…
client.payments.create(order_id="TEST-1", amount_usd=10, token="USDT_TRC20", simulate="confirmed")

# …or later, on a payment you already have (its id or your order id).
payment = client.payments.simulate("TEST-2", "partially_paid")
# payment.simulation: {"outcome": "partially_paid", "state": "running"}
```

Outcomes (`payitgo.SIMULATION_OUTCOMES`): `confirmed`, `overpaid`, `partially_paid`,
`underpaid`, `expired`, `failed`, `wrong_asset`, `paid_late`. Simulated deposits have
`"simulated": True` and a `tx_id` starting with `sim_`. A live key gets `400`; a payment
whose simulation is still running, `409`.

## Webhooks (IPN)

payitgo POSTs a signed notification for every change of a payment (`payment.confirmed`,
`payment.expired`, …), of a direct settlement and of a payout. Each mode has one webhook:
a test key manages the test one, a live key the live one.

**Where the notifications go.** Set the URL once per mode (https:// on a public host,
accepting POST requests), or give a payment its own when you create it:

```python
# Once per mode (safe to repeat):
client.webhooks.set("https://shop.example/payitgo/ipn")

# Or per payment: its events go there instead of the webhook's URL.
client.payments.create(order_id="ORD-1", amount_usd=10, webhook_url="https://shop.example/orders/ipn")

# Or as the client's default for every payment it creates (a payment's own webhook_url wins):
shop = payitgo.Client(os.environ["PAYITGO_KEY"], webhook_url="https://shop.example/orders/ipn")
```

A storefront's key cannot set the account's URL (`403`): pass `webhook_url` instead.
Payout events always go to the webhook's URL.

**The signing secret.** Every notification of a mode is signed with that mode's secret
(`whsec_…`), whichever URL it goes to. Copy it from the dashboard or read it with
`client.webhooks.get().secret`, and give it to the client once:

```python
client = payitgo.Client(os.environ["PAYITGO_KEY"], webhook_secret=os.environ["PAYITGO_WEBHOOK_SECRET"])
```

**Receiving them** (Flask). Verify the raw body, handle each event once, and answer
`2xx` within 10 seconds; otherwise payitgo retries after 1 min, 5 min, 30 min, 2 h, 6 h and
24 h.

```python
@app.post("/payitgo/ipn")
def ipn():
    try:
        # Checks the signature with the client's webhook_secret and refuses anything older than 5 minutes.
        event = client.webhooks.construct_event(request.get_data(), request.headers.get("X-Webhook-Signature"))
    except payitgo.SignatureVerificationError:
        abort(400)
    # The same event can arrive more than once: handle each event id once,
    # e.g. by inserting it into a table with a unique key.
    if not record_event_once(event.id):
        return "", 200
    if event.type == "payment.confirmed":
        jobs.enqueue("fulfil", event.data.order_id)  # slow work off the request
    elif event.type == "payment.expired":
        jobs.enqueue("release_stock", event.data.order_id)
    return "", 200
```

**Testing it.** `client.webhooks.test()` queues a signed `ping` event to the webhook's
URL and returns its delivery (`400` when no URL is set).

## API

```python
client = payitgo.Client(api_key, base_url="https://api.example.com",  # or PAYITGO_BASE_URL
                      timeout=30, max_retries=2,                    # network errors, 429, 5xx
                      webhook_secret="whsec_…",                     # for webhooks.construct_event
                      webhook_url="https://…")                      # default webhook_url of payments.create

client.livemode                                    # False for sk_test_ keys
client.payments.create(order_id, amount_usd, token, webhook_url=None, description=None, metadata=None,
                       customer_email=None, success_url=None, cancel_url=None, simulate=None)
client.payments.select_token("pay_…", token)       # or your order id: the payment with its coin
client.payments.simulate("pay_…", "confirmed")     # test keys: plays the outcome
client.payments.retrieve("pay_…")                  # or your order id
client.payments.list(status="confirmed", store_id="sto_…", limit=50, offset=0)  # every filter optional
client.tokens.list()                               # [{token, chain, min_usd, network_fee_usd, fee_note}]
client.tokens.list(amount_usd="49.90")             # the coins a $49.90 payment offers, with amount_due_usd
client.checkout.retrieve(checkout_token)           # public payer view
client.webhooks.get()                              # {livemode, url, secret, created_at, updated_at}
client.webhooks.set(url)                           # the webhook
client.webhooks.remove()                           # the webhook, url None (the secret stays)
client.webhooks.test()                             # the delivery of a "ping" event
client.webhooks.construct_event(raw_body, signature_header, secret=None, tolerance=300)
payitgo.Webhook.construct_event(raw_body, signature_header, secret, tolerance=300)  # no client needed
```

Responses are dicts that also allow attribute access: `payment["status"]` or
`payment.status`. Field names are the API's own (see the
[payment object](https://api.payitgo.co/docs/payments) and [webhooks](https://api.payitgo.co/docs/webhooks)).
`payitgo.WEBHOOK_EVENTS` lists the event types.

## Errors

```python
try:
    client.payments.create(order_id="ORD-1", amount_usd=1, token="BTC")
except payitgo.PayitgoError as e:
    print(e.status, e.message)   # 400 minimum payment in BTC is $10 right now (network fees)
```

`PayitgoError.status` is the HTTP status (0 for a network error).
`construct_event` raises `SignatureVerificationError` for a bad signature, a stale
timestamp, or when it has no secret (none passed and no `webhook_secret`).

## Development

```bash
python3 -m unittest discover -s tests
```
