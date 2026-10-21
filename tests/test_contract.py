"""Tests that the service and openapi.json describe the same HTTP contract, and that /health declares it.

    python -m unittest discover -s tests
"""

import threading
import unittest
from pathlib import Path

from contract import DOCUMENT, problems, response_schema
from service_case import FakeMedia, FakeWhisperModel, ServiceTestCase

KEY = "s3cret-key"
VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()


class VersionTest(ServiceTestCase):
    def test_health_declares_the_release_and_the_api_version(self):
        health = self.service().get("/health").get_json()

        self.assertEqual((health["version"], health["api_version"]), (VERSION, 1))

    def test_the_document_carries_the_same_versions(self):
        self.assertEqual((DOCUMENT["info"]["version"], DOCUMENT["info"]["x-api-version"]), (VERSION, 1))


class ContractTest(ServiceTestCase):
    def assertMatches(self, path, method, response):
        schema = response_schema(path, method, response.status_code)
        self.assertEqual(problems(schema, response.get_json()), [], f"{method.upper()} {path} {response.status_code}")
        self.assertIn("X-Request-Id", response.headers)

    def test_the_routes_and_the_documented_paths_match(self):
        module = self.load_service()
        routes = {
            (rule.rule, method.lower())
            for rule in module.app.url_map.iter_rules()
            if rule.endpoint != "static"
            for method in rule.methods - {"HEAD", "OPTIONS"}
        }
        documented = {(path, method) for path, operations in DOCUMENT["paths"].items() for method in operations}

        self.assertEqual(routes, documented)

    def test_every_response_example_matches_its_schema(self):
        for path, operations in DOCUMENT["paths"].items():
            for method, operation in operations.items():
                for status, answer in operation["responses"].items():
                    with self.subTest(f"{method} {path} {status}"):
                        content = answer["content"]["application/json"]
                        self.assertEqual(problems(content["schema"], content["example"]), [])

    def test_the_multipart_field_is_file(self):
        body = DOCUMENT["paths"]["/transcribe"]["post"]["requestBody"]["content"]["multipart/form-data"]["schema"]

        self.assertEqual(body["required"], ["file"])

    def test_the_answers_match_the_document(self):
        client = self.service(WHISPER_API_KEY=KEY, WHISPER_MAX_UPLOAD_MB="1")
        auth = {"Authorization": f"Bearer {KEY}"}

        self.assertMatches("/health", "get", client.get("/health"))
        self.assertMatches("/ready", "get", client.get("/ready"))
        self.assertMatches("/transcribe", "post", self.upload(client, headers=auth))
        self.assertMatches("/transcribe", "post", self.upload(client, headers=auth, query="?language=italian"))
        self.assertMatches("/transcribe", "post", self.upload(client))
        self.assertMatches("/transcribe", "post", self.upload(client, content=b"a" * (1024 * 1024 + 1), headers=auth))
        FakeMedia.unreadable = True
        self.assertMatches("/transcribe", "post", self.upload(client, headers=auth))
        FakeMedia.unreadable = False
        FakeWhisperModel.fails_with = RuntimeError("decoder crashed")
        with self.assertLogs("whisper_api", "ERROR"):
            self.assertMatches("/transcribe", "post", self.upload(client, headers=auth))

    def test_the_503_answers_match_the_document(self):
        loaded = threading.Event()
        FakeWhisperModel.block_on = loaded
        module = self.load_service(wait_until_ready=False)
        self.addCleanup(module.loader.join, 5)
        self.addCleanup(loaded.set)
        client = module.app.test_client()

        self.assertMatches("/ready", "get", client.get("/ready"))
        self.assertMatches("/transcribe", "post", self.upload(client))


if __name__ == "__main__":
    unittest.main()
