#!/usr/bin/env python3
"""Skill-invariant checks for /memory-review — Layer 1 (static, no model calls).

The tombstone grammar is a shared contract: the skill WRITES it, review-scan DETECTS
it, and the format doctrine DOCUMENTS it — the three must agree, or quarantine silently
breaks (a line the skill writes that review-scan can't recognize would still steer).
Also checks the skill reads the doctrine and drives the scripts.
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
SKILL = os.path.join(PLUGIN, "skills", "memory-review", "SKILL.md")
SCAN = os.path.join(PLUGIN, "bin", "review-scan")
DOCTRINE = os.path.join(PLUGIN, "templates", "reference_store_memory_format.md")
TOMBSTONE = "contradicted — pending review"
DOCTRINE_FILES = ["framework_memory_gate", "reference_store_memory_format",
                  "framework_write_for_the_cold_reader", "framework_a_guess_is_not_a_fact",
                  "framework_reflect_memories_back"]


class ReviewSkill(unittest.TestCase):
    def setUp(self):
        self.skill = open(SKILL, encoding="utf-8").read()
        self.scan = open(SCAN, encoding="utf-8").read()
        self.doctrine = open(DOCTRINE, encoding="utf-8").read()

    def test_frontmatter_name(self):
        self.assertRegex(self.skill, r"(?m)^name:\s*memory-review\s*$")

    def test_reads_all_five_doctrine(self):
        for d in DOCTRINE_FILES:
            self.assertIn(d, self.skill, f"skill doesn't reference doctrine {d}")

    def test_drives_the_scripts(self):
        self.assertIn("review-scan", self.skill)
        self.assertIn("review-apply", self.skill)

    def test_tombstone_grammar_is_a_shared_contract(self):
        self.assertIn(TOMBSTONE, self.skill, "skill missing the tombstone marker")
        self.assertIn(f'TOMBSTONE_TITLE = "{TOMBSTONE}"', self.scan, "review-scan's marker drifted from the skill")
        self.assertIn(TOMBSTONE, self.doctrine, "format doctrine doesn't document the tombstone marker")

    def test_never_writes_beliefs_stated(self):
        # the load-bearing routing rule must be explicit in the skill
        self.assertIn("file tools", self.skill)
        self.assertRegex(self.skill, r"never writes the framework|beliefs.*file tools|file tools.*belief")


if __name__ == "__main__":
    unittest.main()
