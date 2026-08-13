"""Host-enforced task admission for organization runs.

Nine recorded runs across two projects processed over a billion input tokens
and merged nothing. The forensic mechanism: every mechanical gate was
denominated in process (messages, candidates, reviews), so producing and
adjudicating paper satisfied every gate while ``canonical_effects_applied``
stayed false. Admission is the missing plane. Before any supervisor launches,
the mission must name who consumes the result, which class of meaningful work
it is, what falsifiable condition ends it, and what conditions stop it early.

The gate is enforced here, in host code, at ``create_run_request`` time. It is
never delegated to prompt text agents are asked to honor: the recorded runs
already demonstrated that agent-facing procedure is satisfiable by prose.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


ADMISSION_SCHEMA = "reccli.organization-admission.v1"

# The six classes of meaningful work. Anything that fits none of them is not
# admitted, no matter how plausible the mission prose reads.
MEANINGFUL_WORK_CLASSES = frozenset({
    "deployable_artifact",
    "resolved_decision",
    "uncertainty_reduction",
    "hypothesis_test",
    "risk_prevention",
    "reusable_capability",
})

CONSUMER_TYPES = frozenset({"human", "service", "workflow"})

# Crude honesty forcing functions. Falsifiability cannot be machine-checked,
# but a done condition shorter than this is a label, not a condition.
_MIN_CONDITION_CHARS = 12

_EXAMPLE_BLOCK = """{
  "consumer": {
    "name": "will",
    "type": "human",
    "intended_use": "merge the reviewed fix into main and ship it"
  },
  "work_class": "deployable_artifact",
  "done_condition": "BM1004 sphere controls pass 19/19 with the fix merged-ready on the integration branch",
  "stop_conditions": [
    "the evaluator shows no improvement over baseline after two contracts",
    "the fix requires changing protected evidence paths"
  ]
}"""


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_admission(admission: Any) -> Dict[str, Any]:
    """Validate and normalize one admission block, or raise with every defect.

    All problems are reported in a single actionable error so a caller fixes
    the block in one attempt instead of discovering requirements one launch
    failure at a time.
    """
    problems: List[str] = []
    if not isinstance(admission, dict):
        raise ValueError(
            "organization launches require an admission block naming the "
            "downstream consumer, meaningful-work class, falsifiable done "
            "condition, and stop conditions. Example:\n" + _EXAMPLE_BLOCK
        )

    consumer = admission.get("consumer")
    normalized_consumer: Dict[str, str] = {}
    if not isinstance(consumer, dict):
        problems.append(
            "consumer must be an object with name, type, and intended_use"
        )
    else:
        if not _is_nonempty_str(consumer.get("name")):
            problems.append("consumer.name must be a non-empty string")
        consumer_type = consumer.get("type")
        if consumer_type not in CONSUMER_TYPES:
            problems.append(
                "consumer.type must be one of: "
                + ", ".join(sorted(CONSUMER_TYPES))
            )
        intended_use = consumer.get("intended_use")
        if (
            not _is_nonempty_str(intended_use)
            or len(intended_use.strip()) < _MIN_CONDITION_CHARS
        ):
            problems.append(
                "consumer.intended_use must state how the result will be "
                f"used (at least {_MIN_CONDITION_CHARS} characters)"
            )
        if not problems:
            normalized_consumer = {
                "name": consumer["name"].strip(),
                "type": consumer_type,
                "intended_use": intended_use.strip(),
            }

    work_class = admission.get("work_class")
    if work_class not in MEANINGFUL_WORK_CLASSES:
        problems.append(
            "work_class must be one of: "
            + ", ".join(sorted(MEANINGFUL_WORK_CLASSES))
        )

    done_condition = admission.get("done_condition")
    if (
        not _is_nonempty_str(done_condition)
        or len(done_condition.strip()) < _MIN_CONDITION_CHARS
    ):
        problems.append(
            "done_condition must be a falsifiable statement of what ends the "
            f"run (at least {_MIN_CONDITION_CHARS} characters)"
        )

    stop_conditions = admission.get("stop_conditions")
    normalized_stops: List[str] = []
    if not isinstance(stop_conditions, list) or not stop_conditions:
        problems.append(
            "stop_conditions must be a non-empty list of conditions under "
            "which the run stops without finishing"
        )
    else:
        for index, condition in enumerate(stop_conditions):
            if not _is_nonempty_str(condition):
                problems.append(
                    f"stop_conditions[{index}] must be a non-empty string"
                )
            else:
                normalized_stops.append(condition.strip())

    unknown = sorted(
        set(admission)
        - {
            "schema", "consumer", "work_class", "done_condition",
            "stop_conditions", "origin", "carried_from_run_id",
        }
    )
    if unknown:
        problems.append(f"unknown admission fields: {', '.join(unknown)}")

    if problems:
        raise ValueError(
            "organization admission rejected:\n- "
            + "\n- ".join(problems)
            + "\n\nExample of a valid admission block:\n" + _EXAMPLE_BLOCK
        )

    normalized: Dict[str, Any] = {
        "schema": ADMISSION_SCHEMA,
        "consumer": normalized_consumer,
        "work_class": work_class,
        "done_condition": done_condition.strip(),
        "stop_conditions": normalized_stops,
        "origin": str(admission.get("origin") or "direct"),
    }
    if admission.get("carried_from_run_id"):
        normalized["carried_from_run_id"] = str(
            admission["carried_from_run_id"]
        ).strip()
    return normalized


def admission_for_continuation(
    parent_admission: Optional[Dict[str, Any]],
    parent_run_id: str,
) -> Optional[Dict[str, Any]]:
    """Carry the standing admission contract onto a terminal-continuation successor.

    The successor mission changes (it is derived from the terminal conclusion)
    but the admission contract is the mission-level authority: same consumer,
    same work class, same done and stop conditions. A parent with no recorded
    admission (a pre-admission run) returns None; the launch surface then
    demands one from the project contract instead of fabricating consent.
    """
    if not parent_admission:
        return None
    carried = dict(parent_admission)
    carried["origin"] = "terminal-continuation"
    carried["carried_from_run_id"] = parent_run_id
    return validate_admission(carried)


def admission_for_approved_successor(
    parent_admission: Optional[Dict[str, Any]],
    parent_run_id: str,
    approver: str,
) -> Dict[str, Any]:
    """Build the successor admission for a human-approved continuation.

    An explicit human approval is itself an admission act by the consumer, so
    a parent that predates admission gets one synthesized from the recorded
    decision rather than blocking the approval the human already made.
    """
    if parent_admission:
        carried = dict(parent_admission)
        carried["origin"] = "approved-successor"
        carried["carried_from_run_id"] = parent_run_id
        return validate_admission(carried)
    return validate_admission({
        "consumer": {
            "name": approver or "human-operator",
            "type": "human",
            "intended_use": (
                "act on the recorded human approval decision for run "
                f"{parent_run_id}"
            ),
        },
        "work_class": "resolved_decision",
        "done_condition": (
            "the approved decision's staged follow-up work is complete and "
            "reviewed for exactly the approved candidate identities"
        ),
        "stop_conditions": [
            "the approved candidate identities no longer exist or fail "
            "re-verification",
        ],
        "origin": "approved-successor",
        "carried_from_run_id": parent_run_id,
    })


def render_admission_prompt(admission: Dict[str, Any]) -> str:
    """Render the admission contract for the bootstrap prompt.

    This is informational: the contract is enforced by the host, and agents
    are told what ends the run rather than asked to police themselves.
    """
    consumer = admission["consumer"]
    stops = "\n".join(f"- {condition}" for condition in admission["stop_conditions"])
    return (
        f"Downstream consumer: {consumer['name']} ({consumer['type']}): "
        f"{consumer['intended_use']}\n"
        f"Meaningful-work class: {admission['work_class']}\n"
        f"Done condition (falsifiable): {admission['done_condition']}\n"
        f"Stop conditions:\n{stops}"
    )
