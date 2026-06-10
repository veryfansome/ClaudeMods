#!/usr/bin/env python3
"""Tests for the M4 repo→domain map — memory-init --map-repo / --list-domains, the seeded
.domains.json, and _apply_common.repo_root() keying (Layer 1, no model calls).

Covers: install seeds an empty map; --list-domains reports the tag universe; --map-repo writes
the repo-root key + validated domains; validation rejects a typo (unknown domain), the reserved
'local', and a malformed name; a subdir and a worktree map to the SAME key; repo_root's bare-repo
and git-unavailable (walk-up) fallbacks (the Unit-2 review fixes); and .domains.json is gitignored.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from importlib.machinery import SourceFileLoader

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
INIT = os.path.join(PLUGIN, "bin", "memory-init")
AC = SourceFileLoader("ac_probe", os.path.join(PLUGIN, "bin", "_apply_common.py")).load_module()


class Base(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="dm-test-")
        self.store = os.path.join(self.home, ".claudemods", "shared-memory")

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def run_init(self, *args, cwd=None, env_extra=None):
        env = dict(os.environ, HOME=self.home)
        if env_extra:
            env.update(env_extra)
        return subprocess.run([sys.executable, INIT, *args], env=env, cwd=cwd,
                              capture_output=True, text=True)

    def w(self, rel, text):
        p = os.path.join(self.store, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def mem(self, rel, name, domains, local=False):
        self.w(("local/" if local else "") + rel,
               f"---\nname: {name}\ntitle: T\ndescription: d\n"
               f"metadata:\n  type: reference\n  domains: {domains}\n  basis: observed\n---\nb\n")

    def read_map(self):
        p = os.path.join(self.store, ".domains.json")
        if not os.path.exists(p):
            return None
        with open(p) as f:
            return json.load(f)

    def git_repo(self, name):
        r = os.path.join(self.home, "work", name)
        os.makedirs(r)
        subprocess.run(["git", "-C", r, "init", "-q"])
        return r


class MapCommands(Base):
    def test_install_seeds_empty_map(self):
        self.run_init()
        self.assertEqual(self.read_map(), {"version": 1, "repos": {}})

    def test_list_domains_reports_universe(self):
        self.run_init()
        self.mem("reference_a.md", "reference-a", "[yahoo, security, gcp]")
        self.mem("reference_b.md", "reference-b", "[gcp]", local=True)
        self.mem("reference_c.md", "reference-c", "[]")
        out = self.run_init("--list-domains").stdout.split()
        self.assertEqual(sorted(out), ["gcp", "security", "yahoo"])

    def test_map_repo_writes_key_and_domains(self):
        self.run_init()
        self.mem("reference_a.md", "reference-a", "[gcp, security]")
        repo = self.git_repo("r")
        rc = self.run_init("--map-repo", "gcp", "security", cwd=repo)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        m = self.read_map()
        self.assertEqual(m["repos"][AC.repo_root(repo)], ["gcp", "security"])

    def test_map_repo_add_unions_and_map_repo_replaces(self):
        # Unit-4 review m2: --map-repo REPLACES (clobbers others); --map-repo-add UNIONS
        self.run_init()
        self.mem("reference_a.md", "reference-a", "[gcp, security, aws]")
        repo = self.git_repo("r")
        key = AC.repo_root(repo)
        self.run_init("--map-repo", "gcp", "security", cwd=repo)
        # add one domain: existing gcp+security must survive
        rc = self.run_init("--map-repo-add", "aws", cwd=repo)
        self.assertEqual(rc.returncode, 0, rc.stderr)
        self.assertEqual(self.read_map()["repos"][key], ["aws", "gcp", "security"])
        # bare --map-repo still replaces the whole set (the deliberate reset)
        self.run_init("--map-repo", "aws", cwd=repo)
        self.assertEqual(self.read_map()["repos"][key], ["aws"])
        # --map-repo-add validates too (a typo is refused, nothing changed)
        rc = self.run_init("--map-repo-add", "gcpp", cwd=repo)
        self.assertNotEqual(rc.returncode, 0)
        self.assertEqual(self.read_map()["repos"][key], ["aws"])

    def test_map_repo_rejects_typo(self):
        self.run_init()
        self.mem("reference_a.md", "reference-a", "[gcp]")
        repo = self.git_repo("r")
        rc = self.run_init("--map-repo", "gcpp", cwd=repo)
        self.assertNotEqual(rc.returncode, 0)
        self.assertIn("no memory carries this domain tag", rc.stderr)
        self.assertEqual(self.read_map()["repos"], {}, "map written despite a rejected typo")

    def test_map_repo_rejects_reserved_and_malformed(self):
        self.run_init()
        self.mem("reference_a.md", "reference-a", "[gcp]")
        repo = self.git_repo("r")
        self.assertNotEqual(self.run_init("--map-repo", "local", cwd=repo).returncode, 0)
        self.assertNotEqual(self.run_init("--map-repo", "code review", cwd=repo).returncode, 0)

    def test_map_repo_rejects_unicode_domain(self):
        # the regex must be ASCII [A-Za-z0-9_-], not Unicode \w (Unit-3 review)
        self.run_init()
        self.mem("reference_a.md", "reference-a", "[café]")  # a Unicode-tagged memory
        repo = self.git_repo("r")
        rc = self.run_init("--map-repo", "café", cwd=repo)
        self.assertNotEqual(rc.returncode, 0, "Unicode domain accepted")
        self.assertEqual(self.read_map()["repos"], {})

    def test_corrupt_repos_non_dict_degrades_cleanly(self):
        # .domains.json with a non-dict `repos` must not traceback (Unit-3 review)
        self.run_init()
        self.mem("reference_a.md", "reference-a", "[gcp]")
        self.w(".domains.json", json.dumps({"version": 1, "repos": ["oops-a-list"]}))
        repo = self.git_repo("r")
        rc = self.run_init("--map-repo", "gcp", cwd=repo)
        self.assertNotIn("Traceback", rc.stderr, "raw traceback on corrupt repos")
        self.assertEqual(rc.returncode, 0, rc.stderr)   # coerces to {} then maps cleanly
        self.assertEqual(self.read_map()["repos"][AC.repo_root(repo)], ["gcp"])

    def test_subdir_and_worktree_share_key(self):
        self.run_init()
        self.mem("reference_a.md", "reference-a", "[gcp]")
        repo = self.git_repo("r")
        subprocess.run(["git", "-C", repo, "-c", "user.email=x@x", "-c", "user.name=x",
                        "commit", "-q", "--allow-empty", "-m", "init"])
        sub = os.path.join(repo, "a", "b"); os.makedirs(sub)
        self.run_init("--map-repo", "gcp", cwd=repo)
        self.run_init("--map-repo", "gcp", cwd=sub)   # subdir → same key, one entry
        self.assertEqual(len(self.read_map()["repos"]), 1, "subdir created a second key")
        wt = os.path.join(self.home, "work", "r-wt")
        subprocess.run(["git", "-C", repo, "worktree", "add", "-q", wt])
        self.assertEqual(AC.repo_root(wt), AC.repo_root(repo), "worktree keyed differently from main")


class RepoRoot(Base):
    def test_bare_repos_distinct_keys(self):
        bares = os.path.join(self.home, "bares"); os.makedirs(bares)
        one = os.path.join(bares, "one.git"); two = os.path.join(bares, "two.git")
        subprocess.run(["git", "init", "--bare", "-q", one])
        subprocess.run(["git", "init", "--bare", "-q", two])
        self.assertNotEqual(AC.repo_root(one), AC.repo_root(two), "sibling bare repos collided")

    def test_git_unavailable_walks_up_to_repo_root(self):
        repo = self.git_repo("r")
        sub = os.path.join(repo, "x", "y"); os.makedirs(sub)
        # strip git from PATH so repo_root takes the no-git fallback; it must still find the
        # repo root by walking up for .git (not key to the subdir)
        code = ("import sys; sys.path.insert(0, %r); import _apply_common as ac; "
                "print(ac.repo_root(%r))" % (os.path.join(PLUGIN, "bin"), sub))
        r = subprocess.run([sys.executable, "-c", code],
                           env=dict(os.environ, PATH="/nonexistent-bin"), capture_output=True, text=True)
        self.assertEqual(r.stdout.strip(), os.path.realpath(repo), r.stderr)

    def test_non_git_dir_keys_to_realpath(self):
        d = os.path.join(self.home, "plain"); os.makedirs(d)
        self.assertEqual(AC.repo_root(d), os.path.realpath(d))


class Confinement(Base):
    def test_domains_map_is_gitignored(self):
        self.run_init()
        gi = os.path.join(self.store, ".gitignore")
        with open(gi) as f:
            lines = f.read().splitlines()
        self.assertIn(".domains.json", lines)
        self.assertIn("MEMORY.local.*.md", lines)


if __name__ == "__main__":
    unittest.main()
