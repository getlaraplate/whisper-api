"""Gunicorn settings for whisper-api (production server).

    gunicorn -c gunicorn.conf.py whisper-api:app

The address comes from the same variables as the development server. Override
any other setting with GUNICORN_CMD_ARGS (e.g. "--threads 8"), which takes
precedence over this file.

Concurrency model: one worker process holds the model; its threads accept
requests. Transcriptions run inside the model, at most WHISPER_NUM_WORKERS at a
time (default 1); the other uploads wait in their thread, and /health keeps
answering. Each waiting request holds a thread and its spooled upload, so
`threads` also caps how many uploads can queue.
"""

import os

bind = f"{os.environ.get('WHISPER_HOST', '0.0.0.0')}:{os.environ.get('WHISPER_PORT', '8001')}"

# One process: every worker would load its own copy of the model.
workers = 1
worker_class = "gthread"
threads = 4

# The gthread worker keeps sending heartbeats while a long transcription runs in
# a thread; this only bounds how long the worker may stay unresponsive.
timeout = 600
graceful_timeout = 60

# No runtime control socket: it would live in the home of a user that has none.
control_socket_disable = True

accesslog = "-"
errorlog = "-"
