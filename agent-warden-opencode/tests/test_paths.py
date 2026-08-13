"""Stdlib tests for path confinement and permission JSON shape."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.paths import confine, is_safe_component, resolve_under
from app import permissions


class ConfineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = (self.tmp / "outputs").resolve()
        self.root.mkdir()

    def test_happy_join(self):
        p = confine(self.root, "Artificial Computational Intelligence", "ACI_Lecture_1")
        self.assertIsNotNone(p)
        self.assertEqual(p.parent, self.root / "Artificial Computational Intelligence")

    def test_reject_parent(self):
        self.assertIsNone(confine(self.root, ".."))
        self.assertIsNone(confine(self.root, "foo", ".."))

    def test_reject_separator(self):
        self.assertIsNone(confine(self.root, r"..\.."))
        self.assertIsNone(confine(self.root, "a/b"))
        self.assertIsNone(confine(self.root, r"a\b"))

    def test_reject_drive_and_unc(self):
        self.assertIsNone(confine(self.root, r"C:\Windows"))
        self.assertIsNone(confine(self.root, "C:Windows"))
        self.assertFalse(is_safe_component(r"C:\Windows"))
        self.assertFalse(is_safe_component(r"\\server\share"))

    def test_reject_posix_absolute(self):
        self.assertIsNone(confine(self.root, "/etc/passwd"))
        self.assertFalse(is_safe_component("/etc"))

    def test_reject_empty(self):
        self.assertIsNone(confine(self.root, ""))
        self.assertIsNone(confine(self.root, "  "))
        self.assertIsNone(confine(self.root))

    def test_resolve_under_keeps_inside(self):
        f = self.root / "notes.txt"
        f.write_text("x", encoding="utf-8")
        self.assertEqual(resolve_under(self.root, f), f.resolve())
        outside = self.tmp / "other.txt"
        outside.write_text("y", encoding="utf-8")
        self.assertIsNone(resolve_under(self.root, outside))
        self.assertIsNone(resolve_under(self.root, self.root / ".." / "other.txt"))


class PermissionShapeTests(unittest.TestCase):
    def test_opencode_denies_question_and_toolkit_edit(self):
        block = permissions.opencode_permission_block()
        self.assertEqual(block["question"], "deny")
        self.assertEqual(block["edit"][f"{permissions.TOOLKIT_REL}/**"], "deny")
        self.assertEqual(block["edit"]["outputs/**"], "allow")
        self.assertEqual(block["edit"]["topic_mappings/**"], "allow")
        self.assertEqual(block["edit"]["*.env"], "deny")

    def test_commandcode_deny_list(self):
        perms = permissions.commandcode_permissions()
        joined = " ".join(perms["deny"])
        self.assertIn(permissions.TOOLKIT_REL, joined)
        self.assertTrue(any("outputs/**" in a for a in perms["allow"]))

    def test_write_job_configs(self):
        with tempfile.TemporaryDirectory() as td:
            paths = permissions.write_job_configs(Path(td))
            self.assertTrue(paths["opencode"].is_file())
            data = json.loads(paths["opencode"].read_text(encoding="utf-8"))
            self.assertEqual(data["permission"]["question"], "deny")
            env = permissions.opencode_env(paths["opencode"])
            self.assertEqual(env["OPENCODE_CONFIG"], str(paths["opencode"]))
            from app import config as app_config
            self.assertEqual(
                env["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"],
                str(app_config.OPENCODE_OUTPUT_TOKEN_MAX))
            self.assertGreaterEqual(app_config.OPENCODE_OUTPUT_TOKEN_MAX, 384000)


if __name__ == "__main__":
    unittest.main()
