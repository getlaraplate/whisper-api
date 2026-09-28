# Whisper API

Self-hosted speech-to-text for Laraplate media analysis, on the shared `ai` host.
Mirrors [`sentence-transformers-api`](https://github.com/swolley/sentence-transformers-api):
a small Flask service under `/opt`, run by systemd, using the shared `/opt/ai-env`
Python environment. Built on [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(CTranslate2). CPU by default; GPU optional.

## Endpoints

- `GET /health` -> `{"status":"healthy","model":"base","device":"cpu"}`
- `POST /transcribe` (multipart `file`, optional `?language=it`; `Authorization: Bearer <WHISPER_API_KEY>` when a key is set) -> `{"text","language","duration"}`

```bash
curl -s -H "Authorization: Bearer $KEY" -F file=@sample.mp3 http://localhost:9000/transcribe
```

## Install (Debian LXC, no Docker)

```bash
apt install -y ffmpeg                       # required to decode audio/video
git clone git@github.com:swolley/whisper-api.git /opt/whisper-api
/opt/ai-env/bin/pip install -r /opt/whisper-api/requirements.txt
printf 'WHISPER_API_KEY=%s\n' "$(openssl rand -hex 24)" > /opt/whisper-api/.env
cp /opt/whisper-api/whisper.service /etc/systemd/system/whisper.service
systemctl daemon-reload && systemctl enable --now whisper
```

## Config (env, via systemd unit or /opt/whisper-api/.env)

| Var | Default | Notes |
|-----|---------|-------|
| `WHISPER_MODEL` | `base` | `tiny`/`base`/`small`/`medium`/`large-v3`. Keep to `base`/`small` on a 4 GB CPU container. |
| `WHISPER_DEVICE` | `cpu` | `cuda` for GPU. |
| `WHISPER_COMPUTE_TYPE` | `int8` | GPU: `float16`. |
| `WHISPER_API_KEY` | *(empty)* | Bearer token; empty = open (dev only). Kept in `.env`, never committed. |
| `WHISPER_PORT` | `9000` | 8000 is the embedding service. |

## Wire into Laraplate

`laraplate/.env`: `WHISPER_URL=http://<host>:9000`, `WHISPER_API_KEY=<key>`,
`AI_MEDIA_TRANSCRIPTION_MODEL=whisper-local`.
