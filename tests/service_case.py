"""Shared fakes and base test case: a fresh copy of the service under a given environment.

faster-whisper is replaced by small fakes: the tests are about what the service asks of the model and what
it answers, not about a real checkpoint, and they run without downloading one.
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
SNAPSHOT_COMMIT = "abc123"


class FakeWhisperModel:
    instances = []
    # When set, the constructor waits on this event (a model still loading) or raises this exception.
    block_on = None
    fail_with = None

    def __init__(self, model_size_or_path, **kwargs):
        if type(self).block_on is not None:
            type(self).block_on.wait(5)
        if type(self).fail_with is not None:
            raise type(self).fail_with
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


class FakeDownloads:
    """Stands for faster_whisper.utils.download_model: records the calls, answers a snapshot path."""

    def __init__(self, root):
        self.root = root
        self.calls = []
        # When set, returned instead of a path in the cache layout.
        self.answer = None

    def __call__(self, size_or_id, revision=None, **kwargs):
        self.calls.append({"model": size_or_id, "revision": revision})
        if self.answer is not None:
            return self.answer
        return str(self.root / f"models--Systran--faster-whisper-{size_or_id}" / "snapshots" / (revision or SNAPSHOT_COMMIT))


class ServiceTestCase(unittest.TestCase):
    def setUp(self):
        FakeWhisperModel.instances = []
        FakeWhisperModel.block_on = None
        FakeWhisperModel.fail_with = None
        self.downloads = FakeDownloads(Path("/cache/hub"))

        faster_whisper = types.ModuleType("faster_whisper")
        faster_whisper.WhisperModel = FakeWhisperModel
        utils = types.ModuleType("faster_whisper.utils")
        utils.download_model = self.downloads
        faster_whisper.utils = utils
        patcher = mock.patch.dict(sys.modules, {"faster_whisper": faster_whisper, "faster_whisper.utils": utils})
        patcher.start()
        self.addCleanup(patcher.stop)

    def service(self, wait_until_ready=True, **env):
        """A fresh copy of the service, configured from the given environment only.

        The model loads in a background thread; unless told otherwise, wait until it is done.
        """
        return self.load_service(wait_until_ready, **env).app.test_client()

    def load_service(self, wait_until_ready=True, **env):
        """The module of a fresh copy of the service, for tests that need its internals."""
        environ = {key: value for key, value in os.environ.items() if not key.startswith("WHISPER_")}
        environ.update(env)

        spec = importlib.util.spec_from_file_location("whisper_api_under_test", SERVICE)
        module = importlib.util.module_from_spec(spec)
        with mock.patch.dict(os.environ, environ, clear=True):
            spec.loader.exec_module(module)

        if wait_until_ready:
            module.loader.join(5)

        return module

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
