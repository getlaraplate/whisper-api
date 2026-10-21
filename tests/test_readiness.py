"""Tests for the background loading of the model, /ready and the readiness gate.

    python -m unittest discover -s tests
"""

import threading
import unittest
from unittest import mock

from service_case import FakeWhisperModel, ServiceTestCase

KEY = "s3cret-key"


class ReadinessTest(ServiceTestCase):
    def loading_service(self, **env):
        """A service whose model is still loading; the model finishes at cleanup."""
        loaded = threading.Event()
        FakeWhisperModel.block_on = loaded
        module = self.load_service(wait_until_ready=False, **env)
        self.addCleanup(module.loader.join, 5)
        self.addCleanup(loaded.set)
        return module, loaded

    def test_health_answers_while_the_model_loads(self):
        module, _ = self.loading_service()

        self.assertEqual(module.app.test_client().get("/health").status_code, 200)

    def test_ready_answers_503_while_loading_then_200(self):
        module, loaded = self.loading_service()
        client = module.app.test_client()

        loading = client.get("/ready")
        loaded.set()
        module.loader.join(5)
        ready = client.get("/ready")

        self.assertEqual((loading.status_code, loading.get_json()), (503, {"status": "loading"}))
        self.assertEqual((ready.status_code, ready.get_json()), (200, {"status": "ready"}))

    def test_transcribe_answers_503_with_retry_after_while_loading(self):
        module, _ = self.loading_service()

        response = self.upload(module.app.test_client())

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"error": "loading model"})
        self.assertEqual(response.headers["Retry-After"], "10")

    def test_the_key_is_checked_before_readiness(self):
        module, _ = self.loading_service(WHISPER_API_KEY=KEY)
        client = module.app.test_client()

        self.assertEqual(self.upload(client).status_code, 401)
        self.assertEqual(self.upload(client, headers={"Authorization": f"Bearer {KEY}"}).status_code, 503)

    def test_ready_stays_open_when_a_key_is_configured(self):
        client = self.service(WHISPER_API_KEY=KEY)

        self.assertEqual(client.get("/ready").status_code, 200)

    def test_a_model_that_fails_to_load_ends_the_process_with_gunicorns_boot_error(self):
        FakeWhisperModel.fail_with = RuntimeError("no such model")

        with mock.patch("os._exit") as exit_process, self.assertLogs("whisper_api", "ERROR"):
            module = self.load_service(wait_until_ready=False)
            module.loader.join(5)

        exit_process.assert_called_once_with(3)
        self.assertEqual(module.app.test_client().get("/ready").status_code, 503)


if __name__ == "__main__":
    unittest.main()
