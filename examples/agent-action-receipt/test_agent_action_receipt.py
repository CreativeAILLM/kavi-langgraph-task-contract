import tempfile
import threading
import unittest
from pathlib import Path

from agent_action_receipt import (
    ActionIdCollision, UncertainOutcome, reconcile_confirmed, run_once,
)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.db = Path(self.dir.name) / "receipts.sqlite3"

    def tearDown(self):
        self.dir.cleanup()

    def test_duplicate_returns_cached_result_not_repeat_side_effect(self):
        calls = []
        do = lambda: calls.append("sent") or {"provider_id": "sms_42"}
        self.assertEqual(run_once(self.db, "sms:lead42:first_reply", {"to": "test"}, do), {"provider_id": "sms_42"})
        self.assertEqual(run_once(self.db, "sms:lead42:first_reply", {"to": "test"}, do), {"provider_id": "sms_42"})
        self.assertEqual(calls, ["sent"])

    def test_changed_payload_same_id_is_rejected(self):
        run_once(self.db, "charge:order42", {"total": 100}, lambda: {"ok": True})
        with self.assertRaises(ActionIdCollision):
            run_once(self.db, "charge:order42", {"total": 200}, lambda: {"ok": True})

    def test_timeout_blocks_retry_until_verified(self):
        calls = []
        def maybe_delivered():
            calls.append("sent")
            raise TimeoutError("response lost after external send")
        with self.assertRaises(TimeoutError):
            run_once(self.db, "sms:lead42:first_reply", {"text": "hello"}, maybe_delivered)
        with self.assertRaises(UncertainOutcome):
            run_once(self.db, "sms:lead42:first_reply", {"text": "hello"}, maybe_delivered)
        self.assertEqual(calls, ["sent"])
        # Simulate an independent provider lookup confirming the sent message.
        reconcile_confirmed(self.db, "sms:lead42:first_reply", {"text": "hello"}, {"provider_id": "sms_42"})
        self.assertEqual(run_once(self.db, "sms:lead42:first_reply", {"text": "hello"}, maybe_delivered), {"provider_id": "sms_42"})
        self.assertEqual(calls, ["sent"])

    def test_second_caller_cannot_execute_pending_action(self):
        started = threading.Event()
        release = threading.Event()
        calls = []
        errors = []
        def side_effect():
            calls.append(1)
            started.set()
            release.wait(timeout=5)
            return {"ok": True}
        def first():
            try:
                run_once(self.db, "appointment:42", {"slot": "10"}, side_effect)
            except BaseException as ex:
                errors.append(ex)
        thread = threading.Thread(target=first)
        thread.start()
        self.assertTrue(started.wait(timeout=3))
        try:
            with self.assertRaises(UncertainOutcome):
                run_once(self.db, "appointment:42", {"slot": "10"}, side_effect)
        finally:
            release.set()
            thread.join(timeout=5)
        self.assertEqual(calls, [1])
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
