import json
import pathlib
import unittest

from _toy import (make_markers_project, make_registry_project, make_worktree, drop_worktree,
                  set_factor, load_cfg, cleanup, git, BASELINE_IMPL)
from _evolve import surface


class TestMarkers(unittest.TestCase):
    def setUp(self):
        self.root = make_markers_project()
        self.cfg = load_cfg(self.root)
        self.wt = make_worktree(self.root)

    def tearDown(self):
        drop_worktree(self.root, self.wt)
        cleanup(self.root)

    def test_parse_and_split(self):
        text = (self.root / "src" / "algo.py").read_text()
        self.assertEqual(len(surface.parse_blocks(text)), 1)
        frozen, mutable = surface.split_regions(text)
        self.assertIn("EVOLVE-BLOCK-START", frozen[0])   # marker lines are frozen
        self.assertIn("return 1.0", mutable[0])

    def test_parse_errors(self):
        for bad in ("x = 1\n", "# EVOLVE-BLOCK-START\n",
                    "# EVOLVE-BLOCK-END\n# EVOLVE-BLOCK-START\n",
                    "# EVOLVE-BLOCK-START\n# EVOLVE-BLOCK-START\n# EVOLVE-BLOCK-END\n"):
            with self.assertRaises(surface.SurfaceError):
                surface.parse_blocks(bad)

    def test_guard_accepts_inside_edit(self):
        set_factor(self.wt, 2.0)
        report = surface.guard_markers(self.wt, self.cfg)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["changed"], ["src/algo.py"])

    def test_guard_rejects_outside_edit(self):
        p = self.wt / "src" / "algo.py"
        p.write_text(p.read_text() + "\ndef helper():\n    return 2\n")
        report = surface.guard_markers(self.wt, self.cfg)
        self.assertFalse(report["ok"])
        self.assertIn("outside EVOLVE blocks", report["violations"][0])

    def test_guard_rejects_deleted_surface_file(self):
        (self.wt / "src" / "algo.py").unlink()
        report = surface.guard_markers(self.wt, self.cfg)   # must be a violation, not a crash
        self.assertFalse(report["ok"])
        self.assertTrue(any("deleted" in v for v in report["violations"]))

    def test_guard_rejects_marker_deletion(self):
        p = self.wt / "src" / "algo.py"
        p.write_text("\n".join(l for l in p.read_text().splitlines()
                               if "EVOLVE-BLOCK-END" not in l) + "\n")
        report = surface.guard_markers(self.wt, self.cfg)
        self.assertFalse(report["ok"])

    def test_guard_rejects_protected_and_foreign_files(self):
        (self.wt / "eval.py").write_text("print('gamed')\n")
        set_factor(self.wt, 2.0)
        report = surface.guard_markers(self.wt, self.cfg)
        self.assertFalse(report["ok"])
        self.assertIn("protected path", report["violations"][0])

    def test_guard_rejects_empty_and_warns_untracked(self):
        report = surface.guard_markers(self.wt, self.cfg)
        self.assertFalse(report["ok"])                            # empty candidate
        (self.wt / "helper.py").write_text("x = 1\n")
        set_factor(self.wt, 3.0)
        report = surface.guard_markers(self.wt, self.cfg)
        self.assertTrue(report["ok"])
        self.assertIn("untracked", report["warnings"][0])

    def test_extract_patch_and_mutable(self):
        set_factor(self.wt, 2.5)
        patch = surface.extract_patch(self.wt)
        self.assertIn("return 2.5", patch)
        self.assertIn("return 2.5", surface.candidate_mutable_text(self.wt, self.cfg))


class TestRegistry(unittest.TestCase):
    def setUp(self):
        self.root = make_registry_project()
        self.cfg = load_cfg(self.root)

    def tearDown(self):
        cleanup(self.root)

    def test_impls_and_baseline(self):
        self.assertEqual(surface.list_impls(self.root, self.cfg, "objective"), ["baseline"])
        g = surface.baseline_genome(self.cfg)
        self.assertTrue(surface.validate_genome(g, self.root, self.cfg))
        self.assertEqual(surface.genome_recipe(g), "objective=baseline")

    def test_validate_genome_errors(self):
        bad = [
            {"chunks": {}},
            {"chunks": {"objective": {"impl": "nope"}}},
            {"chunks": {"mystery": {"impl": "baseline"}}},
            {"chunks": {"objective": {"impl": "baseline", "params": 3}}},
        ]
        for g in bad:
            with self.assertRaises(surface.SurfaceError):
                surface.validate_genome(g, self.root, self.cfg)

    def test_guard_rejects_modified_impl(self):
        p = self.root / "evolve" / "chunks" / "objective" / "baseline.py"
        p.write_text(BASELINE_IMPL.replace("1.0", "9.0"))
        report = surface.guard_registry(surface.baseline_genome(self.cfg), self.root, self.cfg)
        self.assertFalse(report["ok"])
        self.assertIn("append-only", report["violations"][0])
        git(["checkout", "--", str(p)], self.root)

    def test_new_impl_is_fine_and_collected(self):
        p = self.root / "evolve" / "chunks" / "objective" / "better.py"
        p.write_text(BASELINE_IMPL.replace("1.0", "2.0"))
        g = {"chunks": {"objective": {"impl": "better", "params": {}}}}
        report = surface.guard_registry(g, self.root, self.cfg)
        self.assertTrue(report["ok"], report)
        files = surface.genome_impl_files(g, self.root, self.cfg)
        self.assertEqual(files, ["evolve/chunks/objective/better.py"])


if __name__ == "__main__":
    unittest.main()
