"""Pure contract logic for the KAVI Task Contract v1 reference implementation.

No I/O, no network, no LLM. Everything here is deterministic so the graph
behaviour can be unit tested without running LangGraph at all.
"""

from __future__ import annotations

import re

# Fields every KAVI Task Contract v1 task must carry at intake.
REQUIRED_FIELDS = ("TASK_ID", "OWNER", "OBJECTIVE", "DEFINITION_OF_DONE")

# Fields that must be non-empty before a task may execute. OWNER is allowed to
# be blank at intake because the route node assigns it from the lane; the key
# itself must still be present so the contract shape is explicit.
NONEMPTY_FIELDS = ("TASK_ID", "OBJECTIVE", "DEFINITION_OF_DONE")

OWNERS = ("Legend", "ChatGPT", "Hermes")

# Lane -> owner routing rule. "decision" is handled separately: it never
# reaches execute, it terminates as DECISION_NEEDED.
LANE_TO_OWNER = {
    "ops": "Legend",
    "ecommerce": "Legend",
    "video": "Legend",
    "career": "ChatGPT",
    "strategy": "ChatGPT",
    "sales": "ChatGPT",
    "runtime": "Hermes",
    "engineering": "Hermes",
    "infra": "Hermes",
}

STOPWORDS = {
    "with", "that", "this", "must", "shall", "will", "have", "been",
    "into", "from", "and", "the", "for", "are", "was",
}


def validate_task(task: dict) -> dict:
    """Validate a raw task dict against the contract. Returns the task.

    Raises ValueError listing every problem found (missing keys, blank
    required values, unknown preset OWNER).
    """
    if not isinstance(task, dict):
        raise ValueError("task must be a dict")
    problems = []
    for field in REQUIRED_FIELDS:
        if field not in task:
            problems.append(f"missing field: {field}")
    for field in NONEMPTY_FIELDS:
        if field in task and not str(task[field]).strip():
            problems.append(f"blank field: {field}")
    owner = str(task.get("OWNER", "")).strip()
    if owner and owner not in OWNERS:
        problems.append(f"unknown OWNER: {owner!r} (expected one of {OWNERS})")
    if "lane" not in task or not str(task["lane"]).strip():
        problems.append("missing field: lane")
    if problems:
        raise ValueError("invalid task contract: " + "; ".join(problems))
    return task


def owner_for_lane(lane: str) -> str | None:
    """Return the owner for a lane, or None for the decision lane."""
    lane = str(lane).strip().lower()
    if lane == "decision":
        return None
    if lane not in LANE_TO_OWNER:
        raise ValueError(f"unknown lane: {lane!r}")
    return LANE_TO_OWNER[lane]


def stub_execute(task: dict, owner: str) -> str:
    """Deterministic stand-in for real work. No external calls.

    The output echoes the objective and the task's optional 'deliverable'
    text, which is what verify later checks the DEFINITION_OF_DONE against.
    """
    deliverable = str(task.get("deliverable", "")).strip() or "none"
    return (
        f"{owner} executed task {task['TASK_ID']}. "
        f"Objective: {task['OBJECTIVE']}. "
        f"Deliverable: {deliverable}."
    )


def dod_keywords(definition_of_done: str) -> list[str]:
    """Extract checkable keywords from a DEFINITION_OF_DONE string."""
    tokens = re.findall(r"[a-z0-9]+", definition_of_done.lower())
    return sorted({t for t in tokens if len(t) >= 4 and t not in STOPWORDS})


def verify_output(output: str, definition_of_done: str) -> tuple[bool, list[str]]:
    """Pass only if every DEFINITION_OF_DONE keyword appears in the output.

    Returns (passed, missing_keywords). This mirrors the KAVI evidence rule:
    'done' is a claim, the deliverable text is the evidence checked.
    """
    haystack = output.lower()
    missing = [kw for kw in dod_keywords(definition_of_done) if kw not in haystack]
    return (not missing, missing)
