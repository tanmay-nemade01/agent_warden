"""Subject profiles: per-subject transcripts, docs, outputs and prefixes."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import config, permissions
from app.pipeline import Pipeline
from app.server import Handler


class ProfileWorkspace:
    """A throw-away workspace with its own subjects/profiles config."""

    def __init__(self, root: Path):
        self.root = root
        self.workspace = root / "ws"
        self.outputs = self.workspace / "outputs"
        self.transcripts = self.workspace / "transcript files"
        self.docs = self.workspace / "companion_docs"
        self.topics = self.workspace / "topic_mappings"
        for d in (self.outputs, self.transcripts, self.docs, self.topics):
            d.mkdir(parents=True, exist_ok=True)
        (self.workspace / "subjects.json").write_text(
            json.dumps({"CV": "Computer Vision", "QC": "Quantum Computing"}),
            encoding="utf-8")

    def patches(self):
        return [
            patch.object(config, "WORKSPACE", self.workspace),
            patch.object(config, "OUTPUTS_DIR", self.outputs),
            patch.object(config, "TRANSCRIPTS_DIR", self.transcripts),
            patch.object(config, "DOCS_DIR", self.docs),
            patch.object(config, "TOPIC_MAPPINGS_DIR", self.topics),
            patch.object(config, "SUBJECTS_FILE", self.workspace / "subjects.json"),
            patch.object(config, "SUBJECT_PROFILES_FILE",
                         self.workspace / "subject_profiles.json"),
        ]


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = ProfileWorkspace(Path(self.tmp.name))
        self._patchers = self.ws.patches()
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.tmp.cleanup()

    # ------------------------------------------------------------ defaults
    def test_default_profile_keeps_legacy_layout(self):
        prof = config.subject_profile("QC")
        self.assertFalse(prof.explicit)
        self.assertEqual(prof.output_template, "{subject}/{prefix}")
        self.assertEqual(prof.prefix_template, "{abbr}_Lecture_{lecture}")
        base = config.output_paths("Quantum Computing", "QC_Lecture_1",
                                   abbr="QC")["base"]
        self.assertEqual(base, self.ws.outputs / "Quantum Computing" / "QC_Lecture_1")

    def test_default_write_roots_match_legacy(self):
        prof = config.subject_profile("QC")
        self.assertEqual(prof.write_roots(),
                         ["outputs/**", "topic_mappings/**"])

    def test_subject_lookup_by_name_or_abbr(self):
        self.assertEqual(config.subject_profile("Quantum Computing").abbr, "QC")
        self.assertEqual(config.subject_profile("QC").abbr, "QC")
        self.assertEqual(config.subject_profile("qc").abbr, "QC")

    # ------------------------------------------------------------- saving
    def test_save_profile_persists_overrides(self):
        prof = config.save_profile("QC", {
            "transcripts_dir": "transcript files/QC",
            "output_template": "{abbr}/notes/{prefix}",
            "prefix_template": "{abbr}-L{lecture}",
        })
        self.assertTrue(prof.explicit)
        self.assertEqual(prof.output_template, "{abbr}/notes/{prefix}")
        self.assertEqual(prof.prefix_template, "{abbr}-L{lecture}")
        self.assertEqual(prof.transcripts_dir,
                         self.ws.transcripts / "QC")
        # Reloaded from disk, not from memory.
        again = config.subject_profile("QC")
        self.assertEqual(again.output_template, "{abbr}/notes/{prefix}")

    def test_save_profile_rejects_unknown_subject(self):
        with self.assertRaises(ValueError):
            config.save_profile("NOPE", {"output_root": "outputs"})

    def test_save_profile_rejects_escaping_output_root(self):
        with self.assertRaises(ValueError):
            config.save_profile("QC", {"output_root": "../elsewhere"})

    def test_save_profile_rejects_unknown_field(self):
        with self.assertRaises(ValueError):
            config.save_profile("QC", {"nonsense": "x"})

    def test_delete_profile_restores_defaults(self):
        config.save_profile("QC", {"output_template": "{abbr}/{prefix}"})
        config.delete_profile("QC")
        self.assertFalse(config.subject_profile("QC").explicit)

    # ------------------------------------------------------------ outputs
    def test_output_template_variants(self):
        config.save_profile("QC", {"output_template": "{abbr}/notes/{prefix}"})
        base = config.output_paths("Quantum Computing", "QC_Lecture_1",
                                   abbr="QC")["base"]
        self.assertEqual(base, self.ws.outputs / "QC" / "notes" / "QC_Lecture_1")

    def test_output_root_isolates_subjects(self):
        config.save_profile("QC", {"output_root": "outputs/QC",
                                   "output_template": "{prefix}"})
        config.save_profile("CV", {"output_root": "outputs/CV",
                                   "output_template": "{prefix}"})
        qc = config.output_paths("Quantum Computing", "QC_Lecture_1", abbr="QC")["base"]
        cv = config.output_paths("Computer Vision", "CV_Lecture_1", abbr="CV")["base"]
        self.assertEqual(qc, self.ws.outputs / "QC" / "QC_Lecture_1")
        self.assertEqual(cv, self.ws.outputs / "CV" / "CV_Lecture_1")
        self.assertNotEqual(qc.parent, cv.parent)

    def test_output_template_rejects_escapes(self):
        config.save_profile("QC", {"output_template": "{subject}/../{prefix}"})
        with self.assertRaises(ValueError):
            config.output_paths("Quantum Computing", "QC_Lecture_1", abbr="QC")

    def test_output_rel_path_is_root_relative(self):
        config.save_profile("QC", {"output_root": "outputs/QC",
                                   "output_template": "{prefix}"})
        # The subject's own root sits inside the shared outputs root, so the
        # URL carries that extra hop.
        rel = config.output_rel_path("Quantum Computing", "QC_Lecture_1", abbr="QC")
        self.assertEqual(rel, "QC/QC_Lecture_1")

    # ------------------------------------------------------------ prefixes
    def test_prefix_template(self):
        config.save_profile("QC", {"prefix_template": "{abbr}-L{lecture}"})
        prof = config.subject_profile("QC")
        self.assertEqual(prof.prefix_for("3"), "QC-L3")
        self.assertEqual(prof.prefix_for(""), "QC-L")

    def test_default_prefix_template_matches_legacy(self):
        prof = config.subject_profile("QC")
        self.assertEqual(prof.prefix_for("7"), "QC_Lecture_7")
        self.assertEqual(prof.prefix_for(""), "QC_Lecture")

    # ---------------------------------------------------------- transcripts
    def test_transcripts_are_owned_per_subject(self):
        qc_dir = self.ws.transcripts / "QC"
        qc_dir.mkdir()
        (qc_dir / "lecture_one.txt").write_text("x", encoding="utf-8")
        (qc_dir / "lecture_two.txt").write_text("x", encoding="utf-8")
        cv_dir = self.ws.transcripts / "CV"
        cv_dir.mkdir()
        (cv_dir / "cv_only.txt").write_text("x", encoding="utf-8")

        config.save_profile("QC", {"transcripts_dir": "transcript files/QC"})
        config.save_profile("CV", {"transcripts_dir": "transcript files/CV"})

        qc = config.list_transcripts(abbr="QC")
        cv = config.list_transcripts(abbr="CV")
        self.assertEqual([t["name"] for t in qc],
                         ["lecture_one.txt", "lecture_two.txt"])
        self.assertEqual([t["name"] for t in cv], ["cv_only.txt"])
        self.assertTrue(all(t["abbr"] == "QC" for t in qc))

    def test_shared_flat_dir_is_attributed_by_filename(self):
        (self.ws.transcripts / "Quantum_computing_lecture_4.txt").write_text(
            "x", encoding="utf-8")
        found = config.list_transcripts(abbr="QC")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["abbr"], "QC")

    # ------------------------------------------------------------- history
    def test_history_scans_every_output_root(self):
        config.save_profile("QC", {"output_root": "outputs/QC",
                                   "output_template": "{prefix}"})
        folder = self.ws.outputs / "QC" / "QC_Lecture_1"
        folder.mkdir(parents=True)
        events = folder / "QC_Lecture_1_run_events.jsonl"
        events.write_text(json.dumps({
            "type": "pipeline_start", "subject": "Quantum Computing",
            "abbr": "QC", "prefix": "QC_Lecture_1", "phases": [1, 2, 3],
            "time": 1}) + "\n", encoding="utf-8")

        runs = config.list_past_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["subject"], "Quantum Computing")
        self.assertEqual(runs[0]["prefix"], "QC_Lecture_1")

    def test_artifacts_status_resolves_custom_root(self):
        config.save_profile("QC", {"output_root": "outputs/QC",
                                   "output_template": "{prefix}"})
        folder = self.ws.outputs / "QC" / "QC_Lecture_1"
        folder.mkdir(parents=True)
        (folder / "QC_Lecture_1_notes_dense.md").write_text("d", encoding="utf-8")
        (folder / "QC_Lecture_1_extraction_manifest.json").write_text(
            "{}", encoding="utf-8")
        notes = folder / "QC_Lecture_1_notes"
        notes.mkdir()
        (notes / "QC_Lecture_1_notes.html").write_text("<html></html>",
                                                       encoding="utf-8")
        arts = config.artifacts_status("Quantum Computing", "QC_Lecture_1",
                                       abbr="QC")
        self.assertTrue(arts["artifacts"]["extractor"])
        self.assertTrue(arts["artifacts"]["formatter"])
        # The enriched draft is missing, so the run resumes at phase 2.
        self.assertEqual(arts["resume_phases"], [2, 3])
        self.assertTrue(arts["html_url"].startswith("/outputs/QC/QC_Lecture_1/"))

    def test_delete_past_run_uses_profile_root(self):
        config.save_profile("QC", {"output_root": "outputs/QC",
                                   "output_template": "{prefix}"})
        folder = self.ws.outputs / "QC" / "QC_Lecture_1"
        folder.mkdir(parents=True)
        (folder / "f.txt").write_text("x", encoding="utf-8")
        res = config.delete_past_run("Quantum Computing", "QC_Lecture_1",
                                     abbr="QC")
        self.assertEqual(res["action"], "deleted")
        self.assertFalse(folder.exists())

    def test_html_url_keeps_path_separators(self):
        """Percent-encoding must not swallow the folder separators."""
        config.save_profile("QC", {"output_root": "outputs/QC",
                                   "output_template": "{abbr}/{prefix}"})
        folder = self.ws.outputs / "QC" / "QC" / "QC_Lecture_1"
        notes = folder / "QC_Lecture_1_notes"
        notes.mkdir(parents=True)
        (notes / "QC_Lecture_1_notes.html").write_text("<html></html>",
                                                       encoding="utf-8")
        arts = config.artifacts_status("Quantum Computing", "QC_Lecture_1",
                                       abbr="QC")
        self.assertEqual(
            arts["html_url"],
            "/outputs/QC/QC/QC_Lecture_1/QC_Lecture_1_notes/QC_Lecture_1_notes.html")
        self.assertNotIn("%2F", arts["html_url"])

    def test_html_url_encodes_spaces_in_the_subject_name(self):
        folder = self.ws.outputs / "Quantum Computing" / "QC_Lecture_1"
        notes = folder / "QC_Lecture_1_notes"
        notes.mkdir(parents=True)
        (notes / "QC_Lecture_1_notes.html").write_text("<html></html>",
                                                       encoding="utf-8")
        arts = config.artifacts_status("Quantum Computing", "QC_Lecture_1",
                                       abbr="QC")
        self.assertEqual(
            arts["html_url"],
            "/outputs/Quantum%20Computing/QC_Lecture_1/QC_Lecture_1_notes/"
            "QC_Lecture_1_notes.html")


class PermissionRootTests(unittest.TestCase):
    def test_write_roots_drive_the_permission_block(self):
        block = permissions.opencode_permission_block(
            ["outputs/CV/**", "topic_mappings/**"])
        edit_rules = {(r["resource"], r["effect"]) for r in block
                      if r["action"] == "edit"}
        self.assertIn(("outputs/CV/**", "allow"), edit_rules)
        self.assertIn(("topic_mappings/**", "allow"), edit_rules)
        self.assertNotIn(("outputs/**", "allow"), edit_rules)
        self.assertIn(("*", "deny"), edit_rules)

    def test_default_block_keeps_legacy_roots(self):
        edit_rules = {(r["resource"], r["effect"])
                      for r in permissions.opencode_permission_block()
                      if r["action"] == "edit"}
        self.assertIn(("outputs/**", "allow"), edit_rules)
        self.assertIn(("topic_mappings/**", "allow"), edit_rules)

    def test_commandcode_allow_list_follows_roots(self):
        perms = permissions.commandcode_permissions(["outputs/CV/**"])
        self.assertIn("Edit(outputs/CV/**)", perms["allow"])
        self.assertIn("Write(outputs/CV/**)", perms["allow"])
        self.assertNotIn("Edit(outputs/**)", perms["allow"])

    def test_inline_config_matches_the_job_file(self):
        """OPENCODE_CONFIG_CONTENT must not fall back to the default roots."""
        import json as _json
        with tempfile.TemporaryDirectory() as td:
            paths = permissions.write_job_configs(Path(td), ["outputs/CV/**"])
            env = permissions.opencode_env(paths["opencode"], ["outputs/CV/**"])
            inline = _json.loads(env["OPENCODE_CONFIG_CONTENT"])
            on_disk = _json.loads(paths["opencode"].read_text(encoding="utf-8"))
            self.assertEqual(inline["permissions"], on_disk["permissions"])
            edit_rules = {(r["resource"], r["effect"])
                          for r in inline["permissions"] if r["action"] == "edit"}
            self.assertIn(("outputs/CV/**", "allow"), edit_rules)
            self.assertNotIn(("outputs/**", "allow"), edit_rules)


class PipelineProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = ProfileWorkspace(Path(self.tmp.name))
        self._patchers = self.ws.patches()
        for p in self._patchers:
            p.start()
        qc_dir = self.ws.transcripts / "QC"
        qc_dir.mkdir()
        self.transcript = qc_dir / "lecture_one.txt"
        self.transcript.write_text("hello", encoding="utf-8")

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.tmp.cleanup()

    def _pipeline(self, **kw):
        params = dict(
            subject="Quantum Computing", abbr="QC", prefix="QC_Lecture_1",
            lecture_num="1", transcript=str(self.transcript))
        params.update(kw)
        return Pipeline(**params)

    def test_pipeline_uses_profile_output_dir(self):
        config.save_profile("QC", {"transcripts_dir": "transcript files/QC",
                                   "output_template": "{abbr}/notes/{prefix}"})
        p = self._pipeline()
        self.assertEqual(p.out, self.ws.outputs / "QC" / "notes" / "QC_Lecture_1")
        self.assertEqual(p.out_rel, "outputs/QC/notes/QC_Lecture_1")

    def test_path_override_note_appears_for_custom_layout(self):
        config.save_profile("QC", {"transcripts_dir": "transcript files/QC",
                                   "output_template": "{abbr}/notes/{prefix}"})
        msg = self._pipeline()._agent_message(1)
        self.assertIn("PATH OVERRIDE", msg)
        self.assertIn("outputs/QC/notes/QC_Lecture_1", msg)

    def test_no_path_override_for_default_layout(self):
        config.save_profile("QC", {"transcripts_dir": "transcript files/QC"})
        msg = self._pipeline()._agent_message(1)
        self.assertNotIn("PATH OVERRIDE", msg)

    def test_transcript_must_live_under_the_subject_folder(self):
        config.save_profile("QC", {"transcripts_dir": "transcript files/QC"})
        stray = self.ws.transcripts / "stray.txt"
        stray.write_text("x", encoding="utf-8")
        with self.assertRaises(Exception):
            self._pipeline(transcript=str(stray))


class BatchPlanningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = ProfileWorkspace(Path(self.tmp.name))
        self._patchers = self.ws.patches()
        for p in self._patchers:
            p.start()
        for abbr, folder, name in (("QC", "QC", "qc_lecture_1.txt"),
                                   ("CV", "CV", "cv_lecture_1.txt")):
            d = self.ws.transcripts / folder
            d.mkdir()
            (d / name).write_text("x", encoding="utf-8")
            config.save_profile(abbr, {"transcripts_dir": f"transcript files/{folder}"})

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.tmp.cleanup()

    def _plan(self, body):
        handler = object.__new__(Handler)
        handler._json = MagicMock()
        with patch("app.server.config.is_backend_available", return_value=(True, "opencode")), \
             patch("app.server.config.list_models", return_value={"ok": True, "models": []}), \
             patch("app.server.config.resolve_model_choice",
                   return_value=("opencode-go/deepseek-v4-flash", "max")):
            return handler._plan_jobs(body, start=False)

    def test_batch_plans_every_subject(self):
        res = self._plan({
            "phases": [1, 2, 3],
            "subjects": [
                {"abbr": "QC", "transcripts": [str(self.ws.transcripts / "QC" / "qc_lecture_1.txt")]},
                {"abbr": "CV", "transcripts": [str(self.ws.transcripts / "CV" / "cv_lecture_1.txt")]},
            ],
        })
        self.assertTrue(res["ok"])
        self.assertEqual(res["subjects"], 2)
        subjects = sorted(j["subject"] for j in res["jobs"])
        self.assertEqual(subjects, ["Computer Vision", "Quantum Computing"])
        self.assertEqual(res["rejected"], [])

    def test_batch_reports_one_bad_subject_without_failing_the_rest(self):
        res = self._plan({
            "phases": [1],
            "subjects": [
                {"abbr": "NOPE", "transcripts": ["whatever.txt"]},
                {"abbr": "QC", "transcripts": [str(self.ws.transcripts / "QC" / "qc_lecture_1.txt")]},
            ],
        })
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["jobs"]), 1)
        self.assertEqual(res["rejected"][0]["subject"], "NOPE")

    def test_batch_rejects_transcript_from_another_subject(self):
        res = self._plan({
            "phases": [1],
            "subjects": [
                {"abbr": "QC", "transcripts": [str(self.ws.transcripts / "CV" / "cv_lecture_1.txt")]},
            ],
        })
        self.assertFalse(res["ok"])
        self.assertEqual(len(res["rejected"]), 1)
        self.assertIn("transcripts folder", res["rejected"][0]["reason"])

    def test_single_subject_body_still_works(self):
        res = self._plan({
            "abbr": "QC", "phases": [2],
            "transcripts": [str(self.ws.transcripts / "QC" / "qc_lecture_1.txt")],
        })
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["jobs"]), 1)
        self.assertEqual(res["jobs"][0]["phases"], [2])

    def test_batch_honours_per_subject_prefix_template(self):
        config.save_profile("QC", {"transcripts_dir": "transcript files/QC",
                                   "prefix_template": "{abbr}-L{lecture}"})
        res = self._plan({
            "phases": [1],
            "subjects": [
                {"abbr": "QC", "transcripts": [str(self.ws.transcripts / "QC" / "qc_lecture_1.txt")]},
            ],
        })
        self.assertEqual(res["jobs"][0]["prefix"], "QC-L1")


class OutputServingTests(unittest.TestCase):
    """/outputs/<path> must resolve percent-encoded names and templates."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = ProfileWorkspace(Path(self.tmp.name))
        self._patchers = self.ws.patches()
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.tmp.cleanup()

    def _serve(self, url_path: str):
        handler = object.__new__(Handler)
        handler._security_headers = MagicMock()
        handler.send_response = MagicMock()
        handler.send_header = MagicMock()
        handler.end_headers = MagicMock()
        handler.wfile = MagicMock()
        handler._text = MagicMock()
        handler._serve_output(url_path)
        return handler

    def test_encoded_subject_name_is_served(self):
        notes = (self.ws.outputs / "Quantum Computing" / "QC_Lecture_1"
                 / "QC_Lecture_1_notes")
        notes.mkdir(parents=True)
        (notes / "QC_Lecture_1_notes.html").write_text("<html>ok</html>",
                                                       encoding="utf-8")
        handler = self._serve(
            "/outputs/Quantum%20Computing/QC_Lecture_1/QC_Lecture_1_notes/"
            "QC_Lecture_1_notes.html")
        handler._text.assert_not_called()
        handler.send_response.assert_called_with(200)
        written = handler.wfile.write.call_args[0][0]
        self.assertIn(b"ok", written)

    def test_custom_output_root_is_served(self):
        config.save_profile("QC", {"output_root": "outputs/QC",
                                   "output_template": "{prefix}"})
        notes = self.ws.outputs / "QC" / "QC_Lecture_1" / "QC_Lecture_1_notes"
        notes.mkdir(parents=True)
        (notes / "QC_Lecture_1_notes.html").write_text("<html>ok</html>",
                                                       encoding="utf-8")
        handler = self._serve(
            "/outputs/QC/QC_Lecture_1/QC_Lecture_1_notes/QC_Lecture_1_notes.html")
        handler._text.assert_not_called()
        handler.send_response.assert_called_with(200)

    def test_output_root_outside_the_shared_outputs_is_served(self):
        config.save_profile("QC", {"output_root": "notes",
                                   "output_template": "{abbr}/{prefix}"})
        notes = self.ws.workspace / "notes" / "QC" / "QC_Lecture_1" / "QC_Lecture_1_notes"
        notes.mkdir(parents=True)
        (notes / "QC_Lecture_1_notes.html").write_text("<html>ok</html>",
                                                       encoding="utf-8")
        handler = self._serve(
            "/outputs/QC/QC_Lecture_1/QC_Lecture_1_notes/QC_Lecture_1_notes.html")
        handler._text.assert_not_called()
        handler.send_response.assert_called_with(200)

    def test_traversal_is_refused(self):
        handler = self._serve("/outputs/..%2F..%2Fsecret.txt")
        handler._text.assert_called()
        self.assertEqual(handler._text.call_args[0][0], "not found")


class BatchLaunchTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = ProfileWorkspace(Path(self.tmp.name))
        self._patchers = self.ws.patches()
        for p in self._patchers:
            p.start()
        for abbr, folder, name in (("QC", "QC", "qc_lecture_1.txt"),
                                   ("CV", "CV", "cv_lecture_1.txt")):
            d = self.ws.transcripts / folder
            d.mkdir()
            (d / name).write_text("x", encoding="utf-8")
            config.save_profile(abbr, {"transcripts_dir": f"transcript files/{folder}"})
        import app.server as server_mod
        self.server = server_mod
        server_mod.STATE["runs"].clear()

    def tearDown(self):
        for p in reversed(self._patchers):
            p.stop()
        self.tmp.cleanup()

    def test_batch_run_registers_a_run_per_lecture(self):
        started: list[dict] = []

        class FakePipeline:
            def __init__(self, **kw):
                started.append(kw)
                self.stop_flag = False

            def run(self):
                pass

            def stop(self):
                self.stop_flag = True

        handler = object.__new__(Handler)
        handler._json = MagicMock()
        body = {
            "phases": [1, 2, 3],
            "subjects": [
                {"abbr": "QC", "transcripts": [str(self.ws.transcripts / "QC" / "qc_lecture_1.txt")]},
                {"abbr": "CV", "transcripts": [str(self.ws.transcripts / "CV" / "cv_lecture_1.txt")]},
            ],
        }
        with patch("app.server.Pipeline", FakePipeline), \
             patch("app.server.config.is_backend_available", return_value=(True, "opencode")), \
             patch("app.server.config.list_models", return_value={"ok": True, "models": []}), \
             patch("app.server.config.resolve_model_choice",
                   return_value=("opencode-go/deepseek-v4-flash", "max")):
            res = handler._plan_jobs(body, start=True)

        self.assertTrue(res["ok"])
        self.assertEqual(len(res["jobs"]), 2)
        self.assertEqual(len(started), 2)
        abbrs = sorted(kw["abbr"] for kw in started)
        self.assertEqual(abbrs, ["CV", "QC"])
        # Every job got its own run id, so they are tracked independently.
        run_ids = {j["run_id"] for j in res["jobs"]}
        self.assertEqual(len(run_ids), 2)
        self.assertTrue(all(rid.startswith("run") for rid in run_ids))

    def test_per_subject_slot_cap_is_enforced(self):
        from app import server as server_mod
        server_mod.set_max_parallel(4)
        server_mod.set_max_parallel_per_subject(1)
        try:
            self.assertTrue(server_mod.try_acquire_slot("QC", 1))
            # QC is at its cap even though global slots remain.
            self.assertFalse(server_mod.try_acquire_slot("QC", 1))
            # A different subject can still take a slot.
            self.assertTrue(server_mod.try_acquire_slot("CV", 1))
            server_mod.release_slot("QC")
            self.assertTrue(server_mod.try_acquire_slot("QC", 1))
        finally:
            server_mod.set_max_parallel_per_subject(0)
            server_mod.set_max_parallel(2)


if __name__ == "__main__":
    unittest.main()
