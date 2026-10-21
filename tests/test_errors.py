"""Tests for X-Request-Id and for the generic answer of unexpected errors.

    python -m unittest discover -s tests
"""

import re
import unittest

from service_case import FakeWhisperModel, ServiceTestCase

KEY = "s3cret-key"
GENERATED_ID = re.compile(r"^[0-9a-f]{32}$")


class RequestIdTest(ServiceTestCase):
    def test_a_valid_incoming_id_is_echoed(self):
        client = self.service()

        response = self.upload(client, headers={"X-Request-Id": "laraplate.media-7_A"})

        self.assertEqual(response.headers["X-Request-Id"], "laraplate.media-7_A")

    def test_an_invalid_incoming_id_is_replaced(self):
        client = self.service()

        for label, value in {"too long": "a" * 129, "a space": "a b", "a tab": "a\tb", "a slash": "a/b"}.items():
            with self.subTest(label):
                response = client.get("/health", headers={"X-Request-Id": value})

                self.assertRegex(response.headers["X-Request-Id"], GENERATED_ID)

    def test_every_answer_carries_an_id(self):
        client = self.service(WHISPER_API_KEY=KEY, WHISPER_MAX_UPLOAD_MB="1")
        auth = {"Authorization": f"Bearer {KEY}"}
        answers = {
            "200": client.get("/ready"),
            "400": client.post("/transcribe", data={}, content_type="multipart/form-data", headers=auth),
            "401": self.upload(client),
            "404": client.get("/nowhere"),
            "413": self.upload(client, content=b"a" * (1024 * 1024 + 1), headers=auth),
        }

        for label, response in answers.items():
            with self.subTest(label):
                self.assertEqual(str(response.status_code), label)
                self.assertRegex(response.headers["X-Request-Id"], GENERATED_ID)


class GenericErrorTest(ServiceTestCase):
    def test_an_unexpected_error_answers_a_generic_500_and_logs_the_exception(self):
        client = self.service()
        FakeWhisperModel.fails_with = RuntimeError("secret decoder state")

        with self.assertLogs("whisper_api", "ERROR") as logs:
            response = self.upload(client, headers={"X-Request-Id": "req-9"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "internal error", "request_id": "req-9"})
        self.assertNotIn("secret", response.get_data(as_text=True))
        self.assertIn("req-9", logs.output[0])


if __name__ == "__main__":
    unittest.main()
