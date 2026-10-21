---
status: closed
created_on: 2026-10-20
---
# Phase 1 runtime hardening. Implementation Plan

> **For agentic workers:** execute task by task. Steps use checkbox (`- [ ]`) syntax; tick each step when it is done, in the same work block. A step deliberately not done is `- [-]` with its reason on the same line.

**Status:** Closed (2026-10-21): delivered on `master`, see "Delivery status" at the end.

**Goal:** the service runs on the latest faster-whisper, starts answering at once and says when it is ready, refuses uploads it cannot take and media longer than allowed, never leaks an exception, carries a request id, declares its version and HTTP contract, and reports the revision of its model, with a way to pin it.

**Architecture:** all in `whisper-api.py` plus `gunicorn.conf.py`, `openapi.json` and `VERSION` at the repository root. The model is resolved with `faster_whisper.utils.download_model` (which returns the snapshot path, hence the revision) and loaded from that path by a loader thread; a `before_request` chain runs the request id, auth, the readiness gate and the in-flight limit; PyAV, already installed by faster-whisper, reads the media duration before transcription.

**Tech Stack:** Python 3.11+, Flask 3.1, gunicorn 26.2 (`gthread`), faster-whisper 1.2.x, PyAV (`av`, a faster-whisper dependency). No new direct dependency.

**Spec:** `../specs/2026-10-20-production-readiness-design.md`, section 5: R2, R3, R4, R7, R10 (Phase 1 of section 8).

## Global Constraints

- Apache-2.0 repository. `requirements.txt` changes only the faster-whisper line.
- The HTTP contract the Laraplate client relies on stays: `POST /transcribe` with a multipart `file`, the answer `{"text", "language", "duration"}`, plus the new `400`, `413` and `503` answers and the new fields of `/health`.
- Tests are `unittest` with fakes of `faster_whisper` and `av`; no model download and no real media decoding in the unit suite. `python -m unittest discover -s tests` passes before every commit, with `-W error::ResourceWarning`.
- No transcript, file name or media content in any log line or error answer.
- Conventional commits, trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, identity `swolley <myentity@inwind.it>`. Never push without the user's OK; never touch the stack's submodule pointers.

## Decisions taken in this plan

- **P1. A model that fails to load ends the process** with exit code 3, gunicorn's worker boot error, so gunicorn stops and systemd (or the container runtime) restarts it. The spec's "`/ready` reports the error" is dropped: a process that is exiting cannot answer.
- **P2. `/ready` is open like `/health`.** `{"status": "loading"}` with `503`, or `{"status": "ready"}` with `200`.
- **P3. Readiness gate.** While loading, `/transcribe` answers `503` `{"error": "loading model"}` with `Retry-After: 10`, after the auth check and before the body is read.
- **P4. In-flight limit.** `WHISPER_MAX_IN_FLIGHT` (default 4) bounds the uploads being served or waiting for the model; one more answers `503` `{"error": "busy"}` with `Retry-After: 30`, at once and before the body is read. With `WHISPER_NUM_WORKERS=1` that is one transcription and three waiting. gunicorn moves to 8 threads and `worker_connections = 32`.
- **P5. Maximum duration.** `WHISPER_MAX_DURATION_SECONDS` (default 10800, three hours). The duration is read from the container header with PyAV (`av.open(path).duration`, in microseconds) after the upload is saved and before transcription. Over the limit: `413` `{"error": "media too long (max N s)"}`. A container that declares no duration is accepted and logged; the upload limit still applies.
- **P6. Unreadable media.** A file PyAV cannot open (`av.error.FFmpegError` and subclasses) answers `400` `{"error": "unreadable media"}`, not `500`.
- **P7. Request id** as in the sibling service: an incoming `X-Request-Id` matching `^[A-Za-z0-9._-]{1,128}$` is kept, otherwise `uuid4().hex`; every answer carries it; gunicorn's access log prints it.
- **P8. `500` answers** are `{"error": "internal error", "request_id": "<id>"}`; the exception goes to the log with the request id.
- **P9. Version.** A `VERSION` file holds the release (`0.1.0`); `/health` adds `version` and `api_version: 1`.
- **P10. OpenAPI.** `openapi.json` (OpenAPI 3.1), hand-written, with request and response schemas and an example each. Contract tests as in the sibling service, without a validator library.
- **P11. Revision and pinning.** `WHISPER_MODEL_REVISION` (empty by default, D4) is passed to `download_model`. The snapshot path it returns ends in the commit; the service logs it and `/health` reports `model_revision` (`null` for a local model directory). Provisioning ahead and `HF_HUB_OFFLINE=1` are documented.

## Review Focus

- The in-flight slot is taken before `request.files` is touched and released on every path, including `413`, `400` and a client that disconnects mid-upload (P4).
- The saved upload is deleted on every path, including the `413` of P5 and the `400` of P6.
- faster-whisper 1.2: `WhisperModel` and `transcribe` keep the arguments the service passes (`device`, `compute_type`, `num_workers`, `language`); the fake must not hide a renamed argument.
- No file name in logs, including the PyAV error message, which may quote the path.

---

### Task 1: faster-whisper 1.2 and model resolution (R10)

**Files:** modify `requirements.txt`, `whisper-api.py`, `tests/test_whisper_api.py`.

- [x] **Verify** in a scratch virtualenv with `faster-whisper==1.2.*`: the signatures of `WhisperModel.__init__`, `WhisperModel.transcribe` and `faster_whisper.utils.download_model`, and that `download_model("tiny")` returns a path ending in `snapshots/<commit>`. Record any difference in this plan before going on.
- [x] **Failing tests.** The fake `faster_whisper` gains `utils.download_model`, recording its arguments and returning `<tmp>/snapshots/abc123`. The service loads `WhisperModel` from that path; `WHISPER_MODEL_REVISION=abc` reaches `download_model` as `revision`; `/health` reports `model_revision: "abc123"`; a `WHISPER_MODEL` that is an existing directory skips `download_model` and reports `null`.
- [x] **Implement** P11; `requirements.txt`: `faster-whisper==1.2.*`.
- [x] **Run** the suite; commit `feat: move to faster-whisper 1.2 and report the model revision`.

> **Verified (2026-10-21):** faster-whisper 1.2.1 (ctranslate2 4.8.3, PyAV 19.0.2): `WhisperModel.__init__` keeps `device`, `compute_type`, `num_workers` (and adds `revision`); `transcribe` keeps `language`; `download_model(size_or_id, output_dir, local_files_only, cache_dir, revision, use_auth_token)` returns `.../snapshots/<commit>`. A real run under gunicorn with `tiny` and `HF_HUB_OFFLINE=1` reports the commit in `/health`.

### Task 2: Background loading and `/ready` (R2)

**Files:** modify `whisper-api.py`, `tests/test_whisper_api.py`.

- [x] **Failing tests.** `/health` answers while the model loads (a fake whose constructor blocks on an event); `/ready` `503` then `200`; `/transcribe` `503` with `Retry-After` while loading, `401` without a key when one is set; a model whose constructor raises makes the loader call the injected exit function with code 3.
- [x] **Implement** P1 to P3: loader thread started at import, readiness flag, `/ready`, gate after auth. A test helper waits for the loader so the existing tests keep a ready service.
- [x] **Run** the suite; commit `feat: load the model in the background and add /ready`.

### Task 3: In-flight limit and maximum duration (R3)

**Files:** modify `whisper-api.py`, `gunicorn.conf.py`, `whisper.service`, `tests/test_whisper_api.py`.

- [x] **Failing tests.** With `WHISPER_MAX_IN_FLIGHT=1` and a fake transcribe blocked on an event, a second upload answers `503` `{"error": "busy"}` with `Retry-After`, and its body is not parsed (the fake model sees one call); the slot comes back after a `500`. With a fake `av` reporting a duration over `WHISPER_MAX_DURATION_SECONDS`: `413`, the model not called, the temporary file gone. A fake `av.open` raising the fake `FFmpegError` with the path in its message: `400` `{"error": "unreadable media"}`, the path absent from the answer and from the log. A container without duration: transcribed.
- [x] **Implement** P4 to P6. `gunicorn.conf.py`: `threads = 8`, `worker_connections = 32`.
- [x] **Measure memory.** In a scratch virtualenv, transcribe a short sample with `base` and `small` on CPU and read the resident memory; write the figures in the README and set `MemoryMax` in `whisper.service` at about 1.5 times the `base` figure, with a comment on how to raise it for a larger model.
- [x] **Run** the suite; commit `feat: bound uploads in flight and media duration`.

> **Finding (2026-10-21):** the real run found that faster-whisper 1.2.1 passes `metadata_errors` to `av.open()`, which PyAV 19 (released 2026-09-29) no longer accepts: every transcription failed with `500`. Fixed on faster-whisper master (SYSTRAN/faster-whisper#1589), not released; `requirements.txt` pins `av<19` (18.1.0) until it is, as D4 prescribes. With it: unreadable file `400`, over the duration `413`, a 120 s sample transcribed; peak 0.56 GB with `base`, 1.36 GB with `small`. `MemoryMax=1G`.

### Task 4: Request id and generic errors (R4)

**Files:** modify `whisper-api.py`, `gunicorn.conf.py`, `tests/test_whisper_api.py`.

- [x] **Failing tests.** Valid `X-Request-Id` echoed, invalid replaced, header on every answer including `401`, `413` and `503`. A fake transcribe raising `RuntimeError("secret")`: `500` `{"error": "internal error", "request_id": ...}` without `secret`; the log record holds the request id.
- [x] **Implement** P7 and P8; gunicorn `access_log_format` with `%({x-request-id}o)s`.
- [x] **Run** the suite; commit `feat: add request ids and stop leaking exceptions`.

### Task 5: Version and OpenAPI document (R7)

**Files:** create `VERSION`, `openapi.json`, `tests/test_contract.py`; modify `whisper-api.py`.

- [x] **Failing tests.** `/health` carries `version` from `VERSION` and `api_version` `1`; the routes of the app and the paths of `openapi.json` match; the answers of the test client in the success and documented error cases carry the required fields, with the documented types, of their schema.
- [x] **Implement** P9 and P10: `/health`, `/ready`, `/transcribe` (multipart `file`, `language` in query or form), the Bearer scheme, the error answers (`400`, `401`, `413`, `500`, `503`) and `X-Request-Id`.
- [x] **Run** the suite; commit `feat: declare the version and the HTTP contract`.

### Task 6: Documentation and closing

**Files:** modify `README.md`, `../specs/2026-10-20-production-readiness-design.md`, this plan.

- [x] **Docs.** README: `/ready` and the healthcheck; `WHISPER_MAX_IN_FLIGHT`, `WHISPER_MAX_DURATION_SECONDS`, the `503` and `413` answers; `X-Request-Id`; `version`, `api_version`, `openapi.json`; `WHISPER_MODEL_REVISION`, provisioning ahead and `HF_HUB_OFFLINE=1`; the memory figures and `MemoryMax`; the Concurrency section with the in-flight limit. Production readiness section: the gaps closed by this plan leave the list; the status stays `experimental`.
- [x] **Spec.** Status line points to this plan and records Phase 1 delivered; amend R2 with P1.
- [x] **Close.** Delivery status with divergences, ticks, `status: closed`; commit `docs: close the phase 1 plan`.

## Delivery status (2026-10-21): shipped

All six tasks are built on `master` (the repository's default branch was renamed from `main` meanwhile),
commits `42dc03a` to the closing commit. The service runs on faster-whisper 1.2.1, resolves the model through
`download_model` and reports its revision in `/health` (`WHISPER_MODEL_REVISION` pins one); loads it in a
background thread (`/ready`, `503` with `Retry-After` on `/transcribe` while loading, exit code 3 when it fails
to load); refuses uploads beyond `WHISPER_MAX_IN_FLIGHT` with `503 busy` before reading them; reads the
declared duration with PyAV and answers `413` beyond `WHISPER_MAX_DURATION_SECONDS` and `400` for unreadable
media; carries `X-Request-Id`; answers unexpected errors with a generic `500`; declares `version` and
`api_version` with an `openapi.json` checked by the tests. 47 unit tests pass; real runs under gunicorn with
`tiny`, `base` and `small` confirmed the revision offline, the `400` and `413` paths and the memory figures.

Divergences from the plan text:

- **PyAV pinned below 19 (Task 3).** The real run found that faster-whisper 1.2.1 passes `metadata_errors`
  to `av.open()`, which PyAV 19 removed: every transcription failed. The fix is on faster-whisper master
  (SYSTRAN/faster-whisper#1589) but not released, so `requirements.txt` pins `av<19` with the reason, as D4
  prescribes. The Task 1 verification had only checked signatures, not a transcription.
- **P1 against the spec (Task 2).** As planned, `/ready` does not report a load error: the process exits.
  R2 of the spec is amended accordingly.
- **`MemoryMax=1G` (Task 3)** fits `base` (peak 0.56 GB); the README and the unit say to raise it to `2G` for
  `small` (peak 1.36 GB). Figures from a synthetic sample, stated as an order of magnitude.
- **Request id test (Task 4).** The "invalid id" case uses a tab instead of a newline, which the test client
  refuses in a header value.
- **`contract.py`** is a copy of the one in `sentence-transformers-api`; its docstring says to keep the two in
  step.
- **README "Wire into Laraplate"** no longer names `AI_MEDIA_TRANSCRIPTION_MODEL`, which Laraplate does not
  read any more; it points to the Laraplate guide.

**Documented in:** `README.md` (Production readiness, Endpoints, Security, Concurrency, Memory, Model updates,
Install, Config) and `openapi.json`.
