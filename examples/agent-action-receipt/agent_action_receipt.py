"""Tiny SQLite receipt gate for one-shot agent side effects. Python 3.10+, stdlib only.

Demonstrates at-most-one *local invocation attempt per stable action ID*, not
exactly-once remote delivery. A crash can leave a PENDING record indefinitely.
Use provider-side idempotency keys when supported; reconcile unknowns externally.
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Callable


class UncertainOutcome(RuntimeError):
    """An action may be running or may already have succeeded. Do not retry."""


class ActionIdCollision(ValueError):
    """The same action ID was reused with different arguments."""


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(payload: Any) -> str:
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


def _db(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("""
        CREATE TABLE IF NOT EXISTS action_receipts (
            action_id TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('pending', 'completed')),
            result_json TEXT
        )
    """)
    return con


def run_once(
    path: str | Path,
    action_id: str,
    payload: Any,
    effect: Callable[[], Any],
) -> Any:
    """Reserve an action before invoking its side effect; block ambiguous replay.

    action_id must come from a durable business event, NEVER a random ID per retry.
    Both the payload and effect's return value must be JSON serializable.
    """
    if not isinstance(action_id, str) or not action_id.strip():
        raise ValueError("action_id must be a stable, nonempty string")
    fingerprint = _fingerprint(payload)
    con = _db(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT fingerprint, state, result_json FROM action_receipts WHERE action_id=?",
            (action_id,),
        ).fetchone()
        if row is not None:
            if row[0] != fingerprint:
                raise ActionIdCollision("action_id reused with different payload")
            if row[1] == "pending":
                raise UncertainOutcome("pending: action may have happened; reconcile before retry")
            con.commit()
            return json.loads(row[2])

        con.execute(
            "INSERT INTO action_receipts (action_id, fingerprint, state) VALUES (?, ?, 'pending')",
            (action_id, fingerprint),
        )
        con.commit()  # durable BEFORE calling an external service

        result = effect()  # a timeout/exception leaves 'pending' on purpose
        result_json = _json(result)
        con.execute("BEGIN IMMEDIATE")
        con.execute(
            "UPDATE action_receipts SET state='completed', result_json=? WHERE action_id=?",
            (result_json, action_id),
        )
        con.commit()
        return result
    except BaseException:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()


def reconcile_confirmed(
    path: str | Path,
    action_id: str,
    payload: Any,
    confirmed_result: Any,
) -> None:
    """Mark completed ONLY after independently verifying the external side effect.

    Caller must query the external system using its durable event/provider ID.
    An LLM's assertion is not verification. Never call this on a guess.
    """
    fingerprint = _fingerprint(payload)
    result_json = _json(confirmed_result)
    con = _db(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT fingerprint FROM action_receipts WHERE action_id=?", (action_id,)
        ).fetchone()
        if row is None:
            raise ValueError("unknown action_id: no prior reservation")
        if row[0] != fingerprint:
            raise ActionIdCollision("action_id reused with different payload")
        con.execute(
            "UPDATE action_receipts SET state='completed', result_json=? WHERE action_id=?",
            (result_json, action_id),
        )
        con.commit()
    except BaseException:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()
