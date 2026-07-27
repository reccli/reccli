"""Tests for feature-boundary derivation and repair.

These two functions decide which feature owns which files, which is what agent
dispatch routes on. Both shipped without coverage, and both were wrong in ways
only a dry run against real data caught: one re-derived authority from evidence,
another shattered directory globs into frozen file lists, a third let a single
deep file claim its whole top-level tree.

The recurring theme is that a boundary must stay a *declaration of intent*. A glob
keeps covering files added later; an enumeration silently stops at what exists
today. Several tests below exist specifically to catch a regression back to
enumeration.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reccli.project.devproject import DevProjectManager, resolve_devproject_path


class CandidateBoundaryTests(unittest.TestCase):
    """_candidate_boundaries: derive the broadest safe boundary for a file set."""

    def setUp(self):
        self.derive = DevProjectManager.__new__(DevProjectManager)._candidate_boundaries

    def test_uncontested_directory_becomes_a_glob(self):
        self.assertEqual(self.derive(["src/app/a.py", "src/app/b.py"]), ["src/app/**"])

    def test_glob_is_never_broader_than_a_files_own_parent(self):
        """A single deep file must not claim its whole top-level tree."""
        self.assertEqual(self.derive(["src/pkg/mod/thing.py"]), ["src/pkg/mod/**"])

    def test_contested_directory_descends_instead_of_enumerating(self):
        """The key property: a conflict narrows the glob, it does not abandon globs."""
        result = self.derive(
            ["apps/web/src/a.ts", "apps/web/src/b.ts", "apps/web/lib/c.ts"],
            others=["apps/web/cypress/"],
        )
        self.assertEqual(result, ["apps/web/lib/**", "apps/web/src/**"])
        self.assertTrue(all(b.endswith("/**") for b in result), "must not fall back to file lists")

    def test_contested_at_the_leaf_falls_back_to_explicit_files(self):
        """When there is no deeper level left, enumeration is the only correct answer."""
        self.assertEqual(
            self.derive(["apps/web/src/a.ts"], others=["apps/web/src/b.ts"]),
            ["apps/web/src/a.ts"],
        )

    def test_nested_globs_are_collapsed(self):
        self.assertEqual(self.derive(["a/x.py", "a/b/y.py", "a/b/c/z.py"]), ["a/**"])

    def test_files_covered_by_a_retained_glob_are_dropped(self):
        result = self.derive(["a/x.py", "a/b/y.py"])
        self.assertEqual(result, ["a/**"])

    def test_top_level_file_has_no_directory_to_glob(self):
        self.assertEqual(self.derive(["README.md"]), ["README.md"])

    def test_no_others_preserves_permissive_behaviour(self):
        """Ten call sites pass no `others`; their behaviour must not change."""
        self.assertEqual(self.derive(["src/scan2param/consolidate.py"]), ["src/scan2param/**"])

    def test_empty_input(self):
        self.assertEqual(self.derive([]), [])


class RepairBoundaryTests(unittest.TestCase):
    """repair_feature_boundaries: narrow only boundaries that actually conflict."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        (self.root / "devsession").mkdir()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, features):
        # Resolve the same way save() does; the filename derives from the project
        # directory, so a hardcoded name would be read but never written.
        path = Path(resolve_devproject_path(self.root))
        path.write_text(json.dumps({
            "format": "devproject", "version": "2.1.0", "project_root": str(self.root),
            "updated_at": "2026-01-01T00:00:00Z", "last_updated_session": None,
            "project": {"name": "t", "description": "t", "status": "active", "source": "manual"},
            "features": features, "project_docs": [], "session_index": [], "proposals": [],
        }, indent=2))
        return path

    def _feature(self, fid, boundaries, files=()):
        return {"feature_id": fid, "title": fid, "description": "", "status": "in-progress",
                "source": "auto", "file_boundaries": list(boundaries),
                "files_touched": list(files), "session_ids": [], "feature_version": 1}

    def test_dry_run_leaves_the_file_byte_identical(self):
        path = self._write([
            self._feature("feat_weak", ["src/shared/**"], ["src/shared/a.py"]),
            self._feature("feat_strong", ["src/shared/**"],
                          ["src/shared/a.py", "src/shared/b.py"]),
        ])
        before = path.read_bytes()
        report = DevProjectManager(self.root).repair_feature_boundaries(dry_run=True)
        self.assertTrue(report["changed"], "expected this case to need repair")
        self.assertEqual(path.read_bytes(), before)

    def test_dry_run_and_apply_agree(self):
        """They diverged once: the loop rewrote boundaries other features then read."""
        self._write([
            self._feature("feat_a", ["src/**"], ["src/a.py"]),
            self._feature("feat_b", ["src/b/**"], ["src/b/b.py"]),
            self._feature("feat_c", ["src/c/**"], ["src/c/c.py"]),
        ])
        manager = DevProjectManager(self.root)
        preview = manager.repair_feature_boundaries(dry_run=True)
        applied = manager.repair_feature_boundaries(dry_run=False)
        self.assertEqual(
            [(c["feature_id"], c["after"]) for c in preview["changed"]],
            [(c["feature_id"], c["after"]) for c in applied["changed"]],
        )

    def test_nested_boundary_is_reported_not_narrowed(self):
        """Nesting is coherent, so repair must report it rather than shrink it.

        `src/**` plus `src/charts/**` routes correctly: _find_owning_feature sorts by
        _boundary_specificity and takes the most specific match. Narrowing the parent
        to dodge the nesting is what orphaned 374 tracked files across five projects.
        """
        self._write([
            self._feature("feat_all", ["src/**"], ["src/core/a.py"]),
            self._feature("feat_part", ["src/charts/**"], ["src/charts/c.py"]),
        ])
        report = DevProjectManager(self.root).repair_feature_boundaries(dry_run=False)
        doc = json.loads(Path(resolve_devproject_path(self.root)).read_text())
        feat_all = next(f for f in doc["features"] if f["feature_id"] == "feat_all")

        self.assertEqual(feat_all["file_boundaries"], ["src/**"], "declaration must survive")
        self.assertEqual(report["changed"], [])
        self.assertTrue(any(u["feature_id"] == "feat_all" for u in report["unresolvable"]))

    def test_exact_duplicate_is_reassigned_to_the_better_evidenced_feature(self):
        """The one conflict shape with a correct answer: identical declarations.

        Narrowing cannot help, but ownership can be decided on evidence, and giving
        the region to one claimant preserves coverage exactly.
        """
        self._write([
            self._feature("feat_weak", ["src/shared/**"], ["src/shared/a.py"]),
            self._feature("feat_strong", ["src/shared/**"],
                          ["src/shared/a.py", "src/shared/b.py", "src/shared/c.py"]),
        ])
        report = DevProjectManager(self.root).repair_feature_boundaries(dry_run=False)
        doc = json.loads(Path(resolve_devproject_path(self.root)).read_text())
        owners = [f["feature_id"] for f in doc["features"]
                  if "src/shared/**" in (f.get("file_boundaries") or [])]

        self.assertEqual(owners, ["feat_strong"], "the better-evidenced feature keeps it")
        self.assertTrue(report["reassigned"])
        self.assertGreaterEqual(report["coverage_after"], report["coverage_before"])

    def test_non_conflicting_boundaries_are_left_alone(self):
        """Curated declarations must survive. Blanket re-derivation destroyed them."""
        path = self._write([
            self._feature("feat_a", ["src/a/**"], ["src/a/x.py"]),
            self._feature("feat_b", ["src/b/**"], ["src/b/y.py"]),
        ])
        before = path.read_bytes()
        report = DevProjectManager(self.root).repair_feature_boundaries(dry_run=False)
        self.assertEqual(report["changed"], [])
        self.assertEqual(path.read_bytes(), before)

    def test_declared_boundary_without_evidence_is_kept(self):
        """A feature can legitimately declare territory no session has touched yet."""
        self._write([
            self._feature("feat_planned", ["src/planned/**"], []),
            self._feature("feat_other", ["src/other/**"], ["src/other/a.py"]),
        ])
        DevProjectManager(self.root).repair_feature_boundaries(dry_run=False)
        doc = json.loads(Path(resolve_devproject_path(self.root)).read_text())
        planned = next(f for f in doc["features"] if f["feature_id"] == "feat_planned")
        self.assertEqual(planned["file_boundaries"], ["src/planned/**"])

    def test_conflict_with_no_evidence_is_reported_not_guessed(self):
        self._write([
            self._feature("feat_broad", ["src/**"], []),
            self._feature("feat_inner", ["src/inner/**"], ["src/inner/a.py"]),
        ])
        report = DevProjectManager(self.root).repair_feature_boundaries(dry_run=True)
        self.assertTrue(report["unresolvable"])
        self.assertEqual(report["unresolvable"][0]["feature_id"], "feat_broad")

    def test_cross_cutting_files_touched_is_not_treated_as_conflict(self):
        """Editing another feature's test file is evidence, not an ownership claim.

        Counting it as a conflict shattered tests/** into one entry per file.
        """
        path = self._write([
            self._feature("feat_tests", ["tests/**"], ["tests/a.py"]),
            self._feature("feat_core", ["src/**"], ["src/core.py", "tests/test_core.py"]),
        ])
        before = path.read_bytes()
        report = DevProjectManager(self.root).repair_feature_boundaries(dry_run=False)
        self.assertEqual(report["changed"], [])
        self.assertEqual(path.read_bytes(), before)

    def test_repair_never_reduces_file_coverage(self):
        """The invariant, stated directly. Everything else is a means to it.

        A boundary change that leaves a real file owned by nothing is worse than the
        overlap it removes: an ambiguously-owned file still routes somewhere, an
        unowned one routes nowhere. Repair is gated on this and refuses rather than
        trade ownership for a tidier overlap count.
        """
        (self.root / "src" / "core").mkdir(parents=True)
        (self.root / "src" / "other").mkdir(parents=True)
        (self.root / "src" / "charts").mkdir(parents=True)
        for rel in ("src/core/a.py", "src/other/untouched.py", "src/charts/c.py"):
            (self.root / rel).write_text("x = 1\n")

        self._write([
            self._feature("feat_all", ["src/**"], ["src/core/a.py"]),
            self._feature("feat_part", ["src/charts/**"], ["src/charts/c.py"]),
        ])
        report = DevProjectManager(self.root).repair_feature_boundaries(dry_run=False)

        if report.get("refused"):
            self.assertEqual(report["changed"], [])
        else:
            self.assertGreaterEqual(report["coverage_after"], report["coverage_before"])

        # src/other/untouched.py must still be owned by somebody either way.
        doc = json.loads(Path(resolve_devproject_path(self.root)).read_text())
        prefixes = [b[:-2].rstrip("/") if b.endswith("/**") else b
                    for f in doc["features"] for b in (f.get("file_boundaries") or [])]
        self.assertTrue(
            any("src/other/untouched.py".startswith(p + "/") or "src/other/untouched.py" == p
                for p in prefixes),
            "a file that was owned before must still be owned after",
        )

    def test_repair_is_idempotent(self):
        self._write([
            self._feature("feat_all", ["src/**"], ["src/core/a.py"]),
            self._feature("feat_part", ["src/charts/**"], ["src/charts/c.py"]),
        ])
        manager = DevProjectManager(self.root)
        manager.repair_feature_boundaries(dry_run=False)
        second = manager.repair_feature_boundaries(dry_run=False)
        self.assertEqual(second["changed"], [])


if __name__ == "__main__":
    unittest.main()
