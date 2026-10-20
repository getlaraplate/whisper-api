"""Tests for POST /transcribe and GET /health: Bearer key, upload limit, input checks, response shape.

faster-whisper is replaced by a small fake: the tests are about what the service asks of the model and
what it answers, not about a real checkpoint, and they run without downloading one.

    python -m unittest discover -s tests
"""

import importlib.util
import io
import os
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

from werkzeug.datastructures import FileStorage
from werkzeug.test import encode_multipart

SERVICE = Path(__file__).resolve().parent.parent / "whisper-api.py"
KEY = "s3cret-key"


class FakeWhisperModel:
    instances = []

    def __init__(self, model_size_or_path, **kwargs):
        self.name = model_size_or_path
        self.options = kwargs
        self.calls = []
        type(self).instances.append(self)

    def transcribe(self, audio, language=None, **kwargs):
        with open(audio, "rb") as handle:
            content = handle.read()
        self.calls.append({"path": audio, "language": language, "content": content})
        segments = iter([types.SimpleNamespace(text=" Ciao "), types.SimpleNamespace(text="mondo. ")])
        return segments, types.SimpleNamespace(language=language or "it", duration=1.5)


class WhisperApiTest(unittest.TestCase):
    def setUp(self):
        FakeWhisperModel.instances = []

        faster_whisper = types.ModuleType("faster_whisper")
        faster_whisper.WhisperModel = FakeWhisperModel
        patcher = mock.patch.dict(sys.modules, {"faster_whisper": faster_whisper})
        patcher.start()
        self.addCleanup(patcher.stop)

    def service(self, **env):
        """A fresh copy of the service, configured from the given environment only."""
        environ = {key: value for key, value in os.environ.items() if not key.startswith("WHISPER_")}
        environ.update(env)

        spec = importlib.util.spec_from_file_location("whisper_api_under_test", SERVICE)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, environ, clear=True):
            spec.loader.exec_module(module)

        return module.app.test_client()

    def upload(self, client, content=b"audio bytes", filename="sample.mp3", headers=None, query=""):
        # Encoded in memory: the test client would spool a large body to a file it never closes.
        boundary, body = encode_multipart({"file": FileStorage(io.BytesIO(content), filename)})
        return client.post(
            "/transcribe" + query,
            data=body,
            content_type=f"multipart/form-data; boundary={boundary}",
            headers=headers or {},
        )

    def model(self):
        return FakeWhisperModel.instances[0]

    def test_transcribes_the_upload_into_the_shape_the_laraplate_client_reads(self):
        client = self.service()

        response = self.upload(client)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"text": "Ciao mondo.", "language": "it", "duration": 1.5})
        self.assertEqual(self.model().calls[0]["content"], b"audio bytes")

    def test_loads_the_configured_model_once_at_startup(self):
        self.service(WHISPER_MODEL="small", WHISPER_DEVICE="cuda", WHISPER_COMPUTE_TYPE="float16", WHISPER_NUM_WORKERS="2")

        self.assertEqual(len(FakeWhisperModel.instances), 1)
        self.assertEqual(self.model().name, "small")
        self.assertEqual(self.model().options, {"device": "cuda", "compute_type": "float16", "num_workers": 2})

    def test_runs_one_transcription_at_a_time_by_default(self):
        self.service()

        self.assertEqual(self.model().options["num_workers"], 1)

    def test_without_a_key_configured_transcribe_is_open(self):
        client = self.service()

        self.assertEqual(self.upload(client).status_code, 200)

    def test_with_a_key_configured_a_missing_or_wrong_key_answers_401(self):
        client = self.service(WHISPER_API_KEY=KEY)
        headers = {
            "no header": {},
            "wrong key": {"Authorization": f"Bearer {KEY}x"},
            "basic scheme": {"Authorization": f"Basic {KEY}"},
            "bare key": {"Authorization": KEY},
            "empty bearer": {"Authorization": "Bearer "},
        }

        for label, header in headers.items():
            with self.subTest(label):
                response = self.upload(client, headers=header)

                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.get_json(), {"error": "unauthorized"})
                self.assertEqual(response.headers["WWW-Authenticate"], "Bearer")

        self.assertEqual(self.model().calls, [])

    def test_the_bearer_header_the_laraplate_client_sends_is_accepted(self):
        client = self.service(WHISPER_API_KEY=KEY)

        response = self.upload(client, headers={"Authorization": f"Bearer {KEY}"})

        self.assertEqual(response.status_code, 200)

    def test_several_comma_separated_keys_are_each_accepted(self):
        client = self.service(WHISPER_API_KEY=f"old-key, {KEY}")

        for key in ("old-key", KEY):
            with self.subTest(key):
                self.assertEqual(self.upload(client, headers={"Authorization": f"Bearer {key}"}).status_code, 200)

    def test_health_stays_open_when_a_key_is_configured(self):
        client = self.service(WHISPER_API_KEY=KEY, WHISPER_MODEL="small")

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"status": "healthy", "model": "small", "device": "cpu"})

    def test_an_upload_larger_than_the_limit_answers_413_in_json(self):
        client = self.service(WHISPER_MAX_UPLOAD_MB="1")

        response = self.upload(client, content=b"a" * (1024 * 1024 + 1))

        self.assertEqual(response.status_code, 413)
        self.assertIn("too large", response.get_json()["error"])
        self.assertEqual(self.model().calls, [])

    def test_an_upload_within_the_limit_is_transcribed(self):
        client = self.service(WHISPER_MAX_UPLOAD_MB="1")

        self.assertEqual(self.upload(client, content=b"a" * (1024 * 1024 - 1024)).status_code, 200)

    def test_an_unauthorized_oversized_upload_answers_401_before_the_body_is_read(self):
        client = self.service(WHISPER_API_KEY=KEY, WHISPER_MAX_UPLOAD_MB="1")

        response = self.upload(client, content=b"a" * (2 * 1024 * 1024))

        self.assertEqual(response.status_code, 401)

    def test_a_request_without_a_file_answers_400(self):
        client = self.service()

        response = client.post("/transcribe", data={}, content_type="multipart/form-data")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json(), {"error": "missing file"})

    def test_passes_the_language_from_the_query_or_the_form(self):
        client = self.service()

        self.upload(client, query="?language=en")
        client.post(
            "/transcribe",
            data={"file": (io.BytesIO(b"x"), "a.mp3"), "language": "de"},
            content_type="multipart/form-data",
        )
        self.upload(client)

        self.assertEqual([call["language"] for call in self.model().calls], ["en", "de", None])

    def test_rejects_a_language_that_is_not_an_iso_code(self):
        client = self.service()

        for language in ("italian", "IT", "e1", "../x"):
            with self.subTest(language):
                self.assertEqual(self.upload(client, query=f"?language={language}").status_code, 400)

        self.assertEqual(self.model().calls, [])

    def test_keeps_a_plain_extension_for_the_temporary_file_and_drops_any_other(self):
        client = self.service()

        self.upload(client, filename="clip.MP4")
        self.upload(client, filename="clip.m p4")
        self.upload(client, filename="noextension")

        self.assertEqual([Path(call["path"]).suffix for call in self.model().calls], [".MP4", ".bin", ".bin"])


if __name__ == "__main__":
    unittest.main()
