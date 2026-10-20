# whisper-api: production readiness

**Status:** Draft, 2026-10-20. No plan. Phase 0 delivered on 2026-10-20 (0b3c11a).
**Builds on:** the hardening of 2026-10-20 (5f5738d).
**Siblings:** the same work for [`sentence-transformers-api`](https://github.com/getlaraplate/sentence-transformers-api/blob/main/docs/superpowers/specs/2026-10-20-production-readiness-design.md), and the Laraplate client side in [`laraplate`](https://github.com/getlaraplate/laraplate/blob/master/docs/superpowers/specs/2026-10-20-self-hosted-ai-services-client-design.md). The three specs share the decision and requirement numbers (D, R, E), so a number means the same thing in each.
**History:** split on 2026-10-20 from one spec at the root of the Laraplate workspace, so that each repository owns the design of its own work.

## 1. Goal

This service is part of the Laraplate offer as **free, self-hosted
infrastructure that the customer installs**. Either it is fit for production on
the customer's servers, or it says plainly that it is not. Nobody should run it
in production believing it is ready when it is not.

## 2. Decisions

| # | Decision | Source |
|---|---|---|
| D1 | The customer installs and runs the service. Laraplate does not host it for customers. Multi-tenancy, quotas and billing are out of scope. | Founder, 2026-10-20 |
| D2 | The service stays free (Apache-2.0) and is not sold. Whether the repository stays public or becomes private is decided separately and does not change this spec. | Founder, 2026-10-20 |
| D3 | CPU is the baseline. GPU is supported where it can be built and tested; where it cannot, the docs say so. | Founder, 2026-10-20 |
| D4 | Models follow the latest release available on Hugging Face; no revision is pinned by default. A model whose new release breaks compatibility gets pinned, together with provisioning the weights ahead of time and running offline (R10). Python dependencies follow the same rule: the newest release that passes the tests. | Founder, 2026-10-20 |
| D5 | The honesty requirement comes first: the readiness label (section 4) ships before any other work in this spec. | This spec |

## 3. State on 2026-10-20

Done by the hardening: Bearer-key check in constant time with several keys,
upload limit (`WHISPER_MAX_UPLOAD_MB`), language validation,
`WHISPER_NUM_WORKERS` and a documented concurrency model, gunicorn (one process,
`gthread`) instead of Flask's development server, a systemd unit running as the
unprivileged `whisper-api` user, unit tests with a fake model. The model is fixed
on the host: a request cannot name one.

Still missing for production:

1. **Shared environment.** The documented install shares one virtualenv (`/opt/ai-env`) with `sentence-transformers-api`: CTranslate2 on one side, torch on the other. Upgrading one can break the other.
2. **No packaging.** No container image, no tagged release, no changelog.
3. **No readiness.** The model loads when the module is imported, so a process that is still loading looks alive to a load balancer or a container healthcheck.
4. **No backpressure.** When every thread is busy, uploads wait without limit. Audio of any length is accepted.
5. **No observability.** No metrics, no request id, logs are gunicorn's access log only.
6. **No CI.** The tests exist; nothing runs them on push.
7. **No contract.** No machine-readable API description, no version in `/health`.
8. **Synchronous transcription.** One file at a time by default, against a client timeout of 120 s. Long media fail.
9. **Old library.** `faster-whisper` is held at `1.0.*`; the latest release is 1.2.1 (2026-10-20).

## 4. Readiness label

The README starts with a **Production readiness** section that states:

- **Status:** `experimental` or `production`.
- What is tested and on which hardware (CPU model and RAM, GPU model and CUDA version when supported).
- What is known not to work or not to be ready, listed in the README itself, and a link to this spec.
- The licence of the default model (R9).

Rules:

- Status is `experimental` until every requirement of section 5 is done.
- It becomes `production` in the same change that closes the last requirement of section 5, and only then.
- GPU support has its own line. It reads `supported` only for a configuration that has been run with a real model, on real hardware, for a given release (R1).
- The Laraplate install guide (`laraplate/Modules/AI/docs/WHISPER_INSTALLATION.md`) repeats the status in its first lines and links the README section.
- `/health` reports the status as `"readiness": "experimental" | "production"`.

## 5. Requirements for `production`

### R1. Packaging and releases

- A virtualenv or an image of its own, never shared with another service. The systemd install moves from `/opt/ai-env` to `/opt/whisper-api/.venv`.
- Container images published to GitHub Container Registry for each tag: a CPU image, and a CUDA image where D3 allows. `ffmpeg` is in the image. A `compose.yaml` of reference.
- The systemd install stays supported for bare metal and LXC.
- Semantic version tags, a `CHANGELOG.md`, a GitHub release per tag.
- The CUDA image records the CUDA, cuDNN and cuBLAS versions it was built against, which CTranslate2 requires.

### R2. Startup, liveness and readiness

- The model loads in a background thread after the server starts, not at import.
- `GET /health` answers as soon as the process runs (liveness).
- `GET /ready` answers `503` until the model is loaded, then `200`. The container healthcheck and the docs use `/ready`.
- `/transcribe` answers `503` with `Retry-After` while the service is not ready.
- A model that fails to load makes `/ready` report the error and the process exit with a non-zero code, so systemd or the container runtime restarts it.

### R3. Backpressure and resource limits

- A bounded number of uploads in flight (configurable). Beyond it: `503` with `Retry-After`, not an unbounded wait.
- Maximum media duration (`WHISPER_MAX_DURATION_SECONDS`), measured before transcription with the decoder that faster-whisper already uses; over the limit answers `413`.
- The systemd unit sets `MemoryMax`; the compose file sets a memory limit. The docs give the figures per `WHISPER_MODEL`.

### R4. Errors

- `5xx` answers carry a generic message and the request id, never the exception text. A file the decoder cannot read answers `400`, not `500`.
- Every answer carries `X-Request-Id`: the one the client sent, or a new one. The Laraplate client side is in the sibling spec.

### R5. Observability

- `GET /metrics` in Prometheus format: requests and latency, uploads in flight and rejected, audio seconds transcribed, model load time. Behind the API key when one is set, or on a separate port.
- Optional JSON logs.
- Logs never contain transcripts or file names: they are customer data.
- New dependency to approve: `prometheus-client` (Q2).

### R6. Continuous integration

- GitHub Actions on every push: unit tests, `ruff`, `pip-audit`.
- Dependabot for pip, Docker and Actions. Updates merge when the tests pass (D4).
- A nightly job with Whisper `tiny` and a short audio sample that exercises the HTTP contract end to end.
- A release workflow that builds and pushes the images on a tag.

### R7. Versions and contract

- `/health` adds `version` (the release) and `api_version` (the HTTP contract, starting at `1`) next to `readiness`.
- An OpenAPI document of the current endpoints, kept in the repository. The Laraplate client tests against it (sibling spec).

### R8. Transport

- The README gives a reverse proxy configuration of reference for TLS (Caddy and nginx), with the body size limit aligned to `WHISPER_MAX_UPLOAD_MB`. The compose file includes an optional Caddy service.
- The default listening address stays `0.0.0.0` for container use; the bare-metal docs recommend `127.0.0.1` behind the proxy.

### R9. Model licence

Delivered in Phase 0: the README states the licence of the default model,
Whisper `base` as `Systran/faster-whisper-base`, MIT on its model card, and that
another `WHISPER_MODEL` brings the licence on its own model card.

### R10. Model and library updates (D4)

- No revision pinned by default: the model loads at its latest release.
- At load, the service logs the resolved revision and `/health` or a `/models` endpoint reports it, so a regression can be traced to a model release.
- When a model release breaks compatibility: an env var pins the revision (`WhisperModel` in faster-whisper 1.2.1 accepts a `revision` argument), and the docs describe provisioning the weights ahead of time and running with `HF_HUB_OFFLINE=1`. This is the fallback in D4, not the default.
- `faster-whisper` moves from `1.0.*` to the latest release under the same rule.

## 6. Evolutions

They do not gate `production`.

### E1. Standard protocol (highest leverage)

- Next to `/transcribe`, which stays: `POST /v1/audio/transcriptions` in the OpenAI shape.
- With the Laraplate client speaking the same protocol (sibling spec), a customer can run this service, speaches (MIT) or a cloud API with only `.env` changes.

### E2. Asynchronous transcription

- `POST /jobs` answers `202` with an id; `GET /jobs/{id}` returns the state and, when done, the result. Results expire after a configurable time.
- Jobs live in the single process, in memory with spill to disk. No external broker.
- The synchronous `/transcribe` stays for short files. The Laraplate side (submit and poll) is in the sibling spec.
- Voice activity detection (`vad_filter`, available in faster-whisper) skips silence; on by default after measuring its effect on a test set.

### E4. GPU

- The CUDA image (R1), `float16` defaults on GPU, `BatchedInferencePipeline` from faster-whisper for throughput.
- Docs for the NVIDIA container toolkit and for GPU passthrough to an LXC container.
- D3 applies: a GPU configuration that has not been run reads `not tested` in the readiness section.

### E5. Keys with scopes

- A keys file with one entry per key: scopes (`transcribe`, `metrics`) and a rate limit per key. Keys stored as hashes.
- `WHISPER_API_KEY` keeps working as keys with every scope.

## 7. Out of scope

- Hosting the service for customers, multi-tenancy, quotas, billing (D1).
- Selling the service or its support (D2).
- Pinning model revisions by default (D4).
- A job queue with an external broker (E2 stays in-process).

## 8. Order of work

| Phase | Content |
|---|---|
| 0 | Readiness section `experimental`, model licence, `readiness` in `/health`. **Delivered 2026-10-20** (0b3c11a) |
| 1 | R2, R3, R4, R7, R10 |
| 2 | R6, R1, R8, R5 |
| 3 | Status `production` when section 5 is complete |
| 4 | E1 |
| 5 | E2, E4 |
| 6 | E5 |

**Phase 0 delivery (2026-10-20):** the README opens with a Production readiness
section (status `experimental`, what was tested, known gaps, default model
licence) and `/health` reports `readiness`. The Laraplate guide opens with the
status and links that section (`laraplate-ai` c2530f0).

## 9. Open questions

- **Q2. New dependencies.** `prometheus-client` (R5); GitHub Container Registry for the images (R1); `ruff` and `pip-audit` in CI only (R6).
- **Q3. GPU hardware.** Which GPU, if any, is available to run the CUDA configuration before a release (D3, E4)?
- **Q4. E1 before E2?** With E1, a customer who needs long transcriptions can already use a server that has them. E2 could wait until E1 ships and customers ask for it.
