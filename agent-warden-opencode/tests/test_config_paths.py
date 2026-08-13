"""save_subject / delete_past_run confinement."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.paths import confine


class SubjectPathTests(unittest.TestCase):
    def test_save_subject_rejects_drive_name(self):
        with self.assertRaises(ValueError):
            config.save_subject("ZZZTEST", r"C:\Windows")

    def test_save_subject_rejects_parent(self):
        with self.assertRaises(ValueError):
            config.save_subject("ZZZTEST", "..")

    def test_delete_rejects_escape(self):
        with self.assertRaises(ValueError):
            config.delete_past_run("..", "x")
        with self.assertRaises(ValueError):
            config.delete_past_run("ACI", r"C:\Windows")
        with self.assertRaises(ValueError):
            config.delete_past_run("ACI", "../..")

    def test_save_subject_confined_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            docs = root / "companion_docs"
            topics = root / "topic_mappings"
            docs.mkdir()
            topics.mkdir()
            subjects_file = root / "subjects.json"
            with patch.object(config, "DOCS_DIR", docs), \
                 patch.object(config, "TOPIC_MAPPINGS_DIR", topics), \
                 patch.object(config, "SUBJECTS_FILE", subjects_file):
                config.save_subject("ZZQ", "Zed Queue")
                self.assertTrue((docs / "ZZQ").is_dir())
                yaml_path = confine(topics, "Zed Queue.yaml")
                self.assertIsNotNone(yaml_path)
                self.assertTrue(yaml_path.is_file())
                data = json.loads(subjects_file.read_text(encoding="utf-8"))
                self.assertEqual(data["ZZQ"], "Zed Queue")


if __name__ == "__main__":
    unittest.main()
