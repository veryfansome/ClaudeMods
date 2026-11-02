"""A project rooted in a subdirectory of its repo (--root <repo>/sub), and two searches in one repo.

git reports some paths relative to the repo's top level (diff) and others relative to the current
directory (ls-files); the surface, registry and protected globs are relative to the project root.
These tests pin that every path the engine compares is root-relative, so a subdirectory project
guards its own files, and doesn't mistake another search's files for its own.
"""

import json
import pathlib
import shutil
import subprocess
import tempfile
import unittest

from _toy import (ALGO, BASELINE_IMPL, MARKERS_EVAL, PLUGIN, REGISTRY_EVAL, git, markers_config,
                  registry_config, set_factor)
from _evolve import runner, score, surface

HOOK = PLUGIN / "scripts" / "protect-paths"
GITIGNORE = ".cache/\narchive/private/\n*.json.bak.*\n*.json.tmp\n"


def _registry_at(root):
    (root / "eval.py").write_text(REGISTRY_EVAL)
    impl_dir = root / "evolve" / "chunks" / "objective"
    impl_dir.mkdir(parents=True)
    (impl_dir / "baseline.py").write_text(BASELINE_IMPL)
    (root / "evolve" / "evolve.json").write_text(json.dumps(registry_config(), indent=2))
    (root / "evolve" / ".gitignore").write_text(GITIGNORE)


def _markers_at(root):
    (root / "src").mkdir(parents=True)
    (root / "src" / "algo.py").write_text(ALGO)
    (root / "eval.py").write_text(MARKERS_EVAL)
    (root / "evolve").mkdir()
    (root / "evolve" / "evolve.json").write_text(json.dumps(markers_config(), indent=2))
    (root / "evolve" / ".gitignore").write_text(GITIGNORE)


def make_nested():
    """A repo holding a registry search at its top level, another in sub/, and a markers one in mk/."""
    repo = pathlib.Path(tempfile.mkdtemp(prefix="evolve-toy-nested-"))
    git(["init", "-q"], repo)
    git(["config", "user.email", "test@example.invalid"], repo)
    git(["config", "user.name", "evolve-tests"], repo)
    _registry_at(repo)
    (repo / "sub").mkdir()
    _registry_at(repo / "sub")
    (repo / "mk").mkdir()
    _markers_at(repo / "mk")
    git(["add", "-A"], repo)
    git(["commit", "-q", "-m", "setup"], repo)
    return repo


def cfg_of(root):
    return json.loads((root / "evolve" / "evolve.json").read_text())


def baseline_genome(root):
    return surface.baseline_genome(cfg_of(root))


class TestSubdirRoot(unittest.TestCase):
    def setUp(self):
        self.repo = make_nested()
        self.sub = self.repo / "sub"

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def guard(self, root):
        return surface.guard_registry(baseline_genome(root), root, cfg_of(root))

    def test_a_subdirectory_search_sees_its_own_impl_edited(self):
        with open(self.sub / "evolve" / "chunks" / "objective" / "baseline.py", "a") as f:
            f.write("# edited\n")
        report = self.guard(self.sub)
        self.assertFalse(report["ok"], report)
        self.assertTrue(any("evolve/chunks/objective/baseline.py" in v for v in report["violations"]))
        self.assertEqual(surface.changed_files(self.sub), ["evolve/chunks/objective/baseline.py"])

    def test_a_subdirectory_search_guards_its_protected_paths(self):
        with open(self.sub / "evolve" / "evolve.json") as f:
            protected = json.load(f)["protected"]
        self.assertIn("eval.py", protected)
        with open(self.sub / "eval.py", "a") as f:
            f.write("# edited\n")
        report = self.guard(self.sub)
        self.assertFalse(report["ok"], report)
        self.assertTrue(any("protected path modified: eval.py" in v for v in report["violations"]))

    def test_another_searchs_files_are_not_this_ones(self):
        # The top-level search's impl, edited: the sub/ search must neither flag nor warn about it ...
        with open(self.repo / "evolve" / "chunks" / "objective" / "baseline.py", "a") as f:
            f.write("# edited\n")
        report = self.guard(self.sub)
        self.assertTrue(report["ok"], report)
        self.assertEqual(report["warnings"], [])
        # ... while the top-level search still catches it.
        self.assertFalse(self.guard(self.repo)["ok"])

    def test_a_markers_patch_from_a_subdirectory_is_root_relative_and_scores(self):
        mk = self.repo / "mk"
        cfg = cfg_of(mk)
        with runner.git_worktree(mk) as wt:
            self.assertEqual(wt.name, "mk")  # the project's copy inside the whole-repo worktree
            set_factor(wt, 3.0)
            patch = surface.extract_patch(wt)
        self.assertIn("a/src/algo.py", patch)
        self.assertNotIn("mk/", patch)
        rec = score.score_candidate(mk, cfg, meta={"id": "p1", "parent": None, "generation": 1,
                                                   "inventor": "test", "operator": "diff", "rationale": "x"},
                                    patch=patch, no_archive=True)
        self.assertEqual(rec["fitness"], 3.0, rec)


class TestHookInSubdirProject(unittest.TestCase):
    def setUp(self):
        self.repo = make_nested()
        self.sub = self.repo / "sub"
        for root in (self.repo, self.sub):  # arm both: a campaign is live in each
            (root / "evolve" / "archive").mkdir(parents=True, exist_ok=True)
            (root / "evolve" / "archive" / "genomes.jsonl").write_text("")

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def hook(self, file_path, cwd):
        payload = json.dumps({"tool_name": "Edit", "cwd": str(cwd), "tool_input": {"file_path": str(file_path)}})
        return subprocess.run([str(HOOK)], input=payload, text=True, capture_output=True)

    def test_a_session_at_the_repo_root_is_guarded_in_a_subdirectory_project(self):
        r = self.hook(self.sub / "eval.py", cwd=self.repo)
        self.assertEqual(r.returncode, 2, r.stderr)
        self.assertIn("'eval.py' is a protected path", r.stderr)
        r = self.hook("sub/evolve/evolve.json", cwd=self.repo)  # a path relative to the session
        self.assertEqual(r.returncode, 2, r.stderr)

    def test_new_impls_and_files_outside_any_project_stay_allowed(self):
        self.assertEqual(self.hook(self.sub / "evolve" / "chunks" / "objective" / "new.py", cwd=self.repo).returncode, 0)
        outside = pathlib.Path(tempfile.mkdtemp(prefix="evolve-outside-"))
        try:
            self.assertEqual(self.hook(outside / "notes.md", cwd=self.repo).returncode, 0)
        finally:
            shutil.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
