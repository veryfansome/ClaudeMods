#!/usr/bin/env python3
"""Skill-invariant checks for /memory-distill — Layer 1 (static, no model calls).

Guards the reflect→distill handoff: distill is the consumer that turns a queued
`reflect-pointer` (passthrough) into a memory and records its queue-line offset in
queue_remove. A regression here silently orphans every mined pointer, so pin the load-
bearing pieces of that narrative.
"""
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.join(os.path.dirname(HERE), "skills", "memory-distill", "SKILL.md")


class DistillSkill(unittest.TestCase):
    def setUp(self):
        with open(SKILL, encoding="utf-8") as f:
            self.skill = f.read()

    def test_frontmatter_name(self):
        self.assertRegex(self.skill, r"(?m)^name:\s*memory-distill\s*$")

    def test_describes_passthrough_pointer_consumption(self):
        self.assertIn("passthrough", self.skill)
        self.assertIn("reflect-pointer:", self.skill)

    def test_queue_remove_is_offsets_not_paths(self):
        # the mislabel that M2 verification caught: queue_remove is integer offsets
        self.assertIn("queue_remove", self.skill)
        self.assertNotIn("consumed queue paths", self.skill)

    def test_distinguishes_the_two_offsets(self):
        # the queue-line offset (for queue_remove) vs the record-offset inside the pointer
        self.assertIn("record-offset", self.skill)
        self.assertIn("two distinct offsets", self.skill)


if __name__ == "__main__":
    unittest.main()
