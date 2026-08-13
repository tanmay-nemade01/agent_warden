"""Bounce / adaptation smoke tests."""
from __future__ import annotations

import unittest

from app.pipeline import ADAPTATION_BY_AGENT, _BOUNCE_RE


class AdaptationTests(unittest.TestCase):
    def test_no_full_shell_claim(self):
        blob = "\n".join(ADAPTATION_BY_AGENT.values())
        self.assertNotIn("full shell access", blob)
        self.assertNotIn("read any file", blob)
        self.assertIn("question tool", blob)

    def test_agent_scopes(self):
        self.assertIn("Transcript", ADAPTATION_BY_AGENT[1])
        self.assertIn("update_topic_mapping.py", ADAPTATION_BY_AGENT[2])
        self.assertIn("do not write YAML", ADAPTATION_BY_AGENT[3])

    def test_bounce_patterns(self):
        self.assertTrue(_BOUNCE_RE.search("leftover *[verify]* marker"))
        self.assertTrue(_BOUNCE_RE.search("return the affected section to Agent 2"))
        self.assertTrue(_BOUNCE_RE.search("TODO: fill this"))
        self.assertFalse(_BOUNCE_RE.search("all gates passed"))


if __name__ == "__main__":
    unittest.main()
