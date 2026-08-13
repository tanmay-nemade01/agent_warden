"""Server helper tests that do not bind a port."""
from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.server import EventBus, _job_identity, inflight_summary, is_loopback_host


class LoopbackTests(unittest.TestCase):
    def test_loopback(self):
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertFalse(is_loopback_host("0.0.0.0"))
        self.assertFalse(is_loopback_host("192.168.1.5"))


class EventBusTests(unittest.TestCase):
    def test_drop_oldest_keeps_latest(self):
        q = queue.Queue(maxsize=2)
        EventBus._put_drop_oldest(q, {"n": 1})
        EventBus._put_drop_oldest(q, {"n": 2})
        EventBus._put_drop_oldest(q, {"n": 3})
        self.assertEqual(q.get_nowait()["n"], 2)
        self.assertEqual(q.get_nowait()["n"], 3)
        self.assertTrue(q.empty())


class JobIdentityTests(unittest.TestCase):
    def test_single_file_guess(self):
        subjects = {"ACI": "Artificial Computational Intelligence"}
        prefix, lecture = _job_identity(
            "Artificial and Computational Intelligence - Lecture 8.txt",
            {"prefix": "", "lecture_num": ""},
            ["one"],
            subjects,
        )
        self.assertTrue(prefix)
        self.assertEqual(lecture, "8")

    def test_multi_file_unique_prefix(self):
        subjects = {"NLP": "Natural Language Processing"}
        files = ["NLP_Lecture_1.txt", "NLP_Lecture_2.txt"]
        p1, l1 = _job_identity(files[0], {"prefix": "", "lecture_num": ""},
                               files, subjects)
        p2, l2 = _job_identity(files[1], {"prefix": "", "lecture_num": ""},
                               files, subjects)
        self.assertNotEqual(p1, p2)
        self.assertEqual(l1, "1")
        self.assertEqual(l2, "2")


class RunEventsReadTests(unittest.TestCase):
    def test_pages_jsonl(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            out = root / "Subj" / "Pref"
            out.mkdir(parents=True)
            path = out / "Pref_run_events.jsonl"
            path.write_text(
                '{"type":"pipeline_start"}\n{"type":"phase_start"}\n{"type":"pipeline_end"}\n',
                encoding="utf-8")
            with patch.object(config, "OUTPUTS_DIR", root):
                page = config.read_run_events("Subj", "Pref", offset=1, limit=1)
            self.assertEqual(page["total"], 3)
            self.assertEqual(len(page["events"]), 1)
            self.assertEqual(page["events"][0]["type"], "phase_start")
            self.assertTrue(page["truncated"])


class InFlightSummaryTests(unittest.TestCase):
    def test_includes_phase_and_usage(self):
        class FakePipe:
            model = "test-model"
            variant = "max"
            backend = "opencode"
            _current_phase = "enricher"
            _last_tool = "read"

            def _stats_snapshot(self):
                return {
                    "seconds": 12.0,
                    "cost": 0.042,
                    "tokens": {"input": 1100, "output": 200, "reasoning": 50},
                    "phases": {"extractor": {"seconds": 8, "cost": 0.02}},
                }

        payload = inflight_summary({
            "run_id": "run1",
            "subject": "NLP",
            "prefix": "NLP_Lecture_1",
            "abbr": "NLP",
            "phases": [1, 2, 3],
            "t0": 100.0,
            "active": True,
            "current_phase": "enricher",
            "pipeline": FakePipe(),
        })
        self.assertEqual(payload["status"], "running")
        self.assertEqual(payload["current_phase"], "enricher")
        self.assertEqual(payload["cost"], 0.042)
        self.assertEqual(payload["tokens"]["input"], 1100)
        self.assertEqual(payload["last_tool"], "read")
        self.assertEqual(payload["model"], "test-model")
        self.assertTrue(payload["in_flight"])

    def test_queued_without_pipeline(self):
        payload = inflight_summary({
            "run_id": "run2",
            "subject": "NLP",
            "prefix": "NLP_Lecture_2",
            "abbr": "NLP",
            "phases": [1],
            "t0": 1.0,
            "active": False,
            "pipeline": None,
        })
        self.assertEqual(payload["status"], "queued")
        self.assertIsNone(payload["current_phase"])
        self.assertEqual(payload["cost"], 0.0)


if __name__ == "__main__":
    unittest.main()
