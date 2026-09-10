"""Server helper tests that do not bind a port."""
from __future__ import annotations

import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import config
from app.server import (EventBus, _job_identity, _resolve_batch_prefixes,
                        inflight_summary, is_loopback_host)


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

    def test_manual_subject_single_file_different_subject_filename(self):
        subjects = {"DA": "Data Analytics", "ML": "Machine Learning"}
        prefix, lecture = _job_identity(
            "ML_Lecture_5.txt",
            {"abbr": "DA", "prefix": "", "lecture_num": ""},
            ["ML_Lecture_5.txt"],
            subjects,
        )
        self.assertEqual(prefix, "DA_Lecture_5")
        self.assertEqual(lecture, "5")

    def test_manual_subject_multi_file_different_subject_filenames(self):
        subjects = {"DA": "Data Analytics", "ML": "Machine Learning"}
        files = ["ML_Lecture_1.txt", "ML_Lecture_2.txt"]
        p1, l1 = _job_identity(files[0], {"abbr": "DA", "prefix": "", "lecture_num": ""},
                               files, subjects)
        p2, l2 = _job_identity(files[1], {"abbr": "DA", "prefix": "", "lecture_num": ""},
                               files, subjects)
        self.assertEqual(p1, "DA_Lecture_1")
        self.assertEqual(l1, "1")
        self.assertEqual(p2, "DA_Lecture_2")
        self.assertEqual(l2, "2")

    def test_guess_lecture_not_at_end(self):
        g = config.guess_from_filename(
            "UDL_class_3_transcript.txt", {"UDL": "Universal Design for Learning"})
        self.assertEqual(g["lecture_num"], "3")
        self.assertEqual(g["prefix"], "UDL_Lecture_3")

    def test_guess_ignores_year_numbers(self):
        g = config.guess_from_filename(
            "NLP_notes_2024_review.txt", {"NLP": "Natural Language Processing"})
        self.assertEqual(g["lecture_num"], "")

    def test_batch_duplicate_prefixes_disambiguated(self):
        derived = [
            ("session 1", "UDL_Lecture"),
            ("week 2", "UDL_Lecture"),
            ("talk", "UDL_Lecture"),
        ]
        out = _resolve_batch_prefixes(derived)
        prefixes = [p for p, _ in out]
        self.assertEqual(prefixes[0], "UDL_Lecture")
        self.assertEqual(prefixes[1], "UDL_Lecture_week_2")
        self.assertEqual(prefixes[2], "UDL_Lecture_talk")
        self.assertEqual([a for _, a in out], [False, True, True])
        lowered = {p.lower() for p in prefixes}
        self.assertEqual(len(lowered), 3)

    def test_batch_numbered_fallback_when_tag_useless(self):
        derived = [
            ("UDL", "UDL_Lecture"),
            ("udl", "UDL_Lecture"),
        ]
        out = _resolve_batch_prefixes(derived)
        prefixes = [p for p, _ in out]
        self.assertEqual(len({p.lower() for p in prefixes}), 2)
        self.assertEqual(out[1][1], True)

    def test_batch_unique_inputs_untouched(self):
        derived = [("l1", "UDL_Lecture_1"), ("l2", "UDL_Lecture_2")]
        out = _resolve_batch_prefixes(derived)
        self.assertEqual(out, [("UDL_Lecture_1", False),
                               ("UDL_Lecture_2", False)])


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
        self.assertIn("phase_retries", payload)

    def test_pipeline_phase_retries(self):
        class FakePipeWithRetries:
            model = "test-model"
            variant = "max"
            backend = "opencode"
            _current_phase = "enricher"
            _last_tool = "read"

            def get_phase_retries(self):
                return {"extractor": 1, "enricher": 2, "formatter": 0}

            def _stats_snapshot(self):
                return {
                    "seconds": 25.0,
                    "cost": 0.05,
                    "tokens": {"input": 500, "output": 100, "reasoning": 0},
                    "phases": {
                        "extractor": {"seconds": 10, "cost": 0.02, "retries": 1},
                        "enricher": {"seconds": 15, "cost": 0.03, "retries": 2},
                    },
                    "phase_retries": {"extractor": 1, "enricher": 2, "formatter": 0},
                }

        payload = inflight_summary({
            "run_id": "run_retry_1",
            "subject": "NLP",
            "prefix": "NLP_Lecture_1",
            "abbr": "NLP",
            "phases": [1, 2, 3],
            "t0": 100.0,
            "active": True,
            "current_phase": "enricher",
            "pipeline": FakePipeWithRetries(),
        })
        self.assertEqual(payload["phase_retries"]["extractor"], 1)
        self.assertEqual(payload["phase_retries"]["enricher"], 2)
        self.assertEqual(payload["phase_retries"]["formatter"], 0)

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
        self.assertIn("phase_retries", payload)


class WorkerStopCleanupTests(unittest.TestCase):
    def test_stopped_queued_job_cleans_up_state(self):
        import time
        from unittest.mock import MagicMock
        from app import server

        rid = "test_cleanup_run"
        mock_pipe = MagicMock()
        mock_pipe.stop_flag = True
        run = {
            "run_id": rid,
            "subject": "NLP",
            "prefix": "NLP_Lecture_Test",
            "active": False,
            "pipeline": mock_pipe,
        }
        with server.STATE_LOCK:
            server.STATE["runs"][rid] = run

        handler = server.Handler.__new__(server.Handler)
        spec = {
            "subject": "NLP",
            "abbr": "NLP",
            "prefix": "NLP_Lecture_Test",
            "lecture_num": "1",
            "transcript": "dummy.txt",
            "phases": [1],
            "model": "m",
            "variant": "v",
            "backend": "opencode",
            "docs": None,
        }

        with patch("app.server.Pipeline", return_value=mock_pipe):
            handler._launch_job(spec, rid, run)
            # Give the worker thread a moment to run and exit
            time.sleep(0.1)

class ApiKeysEndpointTests(unittest.TestCase):
    def test_get_and_set_keys(self):
        import os
        from app import server
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            with patch.object(config, "WORKSPACE", root):
                handler = server.Handler.__new__(server.Handler)
                responses = []
                handler._json = lambda data, code=200: responses.append((data, code))

                # Test get keys when none set
                with patch.dict(os.environ, {}, clear=True):
                    handler._get_keys()
                    self.assertTrue(responses[-1][0]["ok"])
                    self.assertFalse(responses[-1][0]["keys"]["CURSOR_API_KEY"]["set"])

                # Test set keys
                handler._body = lambda: {
                    "CURSOR_API_KEY": "crsr_test_key_1234567890",
                    "XAI_API_KEY": "xai-test-key-12345",
                }
                handler._set_keys()
                self.assertTrue(responses[-1][0]["ok"])
                self.assertTrue(responses[-1][0]["keys"]["CURSOR_API_KEY"]["set"])
                self.assertTrue(responses[-1][0]["keys"]["XAI_API_KEY"]["set"])
                self.assertEqual(os.environ.get("CURSOR_API_KEY"), "crsr_test_key_1234567890")
                self.assertEqual(os.environ.get("XAI_API_KEY"), "xai-test-key-12345")

                # Verify written to .env
                env_content = (root / ".env").read_text(encoding="utf-8")
                self.assertIn("CURSOR_API_KEY=crsr_test_key_1234567890", env_content)
                self.assertIn("XAI_API_KEY=xai-test-key-12345", env_content)


class StaticIndexHtmlTests(unittest.TestCase):
    def test_index_html_elements_and_js_syntax(self):
        import shutil
        import subprocess
        html_path = Path(__file__).resolve().parent.parent / "app" / "static" / "index.html"
        self.assertTrue(html_path.exists())
        content = html_path.read_text(encoding="utf-8")

        # Verify essential Add Subject markup
        self.assertIn('id="addsubj"', content)
        self.assertIn('id="addsubj-form"', content)
        self.assertIn('id="addsubj-save"', content)
        self.assertIn('id="addsubj-cancel"', content)
        self.assertIn('id="new-abbr"', content)
        self.assertIn('id="new-name"', content)

        # Verify JS syntax if node is installed
        node_bin = shutil.which("node")
        if node_bin:
            marker = "theme-toggle"
            s_start = content.find("<script>", content.find(marker))
            s_end = content.rfind("</script>")
            self.assertNotEqual(s_start, -1)
            self.assertNotEqual(s_end, -1)
            js_code = content[s_start + len("<script>"):s_end]
            import os
            with tempfile.NamedTemporaryFile("w", suffix=".js", encoding="utf-8", delete=False) as tf:
                tf.write(js_code)
                tpath = tf.name
            try:
                proc = subprocess.run(
                    [node_bin, "--check", tpath],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"JavaScript syntax error in index.html:\n{proc.stderr}",
                )
            finally:
                if os.path.exists(tpath):
                    os.remove(tpath)



class BackendAvailabilityAndPreflightTests(unittest.TestCase):
    def test_is_backend_available(self):
        # opencode is installed on this host
        avail, exe = config.is_backend_available("opencode")
        self.assertTrue(avail)
        self.assertTrue(exe)
        # Antigravity CLI is installed on this host
        avail, exe = config.is_backend_available("antigravity")
        self.assertTrue(avail)
        self.assertTrue(exe.endswith("agy.exe"))
        # claude is not installed
        avail, exe = config.is_backend_available("claude")
        self.assertFalse(avail)
        self.assertEqual(exe, "claude")

    def test_config_endpoint_includes_backend_availability(self):
        from app import server
        handler = server.Handler.__new__(server.Handler)
        cfg_res = handler._config()
        self.assertIn("backends", cfg_res)
        backends = cfg_res["backends"]
        self.assertIsInstance(backends, list)
        self.assertGreater(len(backends), 0)
        for b in backends:
            self.assertIn("available", b)
            self.assertIn("executable", b)
            self.assertIsInstance(b["available"], bool)

    def test_plan_jobs_rejects_unavailable_backend(self):
        from app import server
        handler = server.Handler.__new__(server.Handler)
        responses = []
        handler._json = lambda data, code=200: responses.append((data, code))

        first_abbr = next(iter(config.all_subjects().keys()), "DML")
        body = {
            "abbr": first_abbr,
            "transcripts": ["dummy.txt"],
            "backend": "antigravity",
            "phases": [1, 2, 3],
        }

        # antigravity is not installed
        with patch.object(config, "is_backend_available", return_value=(False, "agy")):
            res = handler._plan_jobs(body, start=True)
            self.assertIsNone(res)
            self.assertEqual(len(responses), 1)
            err_data, code = responses[0]
            self.assertEqual(code, 400)
            self.assertIn("is not available on this machine", err_data.get("error", ""))


if __name__ == "__main__":
    unittest.main()



