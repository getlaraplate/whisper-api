# Whisper API

Self-hosted speech-to-text for Laraplate media analysis, on the shared `ai` host.
Mirrors [`sentence-transformers-api`](https://github.com/getlaraplate/sentence-transformers-api):
a small Flask service under `/opt`, served by gunicorn under systemd, using the
shared `/opt/ai-env` Python environment. Built on
[faster-whisper](https://github.com/SYSTRAN/faster-whisper) (CTranslate2). CPU by
default; GPU optional.

## Production readiness

**Status: experimental.** Fine for development, tests and evaluation on a
trusted network. Not ready for production yet: the gaps below are open, and the
status changes to `production` only when all of them are closed. `GET /health`
reports the same status in `readiness`. The work that closes them, and what
`production` requires, is in
[the production readiness spec](docs/superpowers/specs/2026-10-20-production-readiness-design.md).

**Tested on:** unit tests with a fake model (no model download), run by hand,
and the release of 2026-10-21 under gunicorn with the real `tiny`, `base` and
`small` models on a development machine (x86_64, CPU only), for the memory
figures in [Memory](#memory), readiness, unreadable and too long media.
Earlier releases ran on the Laraplate test container (Debian LXC, 4 GB RAM,
2 vCPU, CPU only, model `base`); the current one has not been verified there
yet. **GPU:** not tested.

**Known gaps:**

- The documented install shares one virtualenv (`/opt/ai-env`) with
  `sentence-transformers-api`: upgrading one service can break the other.
- No container image, no tagged release, no changelog.
- Transcription is synchronous and runs one file at a time by default (see
  [Concurrency](#concurrency)): long media can exceed the client's timeout.
- PyAV is held below 19: faster-whisper 1.2.1 does not work with it yet (see
  `requirements.txt`).
- No metrics.
- No continuous integration: the tests run only by hand.
- Plain HTTP: TLS needs your own reverse proxy (see [Security](#security)).

**Default model:** Whisper `base`, converted for CTranslate2 as
[`Systran/faster-whisper-base`](https://huggingface.co/Systran/faster-whisper-base),
licence MIT (model card). Another `WHISPER_MODEL` brings the licence on its own
model card.

## Endpoints

[`openapi.json`](openapi.json) describes every endpoint, request and answer
(OpenAPI 3.1); the tests check that the service matches it. `api_version` in
`/health` is the version of that contract, raised only by a change that breaks
a client.

- `GET /health` (liveness, always open) ->
  `{"status":"healthy","readiness":"experimental","version":"0.1.0","api_version":1,"model":"base","model_revision":"<commit>","device":"cpu"}`
- `GET /ready` (readiness, always open) -> `200` `{"status":"ready"}` once the
  model is loaded, `503` `{"status":"loading"}` before. Point load balancers and
  container healthchecks here.
- `POST /transcribe` (multipart `file`, optional `language`, as `?language=it` or a
  form field; `Authorization: Bearer <WHISPER_API_KEY>` when a key is set) ->
  `{"text","language","duration"}`

Every answer carries an `X-Request-Id` header: the one the client sent, when it
matches `[A-Za-z0-9._-]{1,128}`, otherwise a new one. The access log prints it.

```bash
curl -s -H "Authorization: Bearer $KEY" -F file=@sample.mp3 http://localhost:8001/transcribe
```

Errors on `/transcribe`: `400` without a `file`, with a `language` that is not
an ISO 639 code (`it`, `en`, `haw`) or with a file that is not readable media;
`401` without a valid key; `413` for a file over `WHISPER_MAX_UPLOAD_MB` or media
longer than `WHISPER_MAX_DURATION_SECONDS`; `503` with `Retry-After` while the
model loads or when `WHISPER_MAX_IN_FLIGHT` uploads are already in flight;
`500` `{"error": "internal error", "request_id": "..."}` for anything
unexpected, with the details in the service log under that request id. The
Laraplate client treats any error as "no transcript" and indexes the media
without one.

At startup the server answers at once and loads the model in the background. A
model that cannot be downloaded or loaded ends the process with gunicorn's boot
error (exit code 3) and the reason in the log; systemd restarts it after
`RestartSec`.

## Security

- **Authentication.** When `WHISPER_API_KEY` is set, `/transcribe` requires
  `Authorization: Bearer <key>`, the header the Laraplate client sends with its
  `WHISPER_API_KEY`. Several keys may be given, comma-separated, to rotate one.
  The key is checked before the upload is read. Without a key the service is
  open: run it like that only on a trusted network.
- **Model.** The model is fixed by `WHISPER_MODEL` on the host; a request cannot
  name one, so it cannot make the service download anything.
- **Upload limits.** Files larger than `WHISPER_MAX_UPLOAD_MB` answer `413`. The
  upload is spooled to the temporary directory and copied once more for the
  decoder, so keep about twice that size free in `/tmp`. Before transcribing,
  the service reads the duration declared by the file with PyAV: longer than
  `WHISPER_MAX_DURATION_SECONDS` answers `413`, a file PyAV cannot open answers
  `400`. Neither the file name nor its path reaches the logs.
- **Process.** The systemd unit runs gunicorn as the unprivileged `whisper-api`
  user.
- **Transport.** The service speaks plain HTTP. Outside a private network, put
  it behind a TLS-terminating reverse proxy, or bind it to `127.0.0.1` with
  `WHISPER_HOST`.

## Concurrency

Transcription is synchronous: the request stays open until the text is ready,
and there is no job queue. gunicorn runs **one worker process with 8 threads**
([`gunicorn.conf.py`](gunicorn.conf.py)), because every process would load its
own copy of the model. Do not turn on `preload_app`: the model loads in a
thread of the worker.

- At most `WHISPER_NUM_WORKERS` transcriptions run at the same time (default 1);
  the other uploads wait in their thread.
- At most `WHISPER_MAX_IN_FLIGHT` uploads (default 4) are transcribed or wait;
  the next answers `503` `{"error": "busy"}` with `Retry-After`, before its body
  is read. Keep gunicorn's threads above that number
  (`GUNICORN_CMD_ARGS="--threads 12"` for more).
- `/health` and `/ready` keep answering while a transcription runs.
- A waiting request counts against the client's timeout (`WHISPER_TIMEOUT` on the
  Laraplate side, default 120 s). On CPU, keep `WHISPER_NUM_WORKERS` at 1 and
  raise the client timeout for long media rather than running transcriptions in
  parallel, which would share the same cores. With a GPU or many cores,
  `WHISPER_NUM_WORKERS=2` lets two transcriptions run in parallel (memory grows
  accordingly).

## Memory

Resident memory of the gunicorn worker, measured on 2026-10-21 (x86_64 CPU,
Python 3.14, faster-whisper 1.2.1, `int8`, one transcription at a time) with a
120-second synthetic sample (a tone over noise; real speech decodes differently,
so treat the figures as an order of magnitude):

| `WHISPER_MODEL` | Idle | Peak | Time for the sample |
| --------------- | ---- | ---- | ------------------- |
| `tiny` | 0.19 GB | 0.26 GB | |
| `base` | 0.24 GB | 0.56 GB | 15 s |
| `small` | 0.60 GB | 1.36 GB | 165 s |

The systemd unit caps the service at `MemoryMax=1G`, enough for `base`. Raise it
to `2G` for `small`, and further for `medium`, `large-v3` or
`WHISPER_NUM_WORKERS` above 1. Uploads waiting for the model are spooled to
disk and add little memory. On a host shared with `sentence-transformers-api`,
keep the sum of both peaks below the host's memory.

## Install (Debian LXC, no Docker)

Create the dedicated user once (no home, no login shell), then install the code
owned by root so the service can read but not modify it:

```bash
apt install -y ffmpeg                       # required to decode audio/video
useradd --system --user-group --no-create-home --shell /usr/sbin/nologin whisper-api
git clone git@github.com:getlaraplate/whisper-api.git /opt/whisper-api
/opt/ai-env/bin/pip install -r /opt/whisper-api/requirements.txt
printf 'WHISPER_API_KEY=%s\n' "$(openssl rand -hex 24)" > /opt/whisper-api/.env
chmod 600 /opt/whisper-api/.env             # systemd reads it as root
cp /opt/whisper-api/whisper.service /etc/systemd/system/whisper.service
systemctl daemon-reload && systemctl enable --now whisper
curl -s http://127.0.0.1:8001/ready         # {"status": "ready"} once the model is loaded
```

The model is cached in `/var/cache/whisper-api` (`HF_HOME`), which systemd
creates and hands to `whisper-api`.

Upgrading from a unit that ran as `root`: the model cached in
`/root/.cache/huggingface` is not visible to the new user and is downloaded
again on the first start. To skip the download, copy it before starting:

```bash
mkdir -p /var/cache/whisper-api
cp -a /root/.cache/huggingface/. /var/cache/whisper-api/
chown -R whisper-api:whisper-api /var/cache/whisper-api
```

The commented `PrivateTmp`, `ProtectSystem` and `ProtectHome` lines in the unit
add sandboxing where mount namespaces are available (not in every unprivileged
LXC container).

## Model updates

The model loads at its **latest revision** on Hugging Face. `/health` reports
the revision in `model_revision` and the log records it at startup, so a change
of behaviour can be traced to a model release. When a release breaks
compatibility, pin the last good one with `WHISPER_MODEL_REVISION=<commit>`. To
stop depending on Hugging Face at runtime, download the model ahead of time
into the cache the service uses and set `HF_HUB_OFFLINE=1` in `.env`:

```bash
sudo -u whisper-api HF_HOME=/var/cache/whisper-api \
  /opt/ai-env/bin/hf download Systran/faster-whisper-base
```

Python dependencies follow the same rule: the latest release that works. A
known incompatibility is pinned in `requirements.txt` with the reason next to
it, as PyAV is today.

## Running locally

```bash
python whisper-api.py                          # Flask development server
gunicorn -c gunicorn.conf.py whisper-api:app   # production server
```

## Config (env, via systemd unit or /opt/whisper-api/.env)

| Var | Default | Notes |
|-----|---------|-------|
| `WHISPER_MODEL` | `base` | `tiny`/`base`/`small`/`medium`/`large-v3`. Keep to `base`/`small` on a 4 GB CPU container. |
| `WHISPER_DEVICE` | `cpu` | `cuda` for GPU. |
| `WHISPER_COMPUTE_TYPE` | `int8` | GPU: `float16`. |
| `WHISPER_MODEL_REVISION` | *(empty)* | Pins a model revision (commit); empty = the latest (see [Model updates](#model-updates)). |
| `WHISPER_NUM_WORKERS` | `1` | Transcriptions that may run at the same time (see [Concurrency](#concurrency)). |
| `WHISPER_MAX_IN_FLIGHT` | `4` | Uploads transcribed or waiting at once; the next answers `503` `busy`. |
| `WHISPER_MAX_DURATION_SECONDS` | `10800` | Longest media accepted, from the duration the file declares; `413` beyond. |
| `WHISPER_API_KEY` | *(empty)* | Bearer token; several comma-separated keys are accepted. Empty = open (trusted network only). Kept in `.env`, never committed. |
| `WHISPER_MAX_UPLOAD_MB` | `512` | Largest accepted upload, in MB; `413` beyond. |
| `WHISPER_HOST` | `0.0.0.0` | Listening address. |
| `WHISPER_PORT` | `8001` | 8000 is the embedding service. |
| `GUNICORN_CMD_ARGS` | *(empty)* | Overrides [`gunicorn.conf.py`](gunicorn.conf.py), e.g. `--threads 12`. |

## Tests

```bash
pip install flask
python -m unittest discover -s tests
```

The tests replace `faster-whisper` and PyAV with fakes: they download no model,
decode no media, and check what the service asks of the model and what it
answers.

## Wire into Laraplate

`laraplate/.env`: `WHISPER_URL=http://<host>:8001`, `WHISPER_API_KEY=<key>`. Media
analysis and its transcription provider are then switched on in Filament >
Settings; see the Laraplate guide `Modules/AI/docs/WHISPER_INSTALLATION.md`.

## License

Licensed under the [Apache License 2.0](LICENSE).
