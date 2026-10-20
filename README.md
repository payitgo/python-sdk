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
    webhook_secret=os.environ["PAYITGO_WEBHOOK_SECRET"],  # the signing secret: see Webhooks (IPN) below
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

Start with a test key, where you [simulate](#test-mode) what the customer pays; switch to
a live key when the account owner has turned live mode on. A key does not say its mode
(the dashboard shows a Test or Live badge next to it): `client.is_live()` asks payitgo,
once.

## Prices in other currencies

To price a payment in your own currency, give `price_amount` and `price_currency` (an ISO
4217 code such as `EUR`, `GBP` or `NGN`) instead of `amount_usd`. payitgo converts the price
into dollars at the rate of the moment, and the payment keeps both:

```python
payment = client.payments.create(
    order_id="ORD-1002",
    price_amount="45.50",    # sent as str(price_amount): a str or a Decimal, not a float
    price_currency="EUR",    # any case
)
# payment.price_amount "45.5", payment.price_currency "EUR",
# payment.price_rate "1.082" (dollars per euro), payment.usd_amount "49.23"
```

Everything else (`usd_amount`, `crypto_amount`, fees, minimums) stays in dollars; a payment
priced in dollars has no `price_*` keys (read them with `.get()`). Give `amount_usd` or the
pair, not both (`400`). When no exchange rate can be had, `create` raises a `PayitgoError`
with status `503` (`exchange rate unavailable for price_currency: try again shortly`) once the
client's retries are spent: try again shortly.

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

The customer can change the coin while the payment still has `token_options` (nothing has
arrived yet): call `payments.select_token` again with another. Once it can no longer change, it is a `409`
(`payments.retrieve` then returns the payment with its coin). A coin outside `token_options`, or a
payment that can no longer be paid, is a `400`.

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
`"simulated": True`; their `tx_id` names no transaction on any chain. A live key gets
`400`; a payment whose simulation is still running, `409`.

## Webhooks (IPN)

payitgo POSTs a signed notification for every change of a payment (`payment.confirmed`,
`payment.expired`, …), of a direct settlement, of a payout and of your
[payouts to your clients](#payouts). Each mode has one webhook: a test key manages the test
one, a live key the live one.

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
A payout's events go to its own `webhook_url` when it has one, otherwise to its batch's
`webhook_url`, otherwise to the webhook's URL ([payout webhooks](#payout-webhooks)).

**The signing secret.** Every notification of a mode is signed with that mode's secret,
whichever URL it goes to. Copy it from the dashboard or read it with
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

**Replaying them.** Every event is kept as it was sent. After an outage, read what you
missed, oldest first, and handle it like a webhook (your event id check skips the ones you
had):

```python
after = last_event_id()  # your code: the id of the last event you handled
while True:
    page = client.events.list(type="payout_item.*", after=after)  # type: one, a list, or a family
    for event in page.events:
        handle(event)
        after = event.id
    if not page.has_more:
        break
```

## Payouts

Pay your own clients (withdrawals from your platform, prizes, payroll) from your **payout
wallet**: one address per network, yours alone, that you top up. Each payment costs 0.5% (at
least $0.50) plus its network fee; the coins it pays in, such as `USDT_TRC20` or `USDT_BEP20`,
are listed in [Mass payouts](https://api.payitgo.co/docs/payouts).

Reading works with any key. Everything that changes something needs a key with **payouts**
turned on in the dashboard; any other key gets `403` (`this api key cannot make payouts: turn
payouts on for it in the dashboard`).

### Payout requests, one by one

Send each client's payout request as it comes in. payitgo checks and prices it at once and
keeps it in your open list, with its costs, until you send the list (or it goes by itself,
with auto-send in your payout settings):

```python
import decimal

# A client asked to withdraw 25.50 USDT.
item = client.payouts.request(
    asset="USDT_TRC20",
    address="TR7mNkucnhLZoJQLLJ6DR88jpLQufWxUJi",
    amount="25.50",               # sent as str(amount): a str or a Decimal, not a float
    reference="withdrawal-1001",  # your id: sending it again returns this payment (safe to retry)
    name="Ana Silva",             # optional, with email and note
)
if item.status == "invalid":
    print([e.message for e in item.errors])  # kept, never sent: tell your client why
else:
    print(item.fee, item.debit)               # its fees, and what it takes from the wallet

# Your open list, and what sending its valid requests needs of each coin.
inbox = client.payouts.inbox()
for c in inbox.coverage:
    if decimal.Decimal(c.top_up) > 0:
        print(f"send {c.top_up} {c.asset} to {c.address} first")
print(len(inbox["items"]), "requests")  # inbox.items is the dict method: use ["items"]

# Send every valid request (or send([id, …]) for some): they become a batch.
batch = client.payouts.send()
print(batch.status)  # pending_approval, awaiting_funds or processing

client.payouts.retrieve(item.id)                         # any payment, by its id…
client.payouts.retrieve_by_reference("withdrawal-1001")  # …or by your reference
client.payouts.cancel(item.id)  # out of the list, or canceled if sent but not on its way yet
client.payouts.remove([item.id, "7b1d3f5a9c2e4b6d8f0a1c3e"])  # many out of the list: a result per id
```

`request` answers the same way the first time (`201`) and on a retry (`200`). To show a
client their fee before they ask, `client.payouts.quote(items)` prices up to 100 payments
without keeping anything: `{quote, items}`. Give a request `webhook_url=` to send its events
to its own URL.

A removed request is kept: `canceled`, with `cancel_reason` `"removed"`, no `batch_id`, and its
reference given back (`released_reference`). `cancel` answers a payment canceled already as it
is, so it is safe to retry. `remove(ids)` returns `{id, item}` or `{id, error: {status,
message}}` per id; it never cancels a request sent already (a `409` result: `cancel` it), and
an empty `ids` raises `ValueError` without a request.

### Batches

A whole list at once, up to 1,000 payments per call and 10,000 per batch:

```python
batch = client.payout_batches.create(
    [
        {"reference": "payroll-2026-10-ana", "asset": "USDT_TRC20",
         "address": "TR7mNkucnhLZoJQLLJ6DR88jpLQufWxUJi", "amount": "120", "name": "Ana Silva"},
        {"reference": "payroll-2026-10-ben", "asset": "USDT_BEP20",
         "address": "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed", "amount": "250"},
    ],
    reference="payroll-2026-10",  # a retry returns this batch, with nothing added
    name="October payroll",
    # fee_paid_by="recipient",    # fees out of each amount instead of on top
)
for row in client.payout_batches.items(batch.id, status="invalid")["items"]:
    print(row.row, row.errors[0].message)  # left out of the batch

batch = client.payout_batches.submit(batch.id)
if batch.status == "awaiting_funds" and batch.waiting.reason == "funds":
    for s in batch.waiting.shortfall:
        print(f"top up {s.amount} {s.asset} at {s.address}")  # it starts by itself once funded
```

A batch is `quoted` once checked (one of more than 100 payments comes back `validating`,
then `payout_batch.quoted` follows). Its prices hold for an hour: past that, `submit` raises
`409` (`its prices expired: get a new quote first`), so call
`client.payout_batches.quote(batch.id)` and submit again. Before it is submitted you can
`add_items`; `cancel` stops it before it is funded, or its payments not sent yet after.

### Claim links

When you don't know the recipient's address, pass `claim=True` (or `claim_assets`) and leave
the address out: the payment is a **claim link**, and its recipient chooses the coin and the
address. With an email, payitgo emails them the link; without one, share `item.claim.url`
yourself. `claim_assets` are the other coins they may choose, paid at the same dollar value.
An email alone makes no claim link: without an address and `claim=True` the payment is
`invalid` (`invalid_address`).

```python
item = client.payouts.request(
    asset="USDT_TRC20",
    amount="50",
    reference="prize-cup-final-1",
    claim=True,
    email="winner@example.com",               # optional: the link is emailed here
    claim_assets=["USDT_BEP20", "USDC_BEP20"],
)
# The same in a batch: a row with "claim": True (or "claim_assets") and no "address".
```

Once its batch is funded, the payment is `claimable` and its money is kept for it until
`item.claim.expires_at`; claimed, it is paid like any other (`payout_item.claimed`, then
`payout_item.sent`). Never claimed, it is `expired` and its money is free again
(`payout_item.claim_expired`). Read `item.claim`: `url`, `email`, `assets`, `expires_at`,
`claimed_at`, `emailed`, `extended`, and `funded_asset` / `funded_amount` when the recipient
chose another coin than the one it was funded with. A bad email is an `invalid_email` error.

### Converted payments

To pay recipients in another coin than your wallet holds, set `convert="convert"` on a batch
or a quote: every payment in another coin than `convert_from` is paid from `convert_from`,
converted on its way. Both default to your payout settings.

```python
batch = client.payout_batches.create(
    [{"reference": "payroll-2026-10-dan", "asset": "BTC",
      "address": "bc1qar0srrr7xfkvy5l643lydnw9re59gtzzwf5mdq", "amount": "0.001"}],
    reference="payroll-2026-10-btc",
    convert="convert",
    convert_from="USDT_TRC20",   # paid from the wallet's USDT on Tron
)
item = client.payout_batches.items(batch.id)["items"][0]
print(item.convert.to, item.convert.to_amount, item.convert.estimate)  # BTC 0.001 0.00100502
print(item.asset, item.amount, item.debit, item.conversion_fee)  # USDT_TRC20 65 69.442417 2.34
```

For a converted payment, `asset`, `amount`, `fee` and `debit` are in the wallet's coin
(`convert_from`) and `conversion_fee` is part of `fee`; `address` is the recipient's on the
network of `convert.to`. `convert.estimate` is about what arrives, and `convert.amount_out`
what did, once sent. A batch with converted payments holds its prices for 15 minutes. A coin
that cannot be converted into right now is a `conversion_unavailable` error.

### The payout wallet

```python
for wallet in client.payout_wallets.list():  # one per network
    for a in wallet.assets:
        print(wallet.network, wallet.address, a.asset, a.spendable, "needed:", a.needed)
```

`balance` is what the address holds, `reserved` what is promised to funded payments, and
`spendable` the rest; `needed` is what your batches waiting for funds lack: the top-up to send.

### Payouts in test mode

With a test key, the test wallet is topped up on paper, and payments are sent on paper within
seconds (a made-up `tx_id`, nothing on chain):

```python
client.payout_wallets.simulate_top_up("USDT_TRC20", "1000")  # also funds test batches waiting for it
```

A live key gets `400`.

### Payout webhooks

`payout_batch.quoted`, `.pending_approval`, `.approved`, `.rejected`, `.awaiting_funds`,
`.processing`, `.completed` and `.canceled` carry the batch; `payout_item.processing`,
`.returned`, `.sent`, `.failed`, `.canceled`, `.claimed` and `.claim_expired` the payment;
`payout_wallet.withdrawn` and `.withdrawal_failed` (with `fail_reason`) the withdrawal. A
payment made with its own `webhook_url` sends its events there; otherwise a batch made with
`webhook_url` sends its events and its payments' there; otherwise they go to the webhook's
URL (withdrawals and schedules always do). Schedules (set up in the dashboard) send
`payout_schedule.run` with a summary of the schedule (its rows left out), and
`payout_schedule.funding_needed` with `{schedule, shortfall}` a few days before a run the
wallet cannot cover; a schedule's batches have `source` `"schedule"`.

```python
# In the handler under Webhooks (IPN), after construct_event and the event id check:
if event.type == "payout_item.sent":
    mark_withdrawal_paid(event.data.get("reference"), event.data.tx_id)
elif event.type in ("payout_item.failed", "payout_item.canceled", "payout_item.claim_expired"):
    # Its money stays in your wallet. A payment never sent gives its reference back
    # (released_reference), for a new payment to use.
    reopen_withdrawal(event.data.get("reference") or event.data.get("released_reference"))
```

**Staying in sync.** Every payment, a payout request or a batch row, ends `sent`, `failed`,
`canceled` or `expired`, with one event each (`payout_item.canceled` whatever canceled it:
`cancel_reason` and `canceled_by` say why and who), delivered at least once. An event keeps
its `id` however often it is sent: deduplicate on `event.id`. Order is not guaranteed: act on
`event.data.status` and the latest `event.data.updated_at`. A payout request also tells you
when it leaves your list (`payout_item.processing`) and comes back (`payout_item.returned`,
not final). Replay what you missed with `client.events.list(after=last_id)`, and resync your
payments with `client.payouts.list()`, oldest change first:

```python
from datetime import datetime, timezone

page = client.payouts.list(status=["sent", "failed", "canceled", "expired"],
                           updated_since=datetime(2026, 10, 1, tzinfo=timezone.utc))
for item in page["items"]:  # then read on with cursor=page["next_cursor"] while page.has_more
    sync_withdrawal(item)
cursor = page.get("next_cursor")  # keep it: next time, list(cursor=cursor) gets what changed since
```

## API

```python
client = payitgo.Client(api_key, base_url="https://api.example.com",  # or PAYITGO_BASE_URL
                      timeout=30, max_retries=2,                    # network errors, 429, 5xx
                      webhook_secret=secret,                        # for webhooks.construct_event
                      webhook_url="https://…")                      # default webhook_url of payments.create

client.is_live()                                   # False for a test key (asks payitgo once)
client.payments.create(order_id, amount_usd, token, webhook_url=None, description=None, metadata=None,
                       customer_email=None, success_url=None, cancel_url=None, simulate=None)
client.payments.create(order_id, price_amount=…, price_currency=…, …)  # priced in another currency
client.payments.select_token(payment_id, token)    # or your order id: the payment with its coin
client.payments.simulate(payment_id, "confirmed")  # test keys: plays the outcome
client.payments.retrieve(payment_id)               # or your order id
client.payments.list(status="confirmed", store_id=store_id, limit=50, offset=0) # every filter optional
client.tokens.list()                               # [{token, chain, min_usd, network_fee_usd, fee_note}]
client.tokens.list(amount_usd="49.90")             # the coins a $49.90 payment offers, with amount_due_usd
client.checkout.retrieve(checkout_token)           # public payer view
client.webhooks.get()                              # {livemode, url, secret, created_at, updated_at}
client.webhooks.set(url)                           # the webhook
client.webhooks.remove()                           # the webhook, url None (the secret stays)
client.webhooks.test()                             # the delivery of a "ping" event
client.webhooks.construct_event(raw_body, signature_header, secret=None, tolerance=300)
payitgo.Webhook.construct_event(raw_body, signature_header, secret, tolerance=300)  # no client needed
client.events.list(type=None, after=None, created_after=None, limit=None)  # {events, has_more}, oldest first

client.payouts.request(asset, address, amount, reference=None, tag=None, name=None, email=None, note=None,
                       claim=None, claim_assets=None, webhook_url=None)  # claim=True, no address: a claim link
client.payouts.retrieve(id)                        # a payment: a request or a batch's
client.payouts.retrieve_by_reference(reference)    # or the latest payment that gave it back
client.payouts.list(status=None, updated_since=None, cursor=None, limit=None)  # {items, has_more, next_cursor}
client.payouts.inbox(status=None, limit=None, offset=None)  # {inbox, coverage, items, total, limit, offset}
client.payouts.send(ids=None)                      # the batch they became
client.payouts.cancel(id)                          # the payment (safe to retry)
client.payouts.remove(ids)                         # [{id, item} or {id, error}], one per id
client.payouts.quote(items, fee_paid_by=None, convert=None, convert_from=None)  # {quote, items}, nothing kept
client.payout_batches.create(items, reference=None, name=None, fee_paid_by=None, webhook_url=None,
                             convert=None, convert_from=None)
client.payout_batches.list(status=None, limit=None, offset=None)  # {batches, total, limit, offset}
client.payout_batches.retrieve(id)
client.payout_batches.items(id, status=None, limit=None, offset=None)  # {items, total, limit, offset}
client.payout_batches.add_items(id, items)
client.payout_batches.quote(id)                    # priced again
client.payout_batches.submit(id)
client.payout_batches.cancel(id)
client.payout_wallets.list()                       # [{chain, network, address, assets, …}]
client.payout_wallets.simulate_top_up(asset, amount)  # test keys: the wallets
```

Responses are dicts that also allow attribute access: `payment["status"]` or
`payment.status`. Field names are the API's own (see the
[payment object](https://api.payitgo.co/docs/payments), [webhooks](https://api.payitgo.co/docs/webhooks)
and [mass payouts](https://api.payitgo.co/docs/payouts)). A key named like a dict method is
read with `[]`: `page["items"]`, not `page.items`. Amounts are decimal strings: convert them
with `decimal.Decimal`. `payitgo.WEBHOOK_EVENTS` lists the event types.

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
