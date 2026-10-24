#!/usr/bin/env python3
"""Regression tests for the toolkit review findings (C1-C4, H1-H8).

Each test names the review item it protects. The suite intentionally imports
the real modules rather than reimplementing their logic, so future refactors
cannot silently reintroduce the original defects.
"""

import json
import os
import sys
import tempfile
import unittest

import convert_md_to_html
import lint as html_lint
import lint_dense
import section_splitter
import topic_mapping_utils as TMU
import update_topic_mapping
import verify_manifest

UTILS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "utils"))
if UTILS_DIR not in sys.path:
    sys.path.insert(0, UTILS_DIR)
import clean_vtt  # noqa: E402  (path set above)


def _verify(manifest, markdown, phase="dense"):
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


def _parser(raw_html):
    parser = html_lint.DocParser()
    parser.feed(raw_html)
    parser.close()
    return parser


class TempTopicMappingMixin:
    """Point the topic-map modules at a throwaway directory."""

    def setUp(self):
        super().setUp()
        self._temp_dir = tempfile.TemporaryDirectory()
        self.mappings_dir = self._temp_dir.name
        self._old = {
            "tmu_dir": TMU.TOPIC_MAPPINGS_DIR,
            "tmu_subjects": TMU.SUBJECTS_JSON,
            "update_dir": update_topic_mapping.TOPIC_MAPPINGS_DIR,
        }
        TMU.TOPIC_MAPPINGS_DIR = self.mappings_dir
        TMU.SUBJECTS_JSON = os.path.join(self.mappings_dir, "missing-subjects.json")
        update_topic_mapping.TOPIC_MAPPINGS_DIR = self.mappings_dir

    def tearDown(self):
        TMU.TOPIC_MAPPINGS_DIR = self._old["tmu_dir"]
        TMU.SUBJECTS_JSON = self._old["tmu_subjects"]
        update_topic_mapping.TOPIC_MAPPINGS_DIR = self._old["update_dir"]
        self._temp_dir.cleanup()
        super().tearDown()

    def write_map(self, filename, text):
        path = os.path.join(self.mappings_dir, filename)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path


class CriticalRegressionTests(TempTopicMappingMixin, unittest.TestCase):
    # -- C1 ---------------------------------------------------------------
    def test_flow_order_accepts_correctly_ordered_events_sharing_generic_words(self):
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
                    "source_anchor": "lines 2-4",
                },
                {
                    "order": 2,
                    "concept": "3.1",
                    "type": "correction",
                    "summary": "reward noisy correction",
                    "anchor_quote": "reward noisy correction",
                    "keywords": ["reward", "noisy", "correction"],
                    "source_anchor": "lines 5-7",
                },
            ],
        }
        markdown = """# Update
## 3.1 Update
### 3.1.1 Explanation
The reward error question appears first.
The reward noisy correction appears later.
"""
        report = _verify(manifest, markdown)
        self.assertFalse(
            [name for name, _ in report.failures if name.startswith("Lecture flow order")],
            report.failures,
        )

    def test_flow_order_without_line_ranges_warns_instead_of_failing(self):
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
        report = _verify(manifest, markdown)
        self.assertFalse(
            [name for name, _ in report.failures if name.startswith("Lecture flow order")]
        )
        self.assertTrue(
            [name for name, _ in report.warnings if name.startswith("Lecture flow order")]
        )

    # -- C2 ---------------------------------------------------------------
    def test_corrupt_topic_map_is_never_overwritten(self):
        corrupt = (
            'subject_name: "Test Subject"\n'
            "lectures: [\n"
            "  - lecture_number: \"1\"\n"
        )
        path = self.write_map("Test Subject.yaml", corrupt)

        with self.assertRaises(TMU.TopicMapParseError):
            update_topic_mapping.update_or_add_lecture(
                "Test Subject",
                "2",
                "Second Lecture",
                "outputs/Test Subject/Lecture_2/Lecture_2_notes/Lecture_2_notes.html",
                ["New Topic"],
            )

        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), corrupt)

    def test_topic_map_update_is_atomic_and_keeps_backup(self):
        original = (
            "subject_name: Test Subject\n"
            "lectures:\n"
            "  - lecture_number: \"1\"\n"
            "    topic: First Lecture\n"
            "    file_name: outputs/Test Subject/Lecture_1/Lecture_1_notes/Lecture_1_notes.html\n"
            "    topics_covered:\n"
            "      - Alpha Topic\n"
        )
        path = self.write_map("Test Subject.yaml", original)

        update_topic_mapping.update_or_add_lecture(
            "Test Subject",
            "2",
            "Second Lecture",
            "outputs/Test Subject/Lecture_2/Lecture_2_notes/Lecture_2_notes.html",
            ["Beta Topic"],
        )

        self.assertTrue(os.path.exists(path + ".bak"))
        with open(path + ".bak", "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), original)

        with open(path, "r", encoding="utf-8") as handle:
            updated = handle.read()
        self.assertIn("Alpha Topic", updated)
        self.assertIn("Beta Topic", updated)

    # -- C3 ---------------------------------------------------------------
    def test_splitter_ignores_h2_inside_fenced_code(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path = os.path.join(temp_dir, "notes.md")
            with open(markdown_path, "w", encoding="utf-8") as handle:
                handle.write(
                    "# Topic\n\n"
                    "## 3.1 Real Section\n"
                    "```python\n"
                    "## not a heading\n"
                    "```\n"
                    "Body text.\n"
                )

            inventory_path = section_splitter.split_markdown(
                markdown_path, temp_dir
            )
            with open(inventory_path, "r", encoding="utf-8") as handle:
                inventory = json.load(handle)

            numbered = [entry for entry in inventory if entry["num"] != 0]
            self.assertEqual(len(numbered), 1)
            self.assertEqual(numbered[0]["title"], "3.1 Real Section")
            self.assertFalse(
                os.path.exists(os.path.join(temp_dir, "section_02.md"))
            )

            with open(
                os.path.join(temp_dir, "section_01.md"), "r", encoding="utf-8"
            ) as handle:
                section = handle.read()
            self.assertIn("## not a heading", section)

    # -- C4 ---------------------------------------------------------------
    def test_vtt_note_blocks_are_skipped_and_short_spoken_lines_kept(self):
        vtt = (
            "WEBVTT\n\n"
            "NOTE This note continues\n"
            "onto a second line that must not leak\n\n"
            "1\n"
            "00:00:01.000 --> 00:00:04.000\n"
            "Okay.\n"
            "00:00:04.000 --> 00:00:06.000\n"
            "This is the real content.\n"
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            vtt_path = os.path.join(temp_dir, "lecture.vtt")
            txt_path = os.path.join(temp_dir, "lecture.txt")
            with open(vtt_path, "w", encoding="utf-8") as handle:
                handle.write(vtt)

            clean_vtt.clean_vtt(
                vtt_path,
                txt_path,
                merge_paragraphs=False,
            )

            with open(txt_path, "r", encoding="utf-8") as handle:
                output = handle.read()

        self.assertIn("Okay.", output)
        self.assertIn("This is the real content.", output)
        self.assertNotIn("continues", output)
        self.assertNotIn("second line", output)

    def test_vtt_keep_timestamps_preserves_source_anchors(self):
        vtt = (
            "WEBVTT\n\n"
            "00:00:07.000 --> 00:00:09.000\n"
            "Timestamped statement.\n"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            vtt_path = os.path.join(temp_dir, "lecture.vtt")
            with open(vtt_path, "w", encoding="utf-8") as handle:
                handle.write(vtt)

            output = clean_vtt.clean_vtt(
                vtt_path,
                merge_paragraphs=False,
                keep_timestamps=True,
            )

        self.assertIn("[00:00:07] Timestamped statement.", output)


class RenderingRegressionTests(unittest.TestCase):
    def test_table_pipes_inside_math_do_not_create_extra_cells(self):
        markdown = (
            "| Formula | Meaning |\n"
            "|---|---|\n"
            "| \\(P(A|B)\\) | conditional probability |\n"
        )
        html = convert_md_to_html.convert_markdown_to_html(markdown)
        self.assertIn("<td>\\(P(A|B)\\)</td>", html)
        self.assertEqual(html.count("<td>"), 2)
        self.assertEqual(html.count("<th>"), 2)

    def test_blockquote_qa_lines_are_separate_paragraphs(self):
        markdown = "> **Q:** Why?\n> **A:** Because.\n"
        html = convert_md_to_html.convert_markdown_to_html(markdown)
        self.assertIn("<p><strong>Q:</strong> Why?</p>", html)
        self.assertIn("<p><strong>A:</strong> Because.</p>", html)
        self.assertEqual(html.count("<p>"), 2)

    def test_dotted_headings_use_inventory_ids(self):
        self.assertEqual(
            convert_md_to_html.make_slug("5.1 Concept Title"), "5.1"
        )
        self.assertEqual(
            convert_md_to_html.make_slug("5.1.2 Detail Title"), "5.1.2"
        )

    def test_preamble_h1_is_dropped_when_converting_section_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(
                os.path.join(temp_dir, "section_00_preamble.md"),
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write("# Duplicate Title\n\nIntro text.")

            convert_md_to_html.convert_section_files(temp_dir)

            with open(
                os.path.join(temp_dir, "section_00_preamble.html"),
                "r",
                encoding="utf-8",
            ) as handle:
                html = handle.read()

        self.assertNotIn("<h1", html)
        self.assertIn("Intro text.", html)

    def test_ampersands_and_balanced_parenthesis_links(self):
        html = convert_md_to_html.process_inline("Tom & Jerry")
        self.assertEqual(html, "Tom &amp; Jerry")

        link_html = convert_md_to_html.process_inline(
            "[docs](https://example.com/a_(b))"
        )
        self.assertIn('href="https://example.com/a_(b)"', link_html)

        escaped = convert_md_to_html.process_inline(r"literal \*stars\*")
        self.assertIn("*stars*", escaped)
        self.assertNotIn("<em>", escaped)


class HtmlLintRegressionTests(unittest.TestCase):
    # -- H3 ---------------------------------------------------------------
    def test_feature_extractor_and_marl_agent_labels_are_not_jargon(self):
        raw = (
            "<html><head><title>Feature Extractor</title></head><body><main>"
            "<h2>Feature Extractor</h2>"
            "<p>Agent 1 selects an action in the environment.</p>"
            "</main></body></html>"
        )
        report = html_lint.Report()
        html_lint.check_pipeline_jargon(raw, _parser(raw), report)
        self.assertFalse(report.has_fail, report.rows)

    def test_pipeline_agent_output_is_still_jargon(self):
        raw = (
            "<html><head><title>Lecture</title></head><body><main>"
            "<p>Agent 1 output is the enriched draft.</p>"
            "</main></body></html>"
        )
        report = html_lint.Report()
        html_lint.check_pipeline_jargon(raw, _parser(raw), report)
        self.assertTrue(report.has_fail)

    def test_marl_agent_output_and_feature_extractor_output_are_not_jargon(self):
        raw = (
            "<html><head><title>Multi-Agent Reinforcement Learning</title></head><body><main>"
            "<p>Agent 1 output is fed into Agent 2 before the joint action is scored.</p>"
            "<p>The feature extractor output maps raw pixels into a compact vector.</p>"
            "</main></body></html>"
        )
        report = html_lint.Report()
        html_lint.check_pipeline_jargon(raw, _parser(raw), report)
        self.assertFalse(report.has_fail, report.rows)

    def test_bare_pipeline_role_label_is_still_jargon(self):
        raw = (
            "<html><head><title>Lecture</title></head><body><main>"
            "<p>Extractor phase output is ready for the next step.</p>"
            "</main></body></html>"
        )
        report = html_lint.Report()
        html_lint.check_pipeline_jargon(raw, _parser(raw), report)
        self.assertTrue(report.has_fail)

    def test_placeholder_token_is_technical_vocabulary(self):
        raw = "<main><p>The placeholder token is masked before attention.</p></main>"
        report = html_lint.Report()
        html_lint.check_placeholder_tasks(raw, _parser(raw), report)
        self.assertFalse(report.has_fail, report.rows)

    def test_placeholder_text_is_still_flagged(self):
        raw = "<main><p>Placeholder text goes here.</p></main>"
        report = html_lint.Report()
        html_lint.check_placeholder_tasks(raw, _parser(raw), report)
        self.assertTrue(report.has_fail)

    def test_placeholder_value_is_technical_vocabulary(self):
        raw = "<main><p>Each field has a placeholder value before the parser runs.</p></main>"
        report = html_lint.Report()
        html_lint.check_placeholder_tasks(raw, _parser(raw), report)
        self.assertFalse(report.has_fail, report.rows)

    def test_secret_inside_code_block_is_a_teaching_example(self):
        raw = (
            "<main><p>Never commit credentials.</p>"
            '<pre><code>api_key = "sk-abcdefghijklmnopqrstuvwx"</code></pre>'
            "</main>"
        )
        report = html_lint.Report()
        html_lint.check_pii_and_secrets(_parser(raw), report)
        self.assertFalse(report.has_fail, report.rows)

    def test_secret_in_visible_prose_still_fails(self):
        raw = '<main><p>api_key = "sk-abcdefghijklmnopqrstuvwx"</p></main>'
        report = html_lint.Report()
        html_lint.check_pii_and_secrets(_parser(raw), report)
        self.assertTrue(report.has_fail)

    def test_named_professor_fails_but_university_is_a_warning(self):
        professor_raw = "<main><p>Professor Bishop explained the bias term.</p></main>"
        professor_report = html_lint.Report()
        html_lint.check_pii_and_secrets(_parser(professor_raw), professor_report)
        self.assertTrue(professor_report.has_fail)

        university_raw = "<main><p>See the University of Oxford notes.</p></main>"
        university_report = html_lint.Report()
        html_lint.check_pii_and_secrets(
            _parser(university_raw), university_report
        )
        self.assertFalse(university_report.has_fail)
        self.assertGreaterEqual(university_report.counts["WARN"], 1)

    # -- H4 ---------------------------------------------------------------
    def test_unknown_metadata_keys_fail(self):
        raw = (
            '<script type="application/json" id="lecture-metadata">'
            + json.dumps({
                "title": "Title",
                "subject": "Subject",
                "gradeLevel": "Postgraduate",
                "datePublished": "2024-01-01",
                "targetAudience": "Students",
                "sections": [{"id": "1.1"}],
                "quiz": [],
            })
            + "</script>"
        )
        report = html_lint.Report()
        html_lint.check_metadata(raw, _parser(raw), report)
        self.assertTrue(report.has_fail)
        self.assertIn("quiz", "\n".join(message for _, _, message in report.rows))

    def test_placeholder_inside_metadata_json_is_detected(self):
        raw = (
            '<script type="application/json" id="lecture-metadata">'
            '{"title": "{{LECTURE_TITLE}}", "sections": []}'
            "</script>"
        )
        report = html_lint.Report()
        html_lint.check_template_hygiene(raw, report)
        self.assertTrue(report.has_fail)

    def _seo_raw(self, description):
        return (
            "<html><head>"
            f'<meta name="description" content="{description}">'
            '<meta property="og:title" content="T">'
            '<meta property="og:description" content="D">'
            '<meta property="og:type" content="article">'
            '<meta property="og:url" content="https://example.com">'
            '<meta property="og:site_name" content="Site">'
            '<meta name="twitter:card" content="summary">'
            '<meta name="twitter:title" content="T">'
            '<meta name="twitter:description" content="D">'
            '<meta name="keywords" content="k">'
            '<meta name="robots" content="index, follow">'
            '<meta name="author" content="Author">'
            '<link rel="canonical" href="https://example.com">'
            '<script type="application/ld+json">{"@type": "Article"}</script>'
            "</head></html>"
        )

    def test_description_length_is_a_hard_contract(self):
        for length, should_fail in ((120, False), (99, True), (156, True)):
            report = html_lint.Report()
            html_lint.check_seo(_parser(self._seo_raw("x" * length)), report)
            self.assertEqual(report.has_fail, should_fail, (length, report.rows))

    # -- Medium: SVG IDs --------------------------------------------------
    def test_duplicate_svg_ids_warn(self):
        raw = (
            '<svg><defs><marker id="arrow"></marker></defs></svg>'
            '<svg><defs><marker id="arrow"></marker></defs></svg>'
        )
        report = html_lint.Report()
        html_lint.check_svg_ids(raw, report)
        self.assertEqual(report.counts["WARN"], 1)

    def test_verify_marker_reason_is_not_hand_waving(self):
        raw = (
            "<main><p>Result "
            "*[verify: reconstructed, after simplification]*"
            " follows.</p></main>"
        )
        report = html_lint.Report()
        html_lint.check_handwaving(_parser(raw), report)
        self.assertEqual(report.counts["FAIL"], 0)


class DenseLintRegressionTests(unittest.TestCase):
    # -- H3 ---------------------------------------------------------------
    def test_currency_dollar_is_not_a_math_delimiter(self):
        report = lint_dense.Report()
        lint_dense.check_math_delimiters(
            "The cost is $5 today and $10 tomorrow.", report
        )
        self.assertFalse(report.has_fail, report.rows)

    def test_math_dollar_is_still_flagged(self):
        report = lint_dense.Report()
        lint_dense.check_math_delimiters("Inline $x + y$ math.", report)
        self.assertTrue(report.has_fail)

    # -- H8 ---------------------------------------------------------------
    def test_glossary_and_further_reading_are_valid_appendices(self):
        text = (
            "## 5.1 First Topic\n"
            "### 5.1.1 Detail\n"
            "Body.\n\n"
            "## Glossary\n"
            "- term: definition\n\n"
            "## Further Reading\n"
            "- a book\n"
        )
        report = lint_dense.Report()
        lint_dense.check_numbering(text, 5, report)
        self.assertFalse(report.has_fail, report.rows)

    def test_numbering_ignores_headings_inside_fenced_code(self):
        text = (
            "## 5.1 First Topic\n"
            "### 5.1.1 Detail\n"
            "```python\n"
            "## not a heading\n"
            "### also not a heading\n"
            "```\n"
            "Body.\n"
        )
        report = lint_dense.Report()
        lint_dense.check_numbering(text, 5, report)
        self.assertFalse(report.has_fail, report.rows)

    def test_explicit_appendix_marker_exempts_custom_heading(self):
        text = (
            "## 5.1 First Topic\n"
            "Body.\n\n"
            "<!-- appendix -->\n"
            "## Custom Extra Material\n"
            "- note\n"
        )
        report = lint_dense.Report()
        lint_dense.check_numbering(text, 5, report)
        self.assertFalse(report.has_fail, report.rows)

    def test_resolved_verify_marker_reason_does_not_fail_hand_waving(self):
        report = lint_dense.Report()
        lint_dense.check_writing_style(
            "The result *[verify: reconstructed, after simplification]* holds.",
            report,
        )
        self.assertFalse(report.has_fail, report.rows)


class ProvenanceAndPrerequisiteRegressionTests(
    TempTopicMappingMixin, unittest.TestCase
):
    # -- H6 ---------------------------------------------------------------
    def test_prerequisite_selection_filters_admin_topics_and_caps_results(self):
        current = ["3.1 Gradient Descent Optimization"]
        previous = [{
            "lecture_number": "1",
            "topic": "Foundations",
            "topics_covered": [
                "Course Logistics",
                "Gradient Descent Alpha",
                "Gradient Descent Beta",
                "Gradient Descent Gamma",
                "Gradient Descent Delta",
            ],
        }]

        selected = TMU._select_prerequisites(current, previous, limit=2)
        labels = [topic for _, topic, _, _, _ in selected]

        self.assertEqual(len(labels), 2)
        self.assertFalse(
            [label for label in labels if "Logistics" in label], labels
        )

    def test_prerequisite_html_links_previous_lecture_notes(self):
        self.write_map(
            "Test Subject.yaml",
            (
                "subject_name: Test Subject\n"
                "lectures:\n"
                "  - lecture_number: \"1\"\n"
                "    topic: Foundations\n"
                "    file_name: outputs/Test Subject/Lecture_1/Lecture_1_notes/Lecture_1_notes.html\n"
                "    topics_covered:\n"
                "      - Gradient Descent\n"
                "      - Course Logistics\n"
                "  - lecture_number: \"2\"\n"
                "    topic: Optimization\n"
                "    file_name: outputs/Test Subject/Lecture_2/Lecture_2_notes/Lecture_2_notes.html\n"
                "    topics_covered:\n"
                "      - Gradient Descent Optimization\n"
            ),
        )

        html = TMU.generate_prerequisite_html("Test Subject", 2)

        self.assertIn('class="prerequisite-section"', html)
        self.assertIn("Gradient Descent", html)
        self.assertNotIn("Course Logistics", html)
        self.assertIn("Lecture_1_notes/Lecture_1_notes.html", html)
        self.assertIn("<a href=", html)

    def test_prerequisite_href_strips_local_outputs_prefix(self):
        href = TMU._prerequisite_href(
            "outputs/Machine Learning/Lecture_3/Lecture_3_notes/Lecture_3_notes.html"
        )
        self.assertEqual(
            href,
            "Machine%20Learning/Lecture_3/Lecture_3_notes/Lecture_3_notes.html",
        )

    # -- H7 ---------------------------------------------------------------
    def test_acronyms_are_extracted_as_keywords(self):
        keywords = verify_manifest.extract_keywords("MARL update with PPO")
        self.assertIn("marl", keywords)
        self.assertIn("ppo", keywords)
        self.assertIn("update", keywords)

    def test_keyword_matching_respects_token_boundaries(self):
        self.assertFalse(
            verify_manifest.keyword_match(
                "act", "The practice session was long.", threshold=1
            )
        )

    def test_anchor_quote_is_primary_evidence(self):
        manifest = {
            "schema_version": 2,
            "concepts": [{"id": "3.1", "title": "Update"}],
            "lecture_flow": [{
                "order": 1,
                "concept": "3.1",
                "type": "question",
                "summary": "unrelated wording that does not appear",
                "anchor_quote": "call the difference an update term",
                "keywords": ["difference", "update"],
                "source_anchor": "lines 4-6",
            }],
        }
        markdown = """# Update
## 3.1 Update
### 3.1.1 Explanation
We call the difference an update term here.
"""
        report = _verify(manifest, markdown)
        self.assertFalse(report.failures)


if __name__ == "__main__":
    unittest.main()
