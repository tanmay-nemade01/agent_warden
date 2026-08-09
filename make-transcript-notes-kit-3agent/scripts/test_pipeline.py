#!/usr/bin/env python3
"""Regression tests for strict assembly, fidelity, and math-safe readability."""

import json
import os
import tempfile
import unittest

import lint as html_lint
import lint_dense
import section_splitter
import verify_manifest


class PipelineRegressionTests(unittest.TestCase):
    def _verify(self, manifest, markdown, phase="dense"):
        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = os.path.join(temp_dir, "manifest.json")
            markdown_path = os.path.join(temp_dir, "notes.md")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with open(markdown_path, "w", encoding="utf-8") as handle:
                handle.write(markdown)
            return verify_manifest.verify_manifest(
                manifest_path,
                markdown_path,
                phase=phase,
            )

    def test_assembly_fails_when_an_expected_section_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            inventory = [
                {
                    "num": 1,
                    "title": "3.1 First",
                    "file": "section_01.md",
                    "subsections": [],
                },
                {
                    "num": 2,
                    "title": "3.2 Missing",
                    "file": "section_02.md",
                    "subsections": [],
                },
            ]
            with open(
                os.path.join(temp_dir, "_inventory.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(inventory, handle)
            with open(
                os.path.join(temp_dir, "section_01.md"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("## 3.1 First")

            with self.assertRaises(FileNotFoundError):
                section_splitter.assemble_files(
                    temp_dir,
                    os.path.join(temp_dir, "assembled.md"),
                    format_type="md",
                )
            self.assertFalse(os.path.exists(os.path.join(temp_dir, "assembled.md")))

    def test_split_invalidates_stale_html_fragments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path = os.path.join(temp_dir, "notes.md")
            stale_html = os.path.join(temp_dir, "section_01.html")
            with open(markdown_path, "w", encoding="utf-8") as handle:
                handle.write("# Topic\n\n## 3.1 Current\n### 3.1.1 Detail\nContent.")
            with open(stale_html, "w", encoding="utf-8") as handle:
                handle.write("<h2>Stale</h2>")

            section_splitter.split_markdown(markdown_path, temp_dir)

            self.assertFalse(os.path.exists(stale_html))
            with self.assertRaises(FileNotFoundError):
                section_splitter.assemble_files(
                    temp_dir,
                    os.path.join(temp_dir, "body.html"),
                )

    def test_assembly_rejects_empty_fragments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(
                os.path.join(temp_dir, "_inventory.json"),
                "w",
                encoding="utf-8",
            ) as handle:
                json.dump(
                    [{
                        "num": 1,
                        "title": "3.1 Empty",
                        "file": "section_01.md",
                        "subsections": [],
                    }],
                    handle,
                )
            open(os.path.join(temp_dir, "section_01.md"), "w", encoding="utf-8").close()
            with self.assertRaises(FileNotFoundError):
                section_splitter.assemble_files(
                    temp_dir,
                    os.path.join(temp_dir, "assembled.md"),
                    format_type="md",
                )

    def test_section_summaries_are_hash_bound_and_stale_edits_fail(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path = os.path.join(temp_dir, "notes.md")
            with open(markdown_path, "w", encoding="utf-8") as handle:
                handle.write("# Topic\n\n## 3.1 Update\n### 3.1.1 Detail\nOriginal.")
            section_splitter.split_markdown(markdown_path, temp_dir)
            summary_path = os.path.join(temp_dir, "section_01_summary.json")
            with open(summary_path, "w", encoding="utf-8") as handle:
                json.dump({"id": "3.1", "exam_revision": {}}, handle)

            section_splitter.bind_summaries(temp_dir)
            section_splitter.validate_summaries(temp_dir)

            with open(
                os.path.join(temp_dir, "section_01.md"),
                "a",
                encoding="utf-8",
            ) as handle:
                handle.write("\nChanged after summary.")
            with self.assertRaises(ValueError):
                section_splitter.validate_summaries(temp_dir)

    def test_agent2_split_invalidates_old_summaries_and_binding_checks_id(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path = os.path.join(temp_dir, "notes.md")
            stale_summary = os.path.join(temp_dir, "section_01_summary.json")
            with open(markdown_path, "w", encoding="utf-8") as handle:
                handle.write("# Topic\n\n## 3.1 Update\n### 3.1.1 Detail\nCurrent.")
            with open(stale_summary, "w", encoding="utf-8") as handle:
                json.dump({"id": "9.9", "summary": "Stale content"}, handle)

            section_splitter.split_markdown(
                markdown_path,
                temp_dir,
                invalidate_summaries=True,
            )
            self.assertFalse(os.path.exists(stale_summary))

            with open(stale_summary, "w", encoding="utf-8") as handle:
                json.dump({"id": "9.9", "summary": "Wrong section"}, handle)
            with self.assertRaises(ValueError):
                section_splitter.bind_summaries(temp_dir)

    def test_readability_ignores_math_and_never_blocks_on_sentence_length(self):
        formula = (
            r"\[ Q_{n+1} = Q_n + \alpha_n "
            r"\left(R_n - Q_n\right) + "
            r"\sum_{i=1}^{n}\frac{x_i^2}{1+\lambda_i} \]"
        )
        prose = lint_dense.extract_readable_prose(
            "The update is\n"
            + formula
            + "\n"
            + " ".join(["ordinary"] * 40)
            + "."
        )
        self.assertNotIn("Q_{n+1}", prose)

        report = lint_dense.Report()
        lint_dense.check_writing_style(prose, report)
        self.assertEqual(report.counts["FAIL"], 0)
        self.assertGreaterEqual(report.counts["WARN"], 1)

    def test_vocabulary_correction_requires_trigger_resolution_and_term(self):
        manifest = {
            "schema_version": 2,
            "concepts": [
                {
                    "id": "3.1",
                    "title": "Incremental Update",
                    "has_qna": True,
                }
            ],
            "qna_exchanges": [
                {
                    "id": "3.1.qna.1",
                    "concept": "3.1",
                    "salience": "essential",
                    "question_summary": "Is the reward difference an error?",
                    "answer_summary": "It changes the estimate toward a noisy reward.",
                    "anchor_quote": "call this difference an error",
                    "source_anchor": "00:42:10",
                }
            ],
            "teaching_moments": [
                {
                    "id": "3.1.moment.1",
                    "concept": "3.1",
                    "type": "vocabulary_correction",
                    "salience": "essential",
                    "trigger": "difference is an error",
                    "resolution": "reward is noisy and not a fixed target",
                    "preferred_term": "update term",
                    "anchor_quote": "call this difference an error",
                    "source_anchor": "00:42:10",
                }
            ],
            "lecture_flow": [
                {
                    "order": 1,
                    "concept": "3.1",
                    "type": "question",
                    "summary": "A student asks whether the difference is an error.",
                    "anchor_quote": "call this difference an error",
                    "keywords": ["difference", "error"],
                    "source_anchor": "00:42:10",
                },
                {
                    "order": 2,
                    "concept": "3.1",
                    "type": "correction",
                    "summary": "The answer prefers update term because the reward is noisy.",
                    "anchor_quote": "reward is noisy and not a fixed target",
                    "keywords": ["update", "noisy"],
                    "source_anchor": "00:42:25",
                },
            ],
        }
        complete_markdown = """# Incremental Updates

## 3.1 Incremental Update
### 3.1.1 Question and Explanation
> **Q:** Should we call this difference an error?
> **A:** It is better called an **update term**. The reward is noisy and not a fixed target.
"""
        incomplete_markdown = complete_markdown.replace("update term", "quantity")

        with tempfile.TemporaryDirectory() as temp_dir:
            manifest_path = os.path.join(temp_dir, "manifest.json")
            complete_path = os.path.join(temp_dir, "complete.md")
            incomplete_path = os.path.join(temp_dir, "incomplete.md")
            with open(manifest_path, "w", encoding="utf-8") as handle:
                json.dump(manifest, handle)
            with open(complete_path, "w", encoding="utf-8") as handle:
                handle.write(complete_markdown)
            with open(incomplete_path, "w", encoding="utf-8") as handle:
                handle.write(incomplete_markdown)

            complete_report = verify_manifest.verify_manifest(
                manifest_path,
                complete_path,
                phase="dense",
            )
            incomplete_report = verify_manifest.verify_manifest(
                manifest_path,
                incomplete_path,
                phase="dense",
            )

        self.assertFalse(complete_report.failures)
        self.assertTrue(
            any("Teaching moment" in name for name, _ in incomplete_report.failures)
        )

    def test_correction_evidence_must_share_one_qna_exchange(self):
        manifest = {
            "schema_version": 2,
            "concepts": [{"id": "3.1", "title": "Update"}],
            "teaching_moments": [{
                "id": "3.1.moment.1",
                "concept": "3.1",
                "type": "vocabulary_correction",
                "salience": "essential",
                "trigger": "difference is an error",
                "resolution": "reward is a noisy target",
                "preferred_term": "update term",
                "anchor_quote": "difference is an error",
                "source_anchor": "00:10",
            }],
            "lecture_flow": [{
                "order": 1,
                "concept": "3.1",
                "type": "question",
                "summary": "difference is an error",
                "anchor_quote": "difference is an error",
                "keywords": ["difference", "error"],
                "source_anchor": "00:10",
            }],
        }
        markdown = """# Update
## 3.1 Update
:::important-note
**Q:** Is the difference an error?
**A:** It is a useful question.
:::
:::important-note
**Q:** What term should we use?
**A:** Use update term because the reward is a noisy target.
:::
"""
        report = self._verify(manifest, markdown, phase="enriched")
        self.assertTrue(
            any("Teaching moment" in name for name, _ in report.failures)
        )

    def test_orphan_schema_v1_item_warns_and_invalid_schema_fails_cleanly(self):
        legacy_manifest = {
            "concepts": [{"id": "3.1", "title": "Update"}],
            "worked_examples": [{
                "concept": "9.9",
                "description": "legacy orphan",
            }],
        }
        markdown = "# Update\n## 3.1 Update\n### 3.1.1 Detail\nContent."
        legacy_report = self._verify(legacy_manifest, markdown)
        self.assertFalse(legacy_report.failures)
        self.assertTrue(legacy_report.warnings)

        invalid_report = self._verify(
            {"schema_version": "not-a-number"},
            markdown,
        )
        self.assertTrue(
            any(name == "Manifest schema" for name, _ in invalid_report.failures)
        )

    def test_lecture_flow_order_is_checked_within_a_concept(self):
        manifest = {
            "schema_version": 2,
            "concepts": [{"id": "3.1", "title": "Update"}],
            "lecture_flow": [
                {
                    "order": 1,
                    "concept": "3.1",
                    "type": "question",
                    "summary": "reward error question",
                    "anchor_quote": "reward error question",
                    "keywords": ["reward", "error", "question"],
                    "source_anchor": "00:10",
                },
                {
                    "order": 2,
                    "concept": "3.1",
                    "type": "correction",
                    "summary": "reward noisy correction",
                    "anchor_quote": "reward noisy correction",
                    "keywords": ["reward", "noisy", "correction"],
                    "source_anchor": "00:20",
                },
            ],
        }
        markdown = """# Update
## 3.1 Update
### 3.1.1 Explanation
The reward noisy correction appears here.
The reward error question appears later.
"""
        report = self._verify(manifest, markdown)
        self.assertTrue(
            any(name == "Lecture flow order (3.1)" for name, _ in report.failures)
        )

    def test_final_html_is_verified_against_teaching_moments(self):
        manifest = {
            "schema_version": 2,
            "concepts": [{"id": "3.1", "title": "Update", "has_qna": True}],
            "qna_exchanges": [{
                "id": "3.1.qna.1",
                "concept": "3.1",
                "salience": "essential",
                "question_summary": "difference is an error",
                "answer_summary": "update term because reward is a noisy target",
                "anchor_quote": "difference is an error",
                "source_anchor": "00:10",
            }],
            "teaching_moments": [{
                "id": "3.1.moment.1",
                "concept": "3.1",
                "type": "vocabulary_correction",
                "salience": "essential",
                "trigger": "difference is an error",
                "resolution": "reward is a noisy target",
                "preferred_term": "update term",
                "anchor_quote": "difference is an error",
                "source_anchor": "00:10",
            }],
            "lecture_flow": [{
                "order": 1,
                "concept": "3.1",
                "type": "correction",
                "summary": "difference error update term noisy target",
                "anchor_quote": "difference is an error",
                "keywords": ["error", "update", "noisy"],
                "source_anchor": "00:10",
            }],
        }
        html = """<html><body><main>
<h2 class="section-title">3.1 Update</h2>
<div class="important-note">
  <p><strong>Q:</strong> Is the difference an error?</p>
  <p><strong>A:</strong> Call it an update term because the reward is a noisy target.</p>
</div>
</main></body></html>"""
        report = self._verify(manifest, html, phase="html")
        self.assertFalse(report.failures)

        wrong_answer_report = self._verify(
            manifest,
            html.replace(
                "Call it an update term because the reward is a noisy target.",
                "Bananas are yellow.",
            ),
            phase="html",
        )
        self.assertTrue(
            any(name.startswith("Q&A") for name, _ in wrong_answer_report.failures)
        )

    def test_final_html_example_callout_and_math_delimiters(self):
        manifest = {
            "schema_version": 2,
            "concepts": [{
                "id": "3.1",
                "title": "Update",
                "has_worked_example": True,
            }],
            "worked_examples": [{
                "id": "3.1.example.1",
                "concept": "3.1",
                "salience": "essential",
                "description": "update estimate from two to three",
                "anchor_quote": "estimate moves from two to three",
                "source_anchor": "00:20",
            }],
            "lecture_flow": [{
                "order": 1,
                "concept": "3.1",
                "type": "example",
                "summary": "estimate moves from two to three",
                "anchor_quote": "estimate moves from two to three",
                "keywords": ["estimate", "two", "three"],
                "source_anchor": "00:20",
            }],
        }
        html = """<html><body><main>
<h2>3.1 Update</h2>
<div class="example-box"><p>The estimate moves from two to three.</p></div>
</main></body></html>"""
        self.assertFalse(self._verify(manifest, html, phase="html").failures)

        # Test double-backslash math delimiters
        report = html_lint.Report()
        html_lint.check_math(r"<main>\\(x + y\\)</main>", report)
        self.assertTrue(report.has_fail)

        # Test split delimiters
        report2 = html_lint.Report()
        html_lint.check_math("<p>\\[</p><p>x + y</p><p>\\]</p>", report2)
        self.assertTrue(report2.has_fail)

        # Test nested delimiters
        report3 = html_lint.Report()
        html_lint.check_math("<p>\\[\\[x + y\\]\\]</p>", report3)
        self.assertTrue(report3.has_fail)

        # Test raw less-than inside math block
        report4 = html_lint.Report()
        html_lint.check_math("<p>\\[y_{<t}\\]</p>", report4)
        self.assertTrue(report4.has_fail)

        # Test normal math block (no failure)
        report5 = html_lint.Report()
        html_lint.check_math("<p>\\[y_{&lt;t}\\]</p>", report5)
        self.assertFalse(report5.has_fail)

    def test_convert_md_to_html_formatting(self):
        from convert_md_to_html import convert_markdown_to_html
        md = ":::key-concept\n## 5.1 Concept Title\nMath: \\( y = x \\)\n- item 1\n:::\n"
        html = convert_markdown_to_html(md)
        self.assertIn('<div class="key-concept">', html)
        self.assertIn('<h2 id="5.1-concept-title">5.1 Concept Title</h2>', html)
        self.assertIn('<p>Math: \\( y = x \\)</p>', html)
        self.assertIn('<ul>', html)
        self.assertIn('<li>item 1</li>', html)

    def test_html_structure_lint_checks(self):
        # Nested callout box test
        p1 = html_lint.DocParser()
        p1.feed('<div class="key-concept"><div class="warning-box">Nested</div></div>')
        p1.close()
        report1 = html_lint.Report()
        html_lint.check_html_structure(p1, report1)
        self.assertTrue(report1.has_fail)

        # Unclosed tag test
        p2 = html_lint.DocParser()
        p2.feed('<div class="key-concept"><p>Text</div>')
        p2.close()
        report2 = html_lint.Report()
        html_lint.check_html_structure(p2, report2)
        self.assertTrue(report2.has_fail)

        # Balanced HTML test
        p3 = html_lint.DocParser()
        p3.feed('<div class="key-concept"><p>Text</p></div>')
        p3.close()
        report3 = html_lint.Report()
        html_lint.check_html_structure(p3, report3)
        self.assertFalse(report3.has_fail)


if __name__ == "__main__":
    unittest.main()

