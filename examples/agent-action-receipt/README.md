# Free Agent Action Receipt Gate — Sender **and** Receiver

**The failure:** an AI agent calls a booking/SMS/payment tool. The operation succeeds, but its network acknowledgment is lost. Retrying with a new attempt can repeat the side effect. Merely **generating an idempotency key on the sender** does not guarantee anything: the **receiver must check and enforce** that key before committing the action.

This free, dependency-free Python 3.10+ example has **two deliberately separate layers**:

| Layer | File | What it guarantees (within its scope) |
| --- | --- | --- |
| Sender | `agent_action_receipt.py` | A durable local `pending` receipt prevents a **second local attempt** for the same stable business action. Unknown outcomes remain blocked until independently reconciled. |
| Receiver | `booking_receiver.py` | An atomic SQLite transaction **checks the incoming key first**, inserts a booking, and persists its cached response together. Matching retries return that response; mismatched payloads are rejected. |

The receiver also enforces `UNIQUE(slot)` to prevent a different key from silently taking the same slot. In this sample, a **booking is a row in the receiver's SQLite database**, not a call to an external scheduling provider.

## Run all tests

From this directory:

```bash
python3 -m unittest -v test_agent_action_receipt.py test_booking_receiver.py
```

The tests cover: sender deduplication; payload mismatch; pending-timeout safety; concurrent sender calls; receiver deduplication; receiver payload mismatch; occupied slots with different keys; six simultaneous receiver calls; rollback when receipt persistence fails; and reconciliation after a **lost acknowledgment**.

## Receiver-side enforcement (the missing piece)

```python
from booking_receiver import book_slot, lookup_booking_by_key

# Imagine this function sits behind an authenticated POST /bookings endpoint.
# The HTTP handler extracts an idempotency key from the request header.
key = "tenant7:lead42:first-appointment"  # stable across retries
result = book_slot("receiver.sqlite3", key, "customer-42", "2026-11-10T10:00")
print(result)

# A duplicate request with the SAME key + SAME payload returns the SAME booking.
assert book_slot("receiver.sqlite3", key, "customer-42", "2026-11-10T10:00") == result

# After a timeout, query the authoritative receiver rather than guessing.
assert lookup_booking_by_key("receiver.sqlite3", key, "customer-42", "2026-11-10T10:00") == result
```

`book_slot(...)` performs these steps inside **one `BEGIN IMMEDIATE` transaction**:

1. Look up the incoming key in `booking_receipts` **before** any booking insert.
2. Return its cached response if the payload fingerprint matches; otherwise raise `IdempotencyConflict` (HTTP **409** in an API wrapper).
3. If the key is new, insert the booking (with `UNIQUE(slot)` to prevent slot collisions), persist the key/fingerprint/result receipt, **then** commit both writes together. An occupied slot raises `SlotUnavailable` (also usually HTTP **409**).
4. If anything fails before commit, the transaction rolls back **both** writes. Two concurrent requests sharing this SQLite database cannot pass the check-and-act gate separately.

In a real HTTP service, the server must enforce the key in its **own durable store**. A client-provided key is not a security credential: authenticate the caller, scope keys to a tenant/business operation, validate booking payloads and apply appropriate retention policies.

## Combining the sender with the receiver

```python
from agent_action_receipt import run_once, reconcile_confirmed, UncertainOutcome
from booking_receiver import book_slot, lookup_booking_by_key

key = "tenant7:lead42:first-appointment"
payload = {"customer_id": "customer-42", "slot": "2026-11-10T10:00"}
sender_db = "sender.sqlite3"
receiver_db = "receiver.sqlite3"

def send_booking():
    # Real implementation sends key + payload to the provider/receiver.
    return book_slot(receiver_db, key, payload["customer_id"], payload["slot"])

try:
    result = run_once(sender_db, key, payload, send_booking)
except UncertainOutcome:
    # Do not blindly retry if sender state is still pending.
    verified = lookup_booking_by_key(receiver_db, key, **payload)
    if verified is None:
        raise  # Unresolved is NOT proof that another attempt is safe.
    reconcile_confirmed(sender_db, key, payload, verified)
    result = verified
```

If the receiver commits but the network response is lost, the **sender remains `pending`**; querying the receiver can establish a confirmed result. `test_lost_ack_uses_receiver_lookup_to_reconcile_caller` exercises this exact sequence.

## Important limitations — not "exactly once" across services

- The receiver demonstration is atomic **only because the booking record and its receipt live in the same SQLite transaction/database**. It does not turn an external calendar API, SMS provider or payment service into an atomic participant. For those, use **provider-enforced idempotency**, an authoritative status lookup, and/or an outbox + reconciliation workflow appropriate to the provider's guarantees.
- The sender demonstration requires the **same durable receipt database and stable action ID** on every attempt. Other hosts using different local databases, destroyed state or a freshly generated key can still duplicate work.
- `pending` means *unknown or in flight*, **not failed**. An absent receipt on a remote lookup is not necessarily conclusive (e.g. stale replicas, delayed processing). Do not clear it or auto-retry without a proven resolution policy.
- Idempotency has a scope and retention period; key reuse with changed arguments must fail. Receiver-side enforcement, isolation, and durable storage are mandatory for any useful guarantee.

This is a teaching example for engineering discussions, not a production booking/payment API. Its purpose is to show the **boundary between creating a key and enforcing one**.
