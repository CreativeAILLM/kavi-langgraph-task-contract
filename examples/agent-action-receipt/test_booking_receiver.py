import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from agent_action_receipt import UncertainOutcome, reconcile_confirmed, run_once
from booking_receiver import (
    IdempotencyConflict, SlotUnavailable, book_slot, lookup_booking_by_key,
)


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "receiver.sqlite3"
        self.caller_db = Path(self.tmp.name) / "caller.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def count_bookings(self):
        with sqlite3.connect(self.db) as con:
            return con.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]

    def test_identical_key_is_checked_before_a_second_booking(self):
        first = book_slot(self.db, "event:42", "customer-42", "2026-11-10T10:00")
        self.assertEqual(first, book_slot(self.db, "event:42", "customer-42", "2026-11-10T10:00"))
        self.assertEqual(self.count_bookings(), 1)

    def test_changed_payload_on_same_key_is_rejected(self):
        book_slot(self.db, "event:42", "customer-42", "10:00")
        with self.assertRaises(IdempotencyConflict):
            book_slot(self.db, "event:42", "customer-42", "11:00")
        self.assertEqual(self.count_bookings(), 1)

    def test_different_key_cannot_take_an_occupied_slot(self):
        book_slot(self.db, "event:42", "customer-42", "10:00")
        with self.assertRaises(SlotUnavailable):
            book_slot(self.db, "event:43", "customer-43", "10:00")
        self.assertEqual(self.count_bookings(), 1)

    def test_concurrent_receivers_commit_one_booking(self):
        num_workers = 6
        barrier = threading.Barrier(num_workers)
        successes = []
        errors = []

        def worker():
            try:
                barrier.wait(timeout=4)
                successes.append(book_slot(self.db, "event:42", "customer-42", "10:00"))
            except BaseException as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(num_workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=12)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(successes), num_workers)
        self.assertEqual(successes, [successes[0]] * num_workers)
        self.assertEqual(self.count_bookings(), 1)

    def test_receipt_insert_failure_rolls_back_booking(self):
        # The receiver's booking write and receipt write MUST be all-or-nothing.
        book_slot(self.db, "setup", "setup-customer", "setup-slot")
        with sqlite3.connect(self.db) as con:
            con.execute("""
                CREATE TRIGGER fail_receipt BEFORE INSERT ON booking_receipts
                BEGIN SELECT RAISE(FAIL, 'simulated receipt-store failure'); END
            """)
        with self.assertRaises(sqlite3.IntegrityError):
            book_slot(self.db, "event:42", "customer-42", "10:00")
        self.assertEqual(self.count_bookings(), 1)
        self.assertIsNone(lookup_booking_by_key(self.db, "event:42", "customer-42", "10:00"))

    def test_lost_ack_uses_receiver_lookup_to_reconcile_caller(self):
        key, customer, slot = "event:42", "customer-42", "10:00"
        payload = {"customer_id": customer, "slot": slot}

        def network_call_with_lost_ack():
            book_slot(self.db, key, customer, slot)  # receiver COMMITS
            raise TimeoutError("network lost response AFTER receiver committed")

        with self.assertRaises(TimeoutError):
            run_once(self.caller_db, key, payload, network_call_with_lost_ack)
        with self.assertRaises(UncertainOutcome):
            run_once(self.caller_db, key, payload, network_call_with_lost_ack)
        received = lookup_booking_by_key(self.db, key, customer, slot)
        self.assertIsNotNone(received)
        reconcile_confirmed(self.caller_db, key, payload, received)
        self.assertEqual(
            run_once(self.caller_db, key, payload, network_call_with_lost_ack), received
        )
        self.assertEqual(self.count_bookings(), 1)


if __name__ == "__main__":
    unittest.main()
