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

**Tested on:** unit tests with a fake model (no model download), run by hand.
Earlier releases ran on the Laraplate test container (Debian LXC, 4 GB RAM,
2 vCPU, CPU only, model `base`); the hardened release (gunicorn, unprivileged
user, upload limit) has not been verified there yet. **GPU:** not tested.

**Known gaps:**

- The documented install shares one virtualenv (`/opt/ai-env`) with
  `sentence-transformers-api`: upgrading one service can break the other.
- No container image, no tagged release, no changelog.
- The model loads at startup with no separate readiness check: a process still
  loading looks alive to a load balancer or a healthcheck.
- Transcription is synchronous and runs one file at a time by default (see
  [Concurrency](#concurrency)): long media can exceed the client's timeout.
- No backpressure and no maximum media duration: uploads wait without limit
  when every thread is busy.
- No metrics and no request id in the logs.
- No continuous integration: the tests run only by hand.
- No machine-readable API description and no service version in `/health`.
- `faster-whisper` is held at `1.0.*`, behind its latest release.
- Plain HTTP: TLS needs your own reverse proxy (see [Security](#security)).

**Default model:** Whisper `base`, converted for CTranslate2 as
[`Systran/faster-whisper-base`](https://huggingface.co/Systran/faster-whisper-base),
licence MIT (model card). Another `WHISPER_MODEL` brings the licence on its own
model card.

## Endpoints

- `GET /health` -> `{"status":"healthy","readiness":"experimental","model":"base","device":"cpu"}`. Always open.
- `POST /transcribe` (multipart `file`, optional `language`, as `?language=it` or a
  form field; `Authorization: Bearer <WHISPER_API_KEY>` when a key is set) ->
  `{"text","language","duration"}`

```bash
curl -s -H "Authorization: Bearer $KEY" -F file=@sample.mp3 http://localhost:8001/transcribe
```

Errors on `/transcribe`: `400` without a `file` or with a `language` that is not
an ISO 639 code (`it`, `en`, `haw`), `401` without a valid key, `413` for a file
over `WHISPER_MAX_UPLOAD_MB`. The Laraplate client treats any error as "no
transcript" and indexes the media without one.

## Security

- **Authentication.** When `WHISPER_API_KEY` is set, `/transcribe` requires
  `Authorization: Bearer <key>`, the header the Laraplate client sends with its
  `WHISPER_API_KEY`. Several keys may be given, comma-separated, to rotate one.
  The key is checked before the upload is read. Without a key the service is
  open: run it like that only on a trusted network.
- **Model.** The model is fixed by `WHISPER_MODEL` on the host; a request cannot
  name one, so it cannot make the service download anything.
- **Upload limit.** Files larger than `WHISPER_MAX_UPLOAD_MB` answer `413`. The
  upload is spooled to the temporary directory and copied once more for the
  decoder, so keep about twice that size free in `/tmp`.
- **Process.** The systemd unit runs gunicorn as the unprivileged `whisper-api`
  user.
- **Transport.** The service speaks plain HTTP. Outside a private network, put
  it behind a TLS-terminating reverse proxy, or bind it to `127.0.0.1` with
  `WHISPER_HOST`.

## Concurrency

Transcription is synchronous: the request stays open until the text is ready,
and there is no job queue. gunicorn runs **one worker process with 4 threads**
([`gunicorn.conf.py`](gunicorn.conf.py)), because every process would load its
own copy of the model:

- At most `WHISPER_NUM_WORKERS` transcriptions run at the same time (default 1);
  the other uploads wait in their thread.
- `/health` keeps answering while a transcription runs.
- Each waiting upload holds a thread, so the 4 threads also cap how many
  requests can be in flight. Raise them with `GUNICORN_CMD_ARGS="--threads 8"`.
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
| `WHISPER_NUM_WORKERS` | `1` | Transcriptions that may run at the same time (see [Concurrency](#concurrency)). |
| `WHISPER_API_KEY` | *(empty)* | Bearer token; several comma-separated keys are accepted. Empty = open (trusted network only). Kept in `.env`, never committed. |
| `WHISPER_MAX_UPLOAD_MB` | `512` | Largest accepted upload, in MB; `413` beyond. |
| `WHISPER_HOST` | `0.0.0.0` | Listening address. |
| `WHISPER_PORT` | `8001` | 8000 is the embedding service. |
| `GUNICORN_CMD_ARGS` | *(empty)* | Overrides [`gunicorn.conf.py`](gunicorn.conf.py), e.g. `--threads 8`. |

## Tests

```bash
pip install flask
python -m unittest discover -s tests
```

The tests replace `faster-whisper` with a fake: they download no model and check
what the service asks of the model and what it answers.

## Wire into Laraplate

`laraplate/.env`: `WHISPER_URL=http://<host>:8001`, `WHISPER_API_KEY=<key>`,
`AI_MEDIA_TRANSCRIPTION_MODEL=whisper-local`.

## License

Licensed under the [Apache License 2.0](LICENSE).
