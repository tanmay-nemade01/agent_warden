#!/usr/bin/env python3
"""Regression tests for strict assembly, fidelity, and math-safe readability."""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "utils")))
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
                    "source_anchor": "lines 10-12",
                },
                {
                    "order": 2,
                    "concept": "3.1",
                    "type": "correction",
                    "summary": "reward noisy correction",
                    "anchor_quote": "reward noisy correction",
                    "keywords": ["reward", "noisy", "correction"],
                    "source_anchor": "lines 13-15",
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
        self.assertIn('<h2 id="5.1" class="section-title">5.1 Concept Title</h2>', html)
        self.assertIn('<p>Math: \\( y = x \\)</p>', html)
        self.assertIn('<ul>', html)
        self.assertIn('<li>item 1</li>', html)

        # Test H1 conversion
        h1_md = "# Preamble Lecture Overview\n\nIntro text."
        h1_html = convert_markdown_to_html(h1_md)
        self.assertIn('<h1 id="preamble-lecture-overview" class="lecture-title">Preamble Lecture Overview</h1>', h1_html)
        self.assertNotIn('<p>#', h1_html)

        # Test raw figure and SVG preservation without <p> wrapper or mangled attributes
        svg_md = '<figure class="visual-intuition">\n  <svg viewBox="0 0 500 220" id="plot_axis_1">\n    <circle cx="50" cy="50" r="10" stroke_width="2" />\n  </svg>\n</figure>'
        svg_html = convert_markdown_to_html(svg_md)
        self.assertIn('<figure class="visual-intuition">', svg_html)
        self.assertIn('<svg viewBox="0 0 500 220" id="plot_axis_1">', svg_html)
        self.assertIn('<circle cx="50" cy="50" r="10" stroke_width="2" />', svg_html)
        self.assertNotIn('<p><figure', svg_html)
        self.assertNotIn('<em>axis</em>', svg_html)  # Underscores must not become <em>

        # Test display math block preservation
        math_md = "\\[\nf(x) = \\sum_{i=1}^n x_i\n\\]"
        math_html = convert_markdown_to_html(math_md)
        self.assertIn('<p>\\[ f(x) = \\sum_{i=1}^n x_i \\]</p>', math_html)

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


    def test_prerequisite_html_generation(self):
        from topic_mapping_utils import generate_prerequisite_html
        # Test with nonexistent subject returns empty string
        empty = generate_prerequisite_html("NonexistentSubject12345", 2)
        self.assertEqual(empty, "")

        # Test with Lecture 1 (no previous lectures) returns empty string
        empty_l1 = generate_prerequisite_html("Machine Learning", 1)
        self.assertEqual(empty_l1, "")

    def test_section_splitter_fenced_code(self):
        """C3: Fenced code blocks containing '## comment' must not split into sections."""
        with tempfile.TemporaryDirectory() as temp_dir:
            md_path = os.path.join(temp_dir, "code_sample.md")
            out_dir = os.path.join(temp_dir, "sections")
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Lecture 1: Python Code\n\n"
                    "## 1.1 First Section\n"
                    "Intro text.\n\n"
                    "```python\n"
                    "## not a heading\n"
                    "x = 10\n"
                    "```\n\n"
                    "~~~bash\n"
                    "## also not a heading\n"
                    "echo 'hello'\n"
                    "~~~\n\n"
                    "## 1.2 Second Section\n"
                    "Conclusion.\n"
                )
            inv_path = section_splitter.split_markdown(md_path, out_dir)
            with open(inv_path, "r", encoding="utf-8") as f:
                inv = json.load(f)
            titles = [entry["title"] for entry in inv]
            self.assertIn("(Preamble)", titles)
            self.assertIn("1.1 First Section", titles)
            self.assertIn("1.2 Second Section", titles)
            self.assertNotIn("not a heading", titles)
            self.assertNotIn("also not a heading", titles)
            self.assertEqual(len(inv), 3)

    def test_update_topic_mapping_corrupt_yaml(self):
        """C2: update_topic_mapping refuses to overwrite corrupt existing YAML and keeps .bak."""
        import topic_mapping_utils as TMU
        import update_topic_mapping as UTM
        with tempfile.TemporaryDirectory() as temp_dir:
            orig_dir = TMU.TOPIC_MAPPINGS_DIR
            TMU.TOPIC_MAPPINGS_DIR = temp_dir
            UTM.TOPIC_MAPPINGS_DIR = temp_dir
            try:
                corrupt_path = os.path.join(temp_dir, "Test Subject.yaml")
                with open(corrupt_path, "w", encoding="utf-8") as f:
                    f.write("subject_name: Test Subject\nlectures: [corrupt: {unclosed\n")

                with self.assertRaises((RuntimeError, ValueError)):
                    UTM.update_or_add_lecture(
                        "Test Subject",
                        "1",
                        "Overview",
                        "Test/L1.html",
                        ["Topic A"],
                    )

                # Valid write creates backup and atomic update
                with open(corrupt_path, "w", encoding="utf-8") as f:
                    f.write("subject_name: Test Subject\nlectures:\n  - lecture_number: '1'\n    topic: Intro\n    topics_covered: ['Old']\n")

                path, added, updated = UTM.update_or_add_lecture(
                    "Test Subject",
                    "2",
                    "Advanced",
                    "Test/L2.html",
                    ["Topic B"],
                )
                self.assertTrue(os.path.exists(path + ".bak"))
                tmap = TMU.load_topic_map("Test Subject")
                self.assertEqual(len(tmap["lectures"]), 2)
            finally:
                TMU.TOPIC_MAPPINGS_DIR = orig_dir
                UTM.TOPIC_MAPPINGS_DIR = orig_dir

    def test_clean_vtt_notes_and_cue_id(self):
        """C4: clean_vtt skips multi-line NOTE blocks and preserves short spoken lines."""
        from clean_vtt import clean_vtt
        with tempfile.TemporaryDirectory() as temp_dir:
            vtt_path = os.path.join(temp_dir, "sample.vtt")
            txt_path = os.path.join(temp_dir, "sample.txt")
            with open(vtt_path, "w", encoding="utf-8") as f:
                f.write(
                    "WEBVTT\n\n"
                    "NOTE\n"
                    "This is an internal note line 1\n"
                    "This is note line 2\n\n"
                    "00:00:01.000 --> 00:00:03.000\n"
                    "Okay.\n\n"
                    "00:00:03.000 --> 00:00:06.000\n"
                    "This is the real spoken lecture content.\n"
                )
            clean_vtt(vtt_path, output_path=txt_path, merge_paragraphs=False)
            with open(txt_path, "r", encoding="utf-8") as f:
                result = f.read()

            self.assertNotIn("internal note line 1", result)
            self.assertNotIn("note line 2", result)
            self.assertIn("Okay.", result)
            self.assertIn("This is the real spoken lecture content.", result)

    def test_table_cells_containing_pipes_in_math(self):
        """H2: Markdown table parsing preserves unescaped pipes in math and escaped pipes."""
        from convert_md_to_html import convert_markdown_to_html
        md = (
            "| Event | Probability \\(P(A|B)\\) | Note \\| Detail |\n"
            "|---|---|---|\n"
            "| Bayes | \\( P(A|B) = \\frac{P(B|A)P(A)}{P(B)} \\) | Conditional \\| joint |\n"
        )
        html = convert_markdown_to_html(md)
        self.assertIn("<table>", html)
        self.assertIn("<th>Event</th>", html)
        self.assertIn("Probability \\(P(A|B)\\)", html)
        self.assertEqual(html.count("<th>"), 3)
        self.assertEqual(html.count("<td>"), 3)

    def test_blockquote_qa_splitting(self):
        """H2: Blockquote Q&A dialogue turns are rendered as separate paragraphs."""
        from convert_md_to_html import convert_markdown_to_html
        md = (
            "> **Q:** Why does gradient descent diverge?\n"
            "> **A:** Because the learning rate is too high.\n"
        )
        html = convert_markdown_to_html(md)
        self.assertIn("<blockquote>", html)
        self.assertIn("<p><strong>Q:</strong> Why does gradient descent diverge?</p>", html)
        self.assertIn("<p><strong>A:</strong> Because the learning rate is too high.</p>", html)

    def test_lint_academic_false_positives(self):
        """H3: Academic vocabulary (Feature Extractor, MARL Agent 1, placeholder token, etc.) passes."""
        p1 = html_lint.DocParser()
        html1 = (
            "<!DOCTYPE html><html><head><title>Convolutional Networks</title></head><body>"
            "<h2 class=\"section-title\">2.3 Feature Extractor Architecture</h2>"
            "<p>In multi-agent reinforcement learning, Agent 1 and Agent 2 play a zero-sum game.</p>"
            "<p>In NLP, BERT uses the placeholder token [MASK] during pretraining.</p>"
            "<pre><code>api_key = \"sk-1234567890abcdef1234567890\"</code></pre>"
            "</body></html>"
        )
        p1.feed(html1)
        p1.close()
        report = html_lint.Report()
        html_lint.check_pipeline_jargon(html1, p1, report)
        html_lint.check_placeholder_tasks(html1, p1, report)
        html_lint.check_pii_and_secrets(p1, report)
        self.assertFalse(report.has_fail)

        report_dense = lint_dense.Report()
        lint_dense.check_math_delimiters("The cloud instance costs $5 per hour or $10.50 on peak.", report_dense)
        self.assertFalse(report_dense.has_fail)

    def test_metadata_extra_keys_and_hygiene(self):
        """H4: Reject extra metadata keys and catch unreplaced template tokens in script tags."""
        report1 = html_lint.Report()
        html_with_token = (
            "<!DOCTYPE html><html><body>"
            "<script id=\"lecture-metadata\" type=\"application/json\">"
            "{\"title\": \"{{LECTURE_TITLE}}\"}"
            "</script></body></html>"
        )
        html_lint.check_template_hygiene(html_with_token, report1)
        self.assertTrue(report1.has_fail)

        report2 = html_lint.Report()
        p2 = html_lint.DocParser()
        p2.has_metadata_script = True
        html_with_extra = (
            "<script id=\"lecture-metadata\" type=\"application/json\">"
            "{"
            "  \"title\": \"Search\","
            "  \"subject\": \"AI\","
            "  \"gradeLevel\": \"UG\","
            "  \"datePublished\": \"2026-01-01\","
            "  \"targetAudience\": \"Students\","
            "  \"sections\": [{\"num\": 1, \"title\": \"Intro\"}],"
            "  \"quiz\": [\"q1\", \"q2\"]"
            "}"
            "</script>"
        )
        html_lint.check_metadata(html_with_extra, p2, report2)
        self.assertTrue(report2.has_fail)
        self.assertIn("unknown metadata field", report2.rows[0][2])

    def test_seo_description_boundaries(self):
        """H4: SEO description must be 100-155 characters (no warnings for good length)."""
        p_good = html_lint.DocParser()
        p_good.description_content = "A" * 125
        report_good = html_lint.Report()
        html_lint.check_seo(p_good, report_good)
        desc_issues = [msg for _, name, msg in report_good.rows if "Description" in msg and "chars" in msg]
        self.assertEqual(len(desc_issues), 0)

        p_short = html_lint.DocParser()
        p_short.description_content = "Too short description"
        report_short = html_lint.Report()
        html_lint.check_seo(p_short, report_short)
        self.assertTrue(report_short.has_fail)

        p_long = html_lint.DocParser()
        p_long.description_content = "A" * 160
        report_long = html_lint.Report()
        html_lint.check_seo(p_long, report_long)
        self.assertTrue(report_long.has_fail)

    def test_flow_order_verification_non_unique_keywords(self):
        """C1: Teaching events sharing generic keywords in narrative order must pass."""
        manifest = {
            "schema_version": 2,
            "sections": [{"id": "1.1", "title": "1.1 Value Iteration"}],
            "teaching_moments": [],
            "qna_exchanges": [],
            "named_references": [],
            "lecture_flow": [
                {
                    "order": 1,
                    "concept": "1.1",
                    "summary": "State reward function update",
                    "keywords": ["reward", "update"],
                    "source_anchor": "lines 10-20",
                },
                {
                    "order": 2,
                    "concept": "1.1",
                    "summary": "Policy model reward update",
                    "keywords": ["reward", "update"],
                    "source_anchor": "lines 25-35",
                },
            ],
        }
        markdown = (
            "# Lecture 1\n\n"
            "## 1.1 Value Iteration\n\n"
            "First we perform the state reward function update on values.\n\n"
            "Later in the algorithm we perform the policy model reward update.\n"
        )
        report = self._verify(manifest, markdown, phase="enriched")
        self.assertEqual(len(report.failures), 0)

    def test_lint_dense_appendix_and_verify_markers(self):
        """H8: Unnumbered appendix headings pass, and verify markers do not trigger hand-waving."""
        report1 = lint_dense.Report()
        md_text = (
            "## 1.1 First Topic\n\nContent.\n\n"
            "## 1.2 Second Topic\n\nContent.\n\n"
            "## Glossary\n\nDefinitions.\n\n"
            "## Further Reading\n\nArticles.\n"
        )
        lint_dense.check_numbering(md_text, 1, report1)
        self.assertFalse(report1.has_fail)

        report2 = lint_dense.Report()
        md_with_marker = (
            "Here we expand the algebraic equation *[verify: check step after simplification]* for the gradient."
        )
        lint_dense.check_writing_style(md_with_marker, report2)
        failures = [msg for lvl, name, msg in report2.rows if lvl == "FAIL"]
        self.assertEqual(len(failures), 0)


if __name__ == "__main__":
    unittest.main()

