"""Project-level outcome ledger for organization runs.

The run directory records how a run behaved; nothing recorded whether any of
it was ever used. Across the first thirteen recorded runs the answer was
never, and no surface said so. This ledger is the host-owned memory of value:
one append-only JSONL per project, written at terminal time and at every
human approval or rejection, so the waste rate is a number instead of an
impression.

A candidate is credited only when it is merged (`promotion_applied`) or
explicitly consumed by a successor (`candidate_used`). Accepted-but-unused
work is waste, and the summary reports it as such.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


OUTCOME_SCHEMA = "reccli.organization-outcome.v1"

# Terminal statuses that are successful without producing candidates: the run
# decided that stopping beat proceeding. Not credited, but not waste either.
_NO_OP_STATUSES = {"completed_no_op"}

_EVENT_KINDS = {
    "run_terminal",
    "promotion_applied",
    "promotion_rejected",
    "candidate_used",
}


def outcome_ledger_path(project_root: Path) -> Path:
    return (
        Path(project_root)
        / "devsession" / "agent-organizations" / "outcome-ledger.jsonl"
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def record_outcome_event(
    project_root: Path,
    event: str,
    run_id: str,
    **fields: Any,
) -> Dict[str, Any]:
    """Append one outcome event. Raises on unknown event kinds; callers own
    swallowing failures where a ledger error must not fail the run."""
    if event not in _EVENT_KINDS:
        raise ValueError(f"unknown outcome event kind: {event}")
    record = {
        "schema": OUTCOME_SCHEMA,
        "event": event,
        "run_id": run_id,
        "ts": _utc_now(),
        **fields,
    }
    path = outcome_ledger_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def summarize_outcomes(project_root: Path) -> Optional[Dict[str, Any]]:
    """Fold the ledger into the numbers that matter: how many terminal runs,
    how many produced anything a human merged or a successor consumed, and
    what the unused remainder cost. Returns None when no ledger exists."""
    path = outcome_ledger_path(project_root)
    if not path.is_file():
        return None
    terminal: Dict[str, Dict[str, Any]] = {}
    used_runs: set = set()
    rejected_runs: set = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        event = record.get("event")
        run_id = str(record.get("run_id") or "")
        if event == "run_terminal" and run_id:
            terminal[run_id] = record
        elif event in {"promotion_applied", "candidate_used"} and run_id:
            used_runs.add(run_id)
        elif event == "promotion_rejected" and run_id:
            rejected_runs.add(run_id)

    def _tokens(record: Dict[str, Any]) -> Dict[str, int]:
        usage = record.get("usage") or {}
        return {
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
        }

    no_op = {
        run_id for run_id, record in terminal.items()
        if str(record.get("terminal_status") or "") in _NO_OP_STATUSES
    }
    productive = {run_id for run_id in terminal if run_id in used_runs}
    # Waste: terminal, not used, and not a deliberate stop.
    waste = set(terminal) - productive - no_op
    waste_tokens = {"input_tokens": 0, "output_tokens": 0}
    total_tokens = {"input_tokens": 0, "output_tokens": 0}
    for run_id, record in terminal.items():
        tokens = _tokens(record)
        for key in total_tokens:
            total_tokens[key] += tokens[key]
            if run_id in waste:
                waste_tokens[key] += tokens[key]
    terminal_count = len(terminal)
    denominator = max(1, terminal_count - len(no_op))
    return {
        "schema": OUTCOME_SCHEMA,
        "terminal_runs": terminal_count,
        "productive_runs": len(productive),
        "no_op_runs": len(no_op),
        "rejected_runs": len(rejected_runs & set(terminal)),
        "waste_runs": len(waste),
        "waste_rate": (
            round(len(waste) / denominator, 4) if terminal_count else 0.0
        ),
        "total_tokens": total_tokens,
        "waste_tokens": waste_tokens,
        "ledger_path": str(path),
    }
