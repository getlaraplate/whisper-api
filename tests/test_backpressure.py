"""Tests for the in-flight limit, the maximum media duration and unreadable media.

    python -m unittest discover -s tests
"""

import os
import threading
import unittest

from service_case import FakeMedia, FakeWhisperModel, ServiceTestCase


class InFlightLimitTest(ServiceTestCase):
    def occupy_the_only_slot(self, client):
        """Start an upload that holds the single slot until the returned event is set."""
        FakeWhisperModel.transcribing = threading.Event()
        FakeWhisperModel.release = threading.Event()
        slow = threading.Thread(target=self.upload, args=(client,))
        slow.start()
        self.addCleanup(slow.join, 5)
        self.addCleanup(FakeWhisperModel.release.set)
        FakeWhisperModel.transcribing.wait(5)
        return FakeWhisperModel.release, slow

    def test_an_upload_beyond_the_limit_answers_503_at_once_without_reading_it(self):
        client = self.service(WHISPER_MAX_IN_FLIGHT="1")
        self.occupy_the_only_slot(client)

        response = self.upload(client, content=b"second")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {"error": "busy"})
        self.assertEqual(response.headers["Retry-After"], "30")
        self.assertEqual(len(FakeMedia.opened), 1)

    def test_the_slot_comes_back_when_the_upload_ends(self):
        client = self.service(WHISPER_MAX_IN_FLIGHT="1")
        release, slow = self.occupy_the_only_slot(client)

        release.set()
        slow.join(5)
        FakeWhisperModel.release = None

        self.assertEqual(self.upload(client).status_code, 200)

    def test_the_slot_comes_back_after_a_failure(self):
        client = self.service(WHISPER_MAX_IN_FLIGHT="1")
        FakeWhisperModel.fails_with = RuntimeError("decoder crashed")

        with self.assertLogs("whisper_api", "ERROR"):
            self.assertEqual(self.upload(client).status_code, 500)
        FakeWhisperModel.fails_with = None

        self.assertEqual(self.upload(client).status_code, 200)

    def test_health_and_ready_are_not_counted(self):
        client = self.service(WHISPER_MAX_IN_FLIGHT="1")
        self.occupy_the_only_slot(client)

        for path in ("/health", "/ready"):
            with self.subTest(path):
                self.assertEqual(client.get(path).status_code, 200)

    def test_the_limit_defaults_to_four(self):
        self.assertEqual(self.load_service().MAX_IN_FLIGHT, 4)


class DurationTest(ServiceTestCase):
    def test_media_longer_than_the_limit_answers_413_and_is_not_transcribed(self):
        client = self.service(WHISPER_MAX_DURATION_SECONDS="60")
        FakeMedia.duration = 61_000_000

        response = self.upload(client)

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.get_json(), {"error": "media too long (max 60 s)"})
        self.assertEqual(self.model().calls, [])
        self.assertFalse(os.path.exists(FakeMedia.opened[0]))

    def test_media_within_the_limit_is_transcribed(self):
        client = self.service(WHISPER_MAX_DURATION_SECONDS="60")
        FakeMedia.duration = 59_000_000

        self.assertEqual(self.upload(client).status_code, 200)

    def test_media_without_a_declared_duration_is_transcribed(self):
        client = self.service(WHISPER_MAX_DURATION_SECONDS="60")
        FakeMedia.duration = None

        self.assertEqual(self.upload(client).status_code, 200)

    def test_the_limit_defaults_to_three_hours(self):
        self.assertEqual(self.load_service().MAX_DURATION_SECONDS, 10800)

    def test_unreadable_media_answers_400_without_the_path(self):
        client = self.service()
        FakeMedia.unreadable = True

        with self.assertLogs("whisper_api", "WARNING") as logs:
            response = self.upload(client, filename="secret-interview.mp3")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "unreadable media"})
        self.assertNotIn("tmp", "\n".join(logs.output))
        self.assertNotIn("secret-interview", "\n".join(logs.output))
        self.assertEqual(self.model().calls, [])


if __name__ == "__main__":
    unittest.main()
