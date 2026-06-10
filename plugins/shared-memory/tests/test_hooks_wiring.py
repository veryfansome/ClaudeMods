#!/usr/bin/env python3
"""G1: validate the SHIPPED hooks/hooks.json wiring — every command entry resolves to an on-disk
script, and every pipeline script is wired. The doctor only reads the *installed* build's content;
this pins the shipped file so a future edit that drops the capture / eval / log-recall / load-recall
/ reflect-end entry fails a Layer-1 test instead of silently taking the pipeline dark.
"""
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
HOOKS = os.path.join(PLUGIN, "hooks", "hooks.json")
PREFIX = "${CLAUDE_PLUGIN_ROOT}/"


class HooksWiring(unittest.TestCase):
    def commands(self):
        hj = json.load(open(HOOKS))
        return [h["command"] for groups in hj["hooks"].values() for grp in groups for h in grp["hooks"]]

    def test_all_command_entries_resolve_to_scripts(self):
        cmds = self.commands()
        self.assertEqual(len(cmds), 7, f"expected 7 command entries (2 capture + 2 eval + log-recall "
                                       f"+ load-recall + reflect-end), got {len(cmds)}: {cmds}")
        for c in cmds:
            self.assertTrue(c.startswith(PREFIX), f"command not ${{CLAUDE_PLUGIN_ROOT}}-anchored: {c}")
            rel = c[len(PREFIX):]
            self.assertTrue(os.path.isfile(os.path.join(PLUGIN, rel)), f"hook script missing on disk: {rel}")

    def test_every_pipeline_script_is_wired(self):
        joined = " ".join(self.commands())
        for script in ("capture-candidate", "eval-memory", "log-recall", "load-recall", "reflect-end"):
            self.assertIn(script, joined, f"{script} not wired in hooks.json")

    def test_scripts_wired_under_correct_events_and_matchers(self):
        # G1: pin WHICH event each script is wired under + the PostToolUse matchers. A build that
        # wires all 5 scripts but under the wrong events (load-recall → SessionEnd, or capture's
        # matcher flipped to Read) reproduces the silent-off failure class while count/all-wired
        # stay green — so those checks alone don't pin the wiring.
        hj = json.load(open(HOOKS))

        def under(event):
            return " ".join(h["command"] for grp in hj["hooks"].get(event, []) for h in grp["hooks"])
        self.assertIn("load-recall", under("SessionStart"))
        self.assertIn("reflect-end", under("SessionEnd"))
        ptu = under("PostToolUse")
        for s in ("capture-candidate", "eval-memory", "log-recall"):
            self.assertIn(s, ptu, f"{s} not wired under PostToolUse")
        matcher = {}
        for grp in hj["hooks"]["PostToolUse"]:
            for h in grp["hooks"]:
                matcher[h["command"].rsplit("/", 1)[-1]] = grp.get("matcher", "")
        self.assertEqual(matcher["capture-candidate"], "Write|Edit")
        self.assertEqual(matcher["eval-memory"], "Write|Edit")
        self.assertEqual(matcher["log-recall"], "Read")

    def test_if_prefix_matches_the_scripts_own_recheck(self):
        # PRESENCE check, not equivalence (the if: globs and the scripts' own path regexes are not
        # provably equal — R2-review note): assert each PostToolUse entry's if: is anchored under the
        # path the script re-checks — capture under project-memory, eval/log under the store.
        hj = json.load(open(HOOKS))
        for grp in hj["hooks"]["PostToolUse"]:
            for h in grp["hooks"]:
                cmd, cond = h["command"], h.get("if", "")
                if "capture-candidate" in cmd:
                    self.assertIn(".claude/projects/", cond, cond)
                    self.assertIn("/memory/", cond, cond)
                elif "eval-memory" in cmd or "log-recall" in cmd:
                    self.assertIn(".claudemods/shared-memory", cond, cond)


if __name__ == "__main__":
    unittest.main()
