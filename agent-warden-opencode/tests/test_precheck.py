"""Tests for phase pre-verification and fast-forward optimization."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import config, gates
from app.pipeline import ADAPTATION_BY_AGENT, PhaseError, Pipeline


class PhasePrecheckTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.outputs = self.root / "outputs"
        self.transcripts = self.root / "transcripts"
        self.outputs.mkdir(parents=True)
        self.transcripts.mkdir(parents=True)

        self.subject = "NLP"
        self.abbr = "NLP"
        self.prefix = "NLP_Lecture_1"
        self.lecture_num = "1"

        # Create dummy transcript
        t_file = self.transcripts / "NLP_Lecture_1.txt"
        t_file.write_text("sample transcript", encoding="utf-8")
        self.transcript_path = str(t_file)

        # Patch config directories
        self.patchers = [
            patch.object(config, "OUTPUTS_DIR", self.outputs),
            patch.object(config, "TRANSCRIPTS_DIR", self.transcripts),
            patch.object(config, "WORKSPACE", self.root),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.td.cleanup()

    def _make_pipeline(self) -> Pipeline:
        p = Pipeline(
            subject=self.subject,
            abbr=self.abbr,
            prefix=self.prefix,
            lecture_num=self.lecture_num,
            transcript=self.transcript_path,
            phases=[1, 2, 3],
        )
        p.out.mkdir(parents=True, exist_ok=True)
        return p

    def test_adaptation_prompts_contain_precheck(self):
        for agent_num in (1, 2, 3):
            prompt = ADAPTATION_BY_AGENT[agent_num]
            self.assertIn("Pre-check optimization", prompt)
            self.assertIn("PHASE_COMPLETE", prompt)

    @patch("app.gates.gate_lint_dense")
    @patch("app.gates.gate_verify_manifest")
    def test_phase1_skips_agent_when_files_exist_and_pass(
            self, mock_verify, mock_lint):
        mock_lint.return_value = gates.GateResult(
            gate="lint_dense", passed=True, findings=["[PASS] dense ok"])
        mock_verify.return_value = gates.GateResult(
            gate="verify_manifest", passed=True, findings=["[PASS] manifest ok"])

        p = self._make_pipeline()
        dense = p.out / f"{self.prefix}_notes_dense.md"
        manifest = p.out / f"{self.prefix}_extraction_manifest.json"
        dense.write_text("# Dense Draft", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")

        p._run_agent = MagicMock()

        res = p._phase_extractor()
        self.assertTrue(res)
        p._run_agent.assert_not_called()
        mock_lint.assert_called_once()
        mock_verify.assert_called_once()

    @patch("app.gates.gate_lint_dense")
    @patch("app.gates.gate_verify_manifest")
    def test_phase1_runs_agent_when_outputs_missing(
            self, mock_verify, mock_lint):
        mock_lint.return_value = gates.GateResult(
            gate="lint_dense", passed=True, findings=["[PASS] dense ok"])
        mock_verify.return_value = gates.GateResult(
            gate="verify_manifest", passed=True, findings=["[PASS] manifest ok"])

        p = self._make_pipeline()
        dense = p.out / f"{self.prefix}_notes_dense.md"
        manifest = p.out / f"{self.prefix}_extraction_manifest.json"

        def fake_run_agent(agent, msg, title, extra_env=None):
            dense.write_text("# Generated Dense", encoding="utf-8")
            manifest.write_text("{}", encoding="utf-8")
            return 0, ["PHASE_COMPLETE"]

        p._run_agent = MagicMock(side_effect=fake_run_agent)

        res = p._phase_extractor()
        self.assertTrue(res)
        p._run_agent.assert_called_once()

    @patch("app.gates.gate_lint_dense")
    @patch("app.gates.gate_verify_manifest")
    def test_phase2_skips_agent_when_enriched_exists_and_passes(
            self, mock_verify, mock_lint):
        mock_lint.return_value = gates.GateResult(
            gate="lint_dense", passed=True, findings=["[PASS] enriched ok"])
        mock_verify.return_value = gates.GateResult(
            gate="verify_manifest", passed=True, findings=["[PASS] manifest ok"])

        p = self._make_pipeline()
        dense = p.out / f"{self.prefix}_notes_dense.md"
        manifest = p.out / f"{self.prefix}_extraction_manifest.json"
        enriched = p.out / f"{self.prefix}_notes_enriched.md"

        dense.write_text("# Dense Draft", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")
        enriched.write_text("# Enriched Draft with all content", encoding="utf-8")

        p._run_agent = MagicMock()

        res = p._phase_enricher()
        self.assertTrue(res)
        p._run_agent.assert_not_called()

    @patch("app.gates.gate_lint_dense")
    @patch("app.gates.gate_verify_manifest")
    def test_phase2_runs_agent_when_enriched_has_verify_marker(
            self, mock_verify, mock_lint):
        mock_lint.return_value = gates.GateResult(
            gate="lint_dense", passed=True, findings=["[PASS] ok"])
        mock_verify.return_value = gates.GateResult(
            gate="verify_manifest", passed=True, findings=["[PASS] ok"])

        p = self._make_pipeline()
        dense = p.out / f"{self.prefix}_notes_dense.md"
        manifest = p.out / f"{self.prefix}_extraction_manifest.json"
        enriched = p.out / f"{self.prefix}_notes_enriched.md"

        dense.write_text("# Dense Draft", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")
        enriched.write_text("# Enriched Draft *[verify]* leftover", encoding="utf-8")

        def fake_run_agent(agent, msg, title, extra_env=None):
            enriched.write_text("# Clean Enriched Draft", encoding="utf-8")
            return 0, ["PHASE_COMPLETE"]

        p._run_agent = MagicMock(side_effect=fake_run_agent)

        res = p._phase_enricher()
        self.assertTrue(res)
        p._run_agent.assert_called_once()

    @patch("app.gates.gate_lint_html")
    @patch("app.gates.gate_verify_manifest")
    def test_phase3_skips_agent_when_html_exists_and_passes(
            self, mock_verify, mock_lint):
        mock_lint.return_value = gates.GateResult(
            gate="lint_html", passed=True, findings=["[PASS] html ok"])
        mock_verify.return_value = gates.GateResult(
            gate="verify_manifest", passed=True, findings=["[PASS] manifest ok"])

        p = self._make_pipeline()
        enriched = p.out / f"{self.prefix}_notes_enriched.md"
        manifest = p.out / f"{self.prefix}_extraction_manifest.json"
        html = p.out / f"{self.prefix}_notes" / f"{self.prefix}_notes.html"
        html.parent.mkdir(parents=True)

        enriched.write_text("# Enriched Draft", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")
        html.write_text("<!DOCTYPE html><html><body>Notes</body></html>", encoding="utf-8")

        p._run_agent = MagicMock()

        res = p._phase_formatter()
        self.assertTrue(res)
        p._run_agent.assert_not_called()

    @patch("app.gates.gate_lint_html")
    @patch("app.gates.gate_verify_manifest")
    def test_phase3_runs_agent_when_html_missing(
            self, mock_verify, mock_lint):
        mock_lint.return_value = gates.GateResult(
            gate="lint_html", passed=True, findings=["[PASS] html ok"])
        mock_verify.return_value = gates.GateResult(
            gate="verify_manifest", passed=True, findings=["[PASS] manifest ok"])

        p = self._make_pipeline()
        enriched = p.out / f"{self.prefix}_notes_enriched.md"
        manifest = p.out / f"{self.prefix}_extraction_manifest.json"
        html = p.out / f"{self.prefix}_notes" / f"{self.prefix}_notes.html"

        enriched.write_text("# Enriched Draft", encoding="utf-8")
        manifest.write_text("{}", encoding="utf-8")

        def fake_run_agent(agent, msg, title, extra_env=None):
            html.parent.mkdir(parents=True, exist_ok=True)
            html.write_text("<!DOCTYPE html><html><body>Generated HTML</body></html>", encoding="utf-8")
            return 0, ["PHASE_COMPLETE"]

        p._run_agent = MagicMock(side_effect=fake_run_agent)

        res = p._phase_formatter()
        self.assertTrue(res)
        p._run_agent.assert_called_once()


if __name__ == "__main__":
    unittest.main()
