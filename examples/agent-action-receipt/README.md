# Free Agent Action Receipt Gate

**The problem:** an agent calls a tool to send a message/book a slot/charge a card. The tool performs the side effect, but the network times out before the agent sees the response. If the agent retries, it can perform the action twice.

**The fix:** assign a stable business-event action ID, persist a `pending` receipt before the call, and treat every unknown outcome as **uncertain**, not as a safe retry. After confirmed success, save the provider result; the next identical invocation returns the saved result. If the process crashes or times out, independently reconcile against the provider before changing state.

This minimal **Python 3.10+ / zero-dependency** educational sample uses one local SQLite database. It does **not** promise exactly-once remote execution. It blocks repeat calls that use the same receipt database and stable action ID. If the database is lost, the operator changes the ID, or another host uses a different database, duplication is still possible. For production/multi-worker systems use a shared transactional store or durable workflow and, whenever supported, pass the *same provider-side idempotency key* on retries.

## Try it

```bash
python3 -m unittest -v test_agent_action_receipt.py
```

## Drop-in example

```python
from agent_action_receipt import run_once, UncertainOutcome

# This ID comes from your durable lead/event record, not from the model.
lead_event_id = "lead42-inbound-first-response"
payload = {"lead_id": "lead42", "message": "Thanks for calling!"}

def send_sms():
    # Replace with provider call; pass lead_event_id as provider idempotency key
    # if the provider supports one.
    return {"provider_message_id": "sms_123"}

try:
    receipt = run_once("receipts.sqlite3", lead_event_id, payload, send_sms)
    print(receipt)
except UncertainOutcome:
    print("STOP. Query provider/event ledger; do not blindly retry.")
```

Three important distinctions:

1. `pending` does not mean failure. It means **the outcome is not known** (or a first call is in flight).
2. A manual `reconcile_confirmed(...)` is allowed only after a provider query proves the effect happened. If it did not happen, a production adapter must have an explicit, audited resolution policy. **Don't just delete the pending row to force a retry.**
3. For external payments and high-impact actions, prefer provider-native idempotency and authoritative lookup. SQLite is only the local teaching example, not an infrastructure-wide guarantee.

**Reproducible checks:** deduplication after success, payload drift detection, ambiguous-timeout lockout with reconciliation, and two concurrent callers racing for the same ID.

Background: Temporal documents at-least-once activity execution and idempotency. AWS Durable Execution documents at-most-once versus at-least-once retry semantics. This is a deliberately smaller self-contained pattern, not a replacement for those systems.
