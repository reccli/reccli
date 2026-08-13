"""Tests for the host-enforced organization admission gate."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from reccli.organization import create_run_request, organization_root
from reccli.organization_admission import (
    admission_for_approved_successor,
    admission_for_continuation,
    render_admission_prompt,
    validate_admission,
)


VALID = {
    "consumer": {
        "name": "will",
        "type": "human",
        "intended_use": "merge the reviewed fix into main and ship it",
    },
    "work_class": "deployable_artifact",
    "done_condition": (
        "BM1004 sphere controls pass 19/19 with the fix merged-ready"
    ),
    "stop_conditions": [
        "the evaluator shows no improvement over baseline after two contracts",
    ],
}


def _init_project(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root, check=True,
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "app.py").write_text("print('hello')\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)


class ValidateAdmissionTests(unittest.TestCase):
    def test_valid_block_normalizes(self):
        normalized = validate_admission(dict(VALID))
        self.assertEqual(
            normalized["schema"], "reccli.organization-admission.v1",
        )
        self.assertEqual(normalized["consumer"]["name"], "will")
        self.assertEqual(normalized["work_class"], "deployable_artifact")
        self.assertEqual(normalized["origin"], "direct")
        self.assertEqual(len(normalized["stop_conditions"]), 1)

    def test_missing_block_names_every_requirement(self):
        with self.assertRaises(ValueError) as caught:
            validate_admission(None)
        message = str(caught.exception)
        for token in (
            "consumer", "work_class", "done", "stop conditions",
        ):
            self.assertIn(token, message)

    def test_all_defects_reported_in_one_error(self):
        with self.assertRaises(ValueError) as caught:
            validate_admission({
                "consumer": {"name": "", "type": "robot", "intended_use": "x"},
                "work_class": "vibes",
                "done_condition": "done",
                "stop_conditions": [],
            })
        message = str(caught.exception)
        self.assertIn("consumer.name", message)
        self.assertIn("consumer.type", message)
        self.assertIn("consumer.intended_use", message)
        self.assertIn("work_class", message)
        self.assertIn("done_condition", message)
        self.assertIn("stop_conditions", message)

    def test_unknown_fields_are_rejected(self):
        block = dict(VALID)
        block["expected_value_usd"] = 500
        with self.assertRaisesRegex(ValueError, "unknown admission fields"):
            validate_admission(block)

    def test_render_contains_the_contract(self):
        text = render_admission_prompt(validate_admission(dict(VALID)))
        self.assertIn("Downstream consumer: will (human)", text)
        self.assertIn("Meaningful-work class: deployable_artifact", text)
        self.assertIn("Done condition", text)
        self.assertIn("Stop conditions:", text)


class LaunchGateTests(unittest.TestCase):
    def test_launch_without_admission_is_rejected_before_any_effect(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            with patch(
                "reccli.organization.shutil.which",
                return_value="/fake/claude",
            ):
                with self.assertRaisesRegex(ValueError, "admission"):
                    create_run_request(
                        str(root), "Ship it.", provider="claude",
                    )
            self.assertFalse(organization_root(root).exists())

    def test_admitted_launch_persists_the_contract_everywhere(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _init_project(root)
            with patch(
                "reccli.organization.shutil.which",
                return_value="/fake/claude",
            ):
                request = create_run_request(
                    str(root), "Ship it.", provider="claude",
                    admission=dict(VALID),
                )
            run_dir = Path(request["run_dir"])
            self.assertEqual(
                request["admission"]["consumer"]["name"], "will",
            )
            persisted = json.loads(
                (run_dir / "admission.json").read_text(encoding="utf-8")
            )
            self.assertEqual(persisted["work_class"], "deployable_artifact")
            status = json.loads(
                (run_dir / "status.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                status["admission"]["done_condition"],
                persisted["done_condition"],
            )


class SuccessorAdmissionTests(unittest.TestCase):
    def test_continuation_carries_the_parent_contract(self):
        carried = admission_for_continuation(
            validate_admission(dict(VALID)), "parent-run",
        )
        self.assertEqual(carried["origin"], "terminal-continuation")
        self.assertEqual(carried["carried_from_run_id"], "parent-run")
        self.assertEqual(
            carried["done_condition"], VALID["done_condition"].strip(),
        )

    def test_continuation_from_pre_admission_parent_carries_nothing(self):
        self.assertIsNone(admission_for_continuation(None, "parent-run"))

    def test_approved_successor_synthesizes_from_the_human_decision(self):
        block = admission_for_approved_successor(None, "parent-run", "will")
        self.assertEqual(block["consumer"]["name"], "will")
        self.assertEqual(block["work_class"], "resolved_decision")
        self.assertEqual(block["origin"], "approved-successor")

    def test_approved_successor_prefers_the_parent_contract(self):
        block = admission_for_approved_successor(
            validate_admission(dict(VALID)), "parent-run", "will",
        )
        self.assertEqual(block["work_class"], "deployable_artifact")
        self.assertEqual(block["carried_from_run_id"], "parent-run")


if __name__ == "__main__":
    unittest.main()
