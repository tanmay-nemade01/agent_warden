#!/usr/bin/env python3
"""Isolated integration tests for topic mapping utilities."""

import os
import sys
import tempfile
import unittest

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import topic_mapping_utils as TMU
import update_topic_mapping as UTM


class TopicMappingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.old_tmu_dir = TMU.TOPIC_MAPPINGS_DIR
        self.old_utm_dir = UTM.TOPIC_MAPPINGS_DIR
        TMU.TOPIC_MAPPINGS_DIR = self.temp_dir.name
        UTM.TOPIC_MAPPINGS_DIR = self.temp_dir.name

    def tearDown(self):
        TMU.TOPIC_MAPPINGS_DIR = self.old_tmu_dir
        UTM.TOPIC_MAPPINGS_DIR = self.old_utm_dir
        self.temp_dir.cleanup()

    def test_update_replaces_stale_topics_without_touching_workspace(self):
        path, added, updated = UTM.update_or_add_lecture(
            "Deep Reinforcement Learning",
            "3",
            "Bandit Methods",
            "Deep Reinforcement Learning/DRL_Lecture_3_notes.html",
            ["3.1 Bandits", "3.2 Sample Average"],
        )
        self.assertTrue(added)
        self.assertFalse(updated)
        self.assertTrue(path.startswith(self.temp_dir.name))

        _, added, updated = UTM.update_or_add_lecture(
            "Deep Reinforcement Learning",
            "3",
            "Bandit Methods",
            "Deep Reinforcement Learning/DRL_Lecture_3_notes.html",
            ["3.1 Bandits", "3.2 Incremental Update"],
        )
        self.assertFalse(added)
        self.assertTrue(updated)

        topic_map = TMU.load_topic_map("DRL")
        self.assertEqual(
            topic_map["lectures"][0]["topics_covered"],
            ["3.1 Bandits", "3.2 Incremental Update"],
        )

    def test_empty_topic_update_is_rejected(self):
        with self.assertRaises(ValueError):
            UTM.update_or_add_lecture(
                "Deep Reinforcement Learning",
                "3",
                "Bandit Methods",
                "DRL/notes.html",
                [],
            )

    def test_acronym_subject_filter_uses_same_matcher_as_loader(self):
        UTM.update_or_add_lecture(
            "Deep Reinforcement Learning",
            "2",
            "Bandits",
            "DRL/lecture-2.html",
            ["2.1 Search Algorithms"],
        )
        results = TMU.find_coverage_by_topics(
            ["Search Algorithms"],
            subject_name="DRL",
            min_word_overlap=2,
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["subject"], "Deep Reinforcement Learning")

        excluded = TMU.find_coverage_by_topics(
            ["Search Algorithms"],
            subject_name="DRL",
            exclude_lecture=("DRL", "2"),
            min_word_overlap=2,
        )
        self.assertEqual(excluded, [])


if __name__ == "__main__":
    unittest.main()