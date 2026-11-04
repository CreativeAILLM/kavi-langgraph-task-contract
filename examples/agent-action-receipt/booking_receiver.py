"""Receiver-side, atomic SQLite booking/idempotency demonstration.

The booking AND its idempotency receipt commit in ONE local SQLite transaction.
This guarantee does NOT extend to an external calendar/payment API. Python 3.10+.
"""

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any


class IdempotencyConflict(ValueError):
    """An existing key is bound to a different request body."""


class SlotUnavailable(ValueError):
    """Another booking already owns the requested slot."""


def _payload(customer_id: str, slot: str) -> dict[str, str]:
    if not isinstance(customer_id, str) or not customer_id.strip():
        raise ValueError("customer_id must be a nonempty string")
    if not isinstance(slot, str) or not slot.strip():
        raise ValueError("slot must be a nonempty string")
    return {"customer_id": customer_id, "slot": slot}


def _key(key: str) -> None:
    if not isinstance(key, str) or not key.strip():
        raise ValueError("idempotency_key must be a stable, nonempty string")


def _fingerprint(payload: dict[str, str]) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _connect(path: str | Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=10, isolation_level=None)
    con.execute("PRAGMA busy_timeout=10000")
    con.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            booking_id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            slot TEXT NOT NULL UNIQUE
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS booking_receipts (
            idempotency_key TEXT PRIMARY KEY,
            fingerprint TEXT NOT NULL,
            booking_id INTEGER NOT NULL REFERENCES bookings(booking_id),
            result_json TEXT NOT NULL
        )
    """)
    con.execute("PRAGMA foreign_keys=ON")
    return con


def book_slot(
    path: str | Path, idempotency_key: str, customer_id: str, slot: str
) -> dict[str, Any]:
    """Check the RECEIVED key before acting; commit booking + receipt atomically.

    BEGIN IMMEDIATE serializes writers across connections using this SAME DB.
    A matching retry returns the stored response, without inserting a booking.
    """
    _key(idempotency_key)
    payload = _payload(customer_id, slot)
    fingerprint = _fingerprint(payload)
    con = _connect(path)
    try:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT fingerprint, result_json FROM booking_receipts WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if row is not None:
            if row[0] != fingerprint:
                raise IdempotencyConflict("key already belongs to a different booking request")
            result = json.loads(row[1])
            con.commit()
            return result

        try:
            cursor = con.execute(
                "INSERT INTO bookings (customer_id, slot) VALUES (?, ?)",
                (customer_id, slot),
            )
        except sqlite3.IntegrityError as exc:
            raise SlotUnavailable("slot is already booked") from exc
        result = {"booking_id": cursor.lastrowid, **payload}
        con.execute(
            "INSERT INTO booking_receipts (idempotency_key, fingerprint, booking_id, result_json) "
            "VALUES (?, ?, ?, ?)",
            (idempotency_key, fingerprint, cursor.lastrowid, json.dumps(result, sort_keys=True)),
        )
        con.commit()
        return result
    except BaseException:
        if con.in_transaction:
            con.rollback()
        raise
    finally:
        con.close()


def lookup_booking_by_key(
    path: str | Path, idempotency_key: str, customer_id: str, slot: str
) -> dict[str, Any] | None:
    """Authoritative receiver-side lookup for reconciliation; no write or retry."""
    _key(idempotency_key)
    fingerprint = _fingerprint(_payload(customer_id, slot))
    con = _connect(path)
    try:
        row = con.execute(
            "SELECT fingerprint, result_json FROM booking_receipts WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None  # Not found is NOT proof a remote operation is safe to retry.
        if row[0] != fingerprint:
            raise IdempotencyConflict("key already belongs to a different booking request")
        return json.loads(row[1])
    finally:
        con.close()
