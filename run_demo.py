#!/usr/bin/env python3
"""Demo: run 3 sample KAVI Task Contract v1 tasks through the LangGraph flow.

1. KAVI-DEMO-001  career lane     -> ChatGPT, deliverable covers the
   DEFINITION_OF_DONE keywords, so it ends COMPLETED_VERIFIED.
2. KAVI-DEMO-002  runtime lane    -> Hermes, deliverable omits required
   DEFINITION_OF_DONE keywords, so verify fails and it ends BLOCKED.
3. KAVI-DEMO-003  decision lane   -> never executes, ends as a
   DECISION_NEEDED terminal record.

The demo resets ledger.json first so the printed ledger is exactly the
result of these three runs. No network, no LLM, deterministic stub work.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from task_contract import read_ledger, run_task

LEDGER = Path(__file__).parent / "ledger.json"

SAMPLE_TASKS = [
    {
        "TASK_ID": "KAVI-DEMO-001",
        "OWNER": "",
        "lane": "career",
        "OBJECTIVE": "Reconcile the job application tracker",
        "DEFINITION_OF_DONE": "Tracker reconciliation summary delivered",
        "deliverable": "Tracker reconciliation summary delivered with source links",
    },
    {
        "TASK_ID": "KAVI-DEMO-002",
        "OWNER": "",
        "lane": "runtime",
        "OBJECTIVE": "Check scheduler health on the Hermes host",
        "DEFINITION_OF_DONE": "Health report includes uptime metrics and restart evidence",
        "deliverable": "Health report generated",
    },
    {
        "TASK_ID": "KAVI-DEMO-003",
        "OWNER": "",
        "lane": "decision",
        "OBJECTIVE": "Approve the Wave 2 diet package (98 unknown jobs)",
        "DEFINITION_OF_DONE": "Decision recorded with rationale",
    },
]


def main() -> None:
    if LEDGER.exists():
        LEDGER.unlink()
    for task in SAMPLE_TASKS:
        final = run_task(task, LEDGER)
        print(f"{task['TASK_ID']}: lane={task['lane']} -> {final['status']}")
    print("\nLedger (ledger.json):")
    print(json.dumps(read_ledger(LEDGER), indent=2))


if __name__ == "__main__":
    main()
