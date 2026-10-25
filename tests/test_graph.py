"""Tests for the KAVI Task Contract v1 LangGraph reference implementation."""

import pytest

from task_contract import (
    owner_for_lane,
    read_ledger,
    run_task,
    validate_task,
    verify_output,
)


def make_task(**overrides):
    task = {
        "TASK_ID": "KAVI-TEST-001",
        "OWNER": "",
        "lane": "career",
        "OBJECTIVE": "Reconcile the job application tracker",
        "DEFINITION_OF_DONE": "Tracker reconciliation summary delivered",
        "deliverable": "Tracker reconciliation summary delivered",
    }
    task.update(overrides)
    return task


def test_intake_rejects_missing_definition_of_done():
    task = make_task()
    del task["DEFINITION_OF_DONE"]
    with pytest.raises(ValueError, match="DEFINITION_OF_DONE"):
        validate_task(task)


def test_intake_rejects_blank_objective_and_unknown_owner():
    with pytest.raises(ValueError, match="OBJECTIVE"):
        validate_task(make_task(OBJECTIVE="  "))
    with pytest.raises(ValueError, match="unknown OWNER"):
        validate_task(make_task(OWNER="Nobody"))


def test_routing_rule_by_lane():
    assert owner_for_lane("career") == "ChatGPT"
    assert owner_for_lane("strategy") == "ChatGPT"
    assert owner_for_lane("runtime") == "Hermes"
    assert owner_for_lane("engineering") == "Hermes"
    assert owner_for_lane("ops") == "Legend"
    assert owner_for_lane("ecommerce") == "Legend"
    assert owner_for_lane("decision") is None
    with pytest.raises(ValueError, match="unknown lane"):
        owner_for_lane("banana")


def test_verify_keyword_logic():
    passed, missing = verify_output(
        "Tracker reconciliation summary delivered", "Tracker reconciliation summary delivered"
    )
    assert passed and missing == []
    passed, missing = verify_output("Health report generated", "uptime metrics evidence")
    assert not passed and "uptime" in missing


def test_pass_task_ends_completed_verified(tmp_path):
    ledger = tmp_path / "ledger.json"
    final = run_task(make_task(), ledger)
    assert final["status"] == "COMPLETED_VERIFIED"
    records = read_ledger(ledger)
    assert len(records) == 1
    assert records[0]["task_id"] == "KAVI-TEST-001"
    assert records[0]["owner"] == "ChatGPT"  # career lane routing
    assert records[0]["status"] == "COMPLETED_VERIFIED"


def test_failing_task_ends_blocked_with_reason(tmp_path):
    ledger = tmp_path / "ledger.json"
    task = make_task(
        TASK_ID="KAVI-TEST-002",
        lane="runtime",
        OBJECTIVE="Check scheduler health",
        DEFINITION_OF_DONE="Health report includes uptime metrics and restart evidence",
        deliverable="Health report generated",
    )
    final = run_task(task, ledger)
    assert final["status"] == "BLOCKED"
    records = read_ledger(ledger)
    assert records[0]["owner"] == "Hermes"  # runtime lane routing
    assert records[0]["status"] == "BLOCKED"
    assert "uptime" in records[0]["reason"]


def test_decision_lane_never_executes(tmp_path):
    ledger = tmp_path / "ledger.json"
    task = make_task(
        TASK_ID="KAVI-TEST-003",
        lane="decision",
        OBJECTIVE="Approve the Wave 2 diet package",
        DEFINITION_OF_DONE="Decision recorded with rationale",
    )
    task.pop("deliverable")
    final = run_task(task, ledger)
    assert final["status"] == "DECISION_NEEDED"
    assert "output" not in final or not final.get("output")
    records = read_ledger(ledger)
    assert len(records) == 1
    assert records[0]["status"] == "DECISION_NEEDED"
    assert "output" not in records[0]
