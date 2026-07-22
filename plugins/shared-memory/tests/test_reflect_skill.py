#!/usr/bin/env python3
"""Skill-invariant checks for /memory-reflect — Layer 1 (static, no model calls).

Guards the load-bearing contracts: the skill reads the store doctrine, drives reflect-scan,
hands mining off to /memory-distill (the single belief-write path — reflect discovers,
distill promotes), and keeps the no-excerpts rule explicit.
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN = os.path.dirname(HERE)
SKILL = os.path.join(PLUGIN, "skills", "memory-reflect", "SKILL.md")
DOCTRINE_FILES = ["framework_memory_gate", "reference_store_memory_format",
                  "framework_write_for_the_cold_reader", "framework_a_guess_is_not_a_fact",
                  "framework_reflect_memories_back"]


class ReflectSkill(unittest.TestCase):
    def setUp(self):
        with open(SKILL, encoding="utf-8") as f:
            self.skill = f.read()

    def test_frontmatter_name(self):
        self.assertRegex(self.skill, r"(?m)^name:\s*memory-reflect\s*$")

    def test_reads_all_five_doctrine(self):
        for d in DOCTRINE_FILES:
            self.assertIn(d, self.skill, f"skill doesn't reference doctrine {d}")

    def test_drives_reflect_scan(self):
        self.assertIn("reflect-scan", self.skill)

    def test_hands_mining_to_distill(self):
        # reflect discovers; distill is the consumer that records queue_remove
        self.assertIn("/memory-distill", self.skill)
        self.assertIn("queue_remove", self.skill)

    def test_no_excerpts_rule_is_explicit(self):
        self.assertRegex(self.skill, r"[Nn]ever excerpt")

    def test_covers_both_jobs(self):
        self.assertIn("recall audit", self.skill.lower())
        self.assertIn("mining", self.skill.lower())


if __name__ == "__main__":
    unittest.main()
