"""Tests for subject-specific phase instructions."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import config
from app.pipeline import Pipeline
from app.server import Handler


class SubjectInstructionsConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.patcher = patch.object(config, "SUBJECT_INSTRUCTIONS_FILE", self.tmp_path / "subject_instructions.json")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_save_and_get_instructions(self):
        self.assertEqual(config.all_subject_instructions(), {})
        self.assertEqual(config.get_subject_instructions("CS"), {})

        # Save instructions for CS enricher
        config.save_subject_instructions("CS", {"enricher": "Add CVE references and MITRE ATT&CK mappings."})
        saved = config.get_subject_instructions("CS")
        self.assertEqual(saved, {"enricher": "Add CVE references and MITRE ATT&CK mappings."})
        self.assertEqual(config.get_subject_instructions("cs"), saved)  # case-insensitive

        # Save instructions for another subject
        config.save_subject_instructions("OS", {
            "extractor": "Capture all POSIX syscall names.",
            "formatter": "Use terminal-style code blocks.",
        })
        all_inst = config.all_subject_instructions()
        self.assertIn("CS", all_inst)
        self.assertIn("OS", all_inst)
        self.assertEqual(all_inst["OS"]["extractor"], "Capture all POSIX syscall names.")

        # Clearing instructions for a subject removes it from the file
        config.save_subject_instructions("CS", {"enricher": "   "})
        self.assertEqual(config.get_subject_instructions("CS"), {})
        self.assertNotIn("CS", config.all_subject_instructions())


class PipelineMessageInstructionsTests(unittest.TestCase):
    @patch.object(config, "TRANSCRIPTS_DIR", Path("d:/E/agent_warden/transcript files"))
    @patch.object(config, "WORKSPACE", Path("d:/E/agent_warden"))
    @patch.object(config, "OUTPUTS_DIR", Path("d:/E/agent_warden/outputs"))
    @patch.object(config, "TOOLKIT", Path("d:/E/agent_warden/make-transcript-notes-kit-3agent"))
    def test_pipeline_message_without_extra_instructions(self):
        p = Pipeline(
            subject="Cyber Security",
            abbr="CS",
            prefix="CS_Lecture_1",
            lecture_num="1",
            transcript="test.txt",
        )
        msg1 = p._agent_message(1)
        msg2 = p._agent_message(2)
        msg3 = p._agent_message(3)

        self.assertNotIn("Subject-Specific Extra Instructions", msg1)
        self.assertNotIn("Subject-Specific Extra Instructions", msg2)
        self.assertNotIn("Subject-Specific Extra Instructions", msg3)

    @patch.object(config, "TRANSCRIPTS_DIR", Path("d:/E/agent_warden/transcript files"))
    @patch.object(config, "WORKSPACE", Path("d:/E/agent_warden"))
    @patch.object(config, "OUTPUTS_DIR", Path("d:/E/agent_warden/outputs"))
    @patch.object(config, "TOOLKIT", Path("d:/E/agent_warden/make-transcript-notes-kit-3agent"))
    def test_pipeline_message_with_enricher_extra_instructions(self):
        p = Pipeline(
            subject="Cyber Security",
            abbr="CS",
            prefix="CS_Lecture_1",
            lecture_num="1",
            transcript="test.txt",
            extra_instructions={"enricher": "Always cross-reference CVE database."},
        )
        msg1 = p._agent_message(1)
        msg2 = p._agent_message(2)
        msg3 = p._agent_message(3)

        # Agent 1 (extractor) and Agent 3 (formatter) have no extra instructions
        self.assertNotIn("Subject-Specific Extra Instructions", msg1)
        self.assertNotIn("Subject-Specific Extra Instructions", msg3)

        # Agent 2 (enricher) contains the extra instructions
        self.assertIn("## Subject-Specific Extra Instructions (Cyber Security - Enricher Phase)", msg2)
        self.assertIn("Always cross-reference CVE database.", msg2)


class ServerInstructionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp.name)
        self.patcher = patch.object(config, "SUBJECT_INSTRUCTIONS_FILE", self.tmp_path / "subject_instructions.json")
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_plan_jobs_with_extra_instructions(self):
        handler = object.__new__(Handler)
        handler._json = MagicMock()

        body = {
            "abbr": "CS",
            "subject": "Cyber Security",
            "prefix": "CS_Lecture_1",
            "transcript": "dummy.txt",
            "phases": [2],
            "extra_instructions": {"enricher": "Focus on network security models."},
        }

        with patch("app.server._resolve_transcript", return_value=Path("d:/E/agent_warden/transcript files/dummy.txt")):
            with patch("app.server.config.is_backend_available", return_value=(True, "opencode")):
                with patch("app.server.config.list_models", return_value={"ok": True, "models": []}):
                    with patch("app.server.config.resolve_model_choice", return_value=("opencode-go/deepseek-v4-flash", "max")):
                        res = handler._plan_jobs(body, start=False)
                        self.assertIsNotNone(res)
                        self.assertTrue(res["ok"])
                        spec = res["jobs"][0]
                        self.assertEqual(spec["extra_instructions"], {"enricher": "Focus on network security models."})

    def test_plan_jobs_fallback_to_saved_instructions(self):
        config.save_subject_instructions("CS", {"enricher": "Saved CS instructions."})

        handler = object.__new__(Handler)
        handler._json = MagicMock()

        body = {
            "abbr": "CS",
            "subject": "Cyber Security",
            "prefix": "CS_Lecture_1",
            "transcript": "dummy.txt",
            "phases": [2],
            # extra_instructions omitted
        }

        with patch("app.server._resolve_transcript", return_value=Path("d:/E/agent_warden/transcript files/dummy.txt")):
            with patch("app.server.config.is_backend_available", return_value=(True, "opencode")):
                with patch("app.server.config.list_models", return_value={"ok": True, "models": []}):
                    with patch("app.server.config.resolve_model_choice", return_value=("opencode-go/deepseek-v4-flash", "max")):
                        res = handler._plan_jobs(body, start=False)
                        self.assertIsNotNone(res)
                        spec = res["jobs"][0]
                        self.assertEqual(spec["extra_instructions"], {"enricher": "Saved CS instructions."})

    def test_set_subject_instructions_api(self):
        handler = object.__new__(Handler)
        handler._body = MagicMock(return_value={
            "abbr": "CS",
            "instructions": {"enricher": "Add CVE references.", "extractor": " "},
        })
        handler._json = MagicMock()

        handler._set_subject_instructions()
        handler._json.assert_called_once()
        args, _ = handler._json.call_args
        res = args[0]
        self.assertTrue(res["ok"])
        self.assertEqual(res["abbr"], "CS")
        self.assertEqual(res["instructions"], {"enricher": "Add CVE references."})
        self.assertEqual(config.get_subject_instructions("CS"), {"enricher": "Add CVE references."})


if __name__ == "__main__":
    unittest.main()

