"""LangGraph wiring for the KAVI Task Contract v1 flow.

Graph shape:

    START -> intake -> route --(lane == decision)--> decision_needed -> END
                          |
                          +--(otherwise)--> execute -> verify
                                                              |
                                          (pass or fail, conditional edge)
                                                              v
                                                        ledger_append -> END

Statuses follow the agreed multi-agent model: COMPLETED_VERIFIED only after
the verify node passes, BLOCKED with a reason when it fails, DECISION_NEEDED
for the decision lane.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .contract import owner_for_lane, stub_execute, validate_task, verify_output


class TaskState(TypedDict, total=False):
    task: dict
    owner: str
    route: str  # "execute" or "decision"
    output: str
    verified: bool
    missing: list
    status: str
    reason: str
    record: dict


def append_ledger(ledger_path: Path, record: dict) -> dict:
    """Append one record to the local JSON ledger file and return it."""
    ledger_path = Path(ledger_path)
    records = []
    if ledger_path.exists():
        records = json.loads(ledger_path.read_text(encoding="utf-8"))
    records.append(record)
    ledger_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    return record


def read_ledger(ledger_path: Path) -> list:
    ledger_path = Path(ledger_path)
    if not ledger_path.exists():
        return []
    return json.loads(ledger_path.read_text(encoding="utf-8"))


def build_graph(ledger_path: str | Path):
    """Compile the contract graph bound to one ledger file."""
    ledger_path = Path(ledger_path)

    def intake(state: TaskState) -> dict:
        task = validate_task(state["task"])
        return {"task": task}

    def route(state: TaskState) -> dict:
        owner = owner_for_lane(state["task"]["lane"])
        if owner is None:
            return {"route": "decision", "owner": ""}
        return {"route": "execute", "owner": owner}

    def decision_needed(state: TaskState) -> dict:
        task = state["task"]
        record = {
            "task_id": task["TASK_ID"],
            "owner": task.get("OWNER", "") or "Onur",
            "status": "DECISION_NEEDED",
            "objective": task["OBJECTIVE"],
            "reason": "lane 'decision': routed to a human decision, never executed",
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        append_ledger(ledger_path, record)
        return {"status": "DECISION_NEEDED", "record": record}

    def execute(state: TaskState) -> dict:
        return {"output": stub_execute(state["task"], state["owner"])}

    def verify(state: TaskState) -> dict:
        passed, missing = verify_output(
            state["output"], state["task"]["DEFINITION_OF_DONE"]
        )
        return {"verified": passed, "missing": missing}

    def after_verify(state: TaskState) -> str:
        return "pass" if state["verified"] else "fail"

    def ledger_append(state: TaskState) -> dict:
        task = state["task"]
        base = {
            "task_id": task["TASK_ID"],
            "owner": state["owner"],
            "objective": task["OBJECTIVE"],
            "output": state["output"],
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if state["verified"]:
            record = {**base, "status": "COMPLETED_VERIFIED"}
        else:
            record = {
                **base,
                "status": "BLOCKED",
                "reason": "DEFINITION_OF_DONE keywords missing from output: "
                + ", ".join(state["missing"]),
            }
        append_ledger(ledger_path, record)
        return {"status": record["status"], "record": record}

    graph = StateGraph(TaskState)
    graph.add_node("intake", intake)
    graph.add_node("route", route)
    graph.add_node("execute", execute)
    graph.add_node("verify", verify)
    graph.add_node("ledger_append", ledger_append)
    graph.add_node("decision_needed", decision_needed)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "route")
    graph.add_conditional_edges(
        "route",
        lambda s: s["route"],
        {"execute": "execute", "decision": "decision_needed"},
    )
    graph.add_edge("execute", "verify")
    graph.add_conditional_edges(
        "verify", after_verify, {"pass": "ledger_append", "fail": "ledger_append"}
    )
    graph.add_edge("ledger_append", END)
    graph.add_edge("decision_needed", END)
    return graph.compile()


def run_task(task: dict, ledger_path: str | Path) -> dict:
    """Run one task dict through the graph; returns the final state."""
    app = build_graph(ledger_path)
    return app.invoke({"task": task})
