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


class SummarizeRunTests(unittest.TestCase):
    def test_current_phase_and_tokens(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "NLP" / "NLP_Lecture_1"
            out.mkdir(parents=True)
            path = out / "NLP_Lecture_1_run_events.jsonl"
            events = [
                {"type": "pipeline_start", "subject": "NLP",
                 "prefix": "NLP_Lecture_1", "phases": [1, 2, 3],
                 "model": "m", "time": 1},
                {"type": "phase_start", "phase": "extractor"},
                {"type": "agent_event", "phase": "extractor",
                 "event": {"type": "step_finish", "part": {
                     "tokens": {"input": 100, "output": 20, "reasoning": 5},
                     "cost": 0.01}}},
                {"type": "phase_start", "phase": "enricher"},
            ]
            path.write_text("\n".join(json.dumps(e) for e in events) + "\n",
                            encoding="utf-8")
            with patch.object(config, "OUTPUTS_DIR", root):
                summary = config.summarize_run_events(
                    path, "NLP", "NLP_Lecture_1")
            self.assertEqual(summary["status"], "running")
            self.assertEqual(summary["current_phase"], "enricher")
            self.assertEqual(summary["tokens"]["input"], 100)
            self.assertGreater(summary["cost"], 0)
            self.assertIn("phase_retries", summary)

    def test_phase_retries_parsed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "CV" / "CV_Lecture_2"
            out.mkdir(parents=True)
            path = out / "CV_Lecture_2_run_events.jsonl"
            events = [
                {"type": "pipeline_start", "subject": "CV",
                 "prefix": "CV_Lecture_2", "phases": [1, 2, 3], "time": 1},
                {"type": "phase_start", "phase": "extractor"},
                {"type": "retry_start", "failed_phase": "extractor", "retry": 1, "max_retries": 2},
                {"type": "phase_end", "phase": "extractor", "ok": True, "seconds": 15},
                {"type": "phase_start", "phase": "enricher"},
                {"type": "bounce_to_enricher", "from_phase": "enricher", "retry": 1, "max_retries": 2},
                {"type": "pipeline_end", "status": "done", "stats": {
                    "phase_retries": {"extractor": 1, "enricher": 1, "formatter": 0}
                }},
            ]
            path.write_text("\n".join(json.dumps(e) for e in events) + "\n",
                            encoding="utf-8")
            with patch.object(config, "OUTPUTS_DIR", root):
                summary = config.summarize_run_events(
                    path, "CV", "CV_Lecture_2")
            self.assertEqual(summary["phase_retries"]["extractor"], 1)
            self.assertEqual(summary["phase_retries"]["enricher"], 1)
            self.assertEqual(summary["phase_retries"]["formatter"], 0)
            self.assertEqual(summary["phase_stats"]["extractor"]["retries"], 1)


if __name__ == "__main__":
    unittest.main()
