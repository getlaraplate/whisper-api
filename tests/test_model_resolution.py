"""Tests for resolving the model through the Hugging Face cache, its reported revision and pinning one.

    python -m unittest discover -s tests
"""

import tempfile
import unittest

from service_case import ServiceTestCase


class ModelResolutionTest(ServiceTestCase):
    def test_the_model_loads_from_the_snapshot_download_model_returns(self):
        self.service(WHISPER_MODEL="base")

        self.assertEqual(self.downloads.calls, [{"model": "base", "revision": None}])
        self.assertEqual(self.model().name, "/cache/hub/models--Systran--faster-whisper-base/snapshots/abc123")

    def test_health_reports_the_revision_of_the_snapshot(self):
        health = self.service().get("/health").get_json()

        self.assertEqual(health["model_revision"], "abc123")
        self.assertEqual(health["model"], "base")

    def test_a_pinned_revision_reaches_the_download(self):
        client = self.service(WHISPER_MODEL_REVISION="def456")

        self.assertEqual(self.downloads.calls, [{"model": "base", "revision": "def456"}])
        self.assertEqual(client.get("/health").get_json()["model_revision"], "def456")

    def test_a_local_model_directory_is_loaded_as_it_is_without_a_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            client = self.service(WHISPER_MODEL=directory)

            self.assertEqual(self.downloads.calls, [])
            self.assertEqual(self.model().name, directory)
            self.assertIsNone(client.get("/health").get_json()["model_revision"])

    def test_a_snapshot_path_without_the_cache_layout_has_no_revision(self):
        self.downloads.answer = "/models/whisper-base"

        client = self.service()

        self.assertIsNone(client.get("/health").get_json()["model_revision"])


if __name__ == "__main__":
    unittest.main()
