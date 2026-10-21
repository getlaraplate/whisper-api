"""Gunicorn settings for whisper-api (production server).

    gunicorn -c gunicorn.conf.py whisper-api:app

The address comes from the same variables as the development server. Override
any other setting with GUNICORN_CMD_ARGS (e.g. "--threads 12"), which takes
precedence over this file.

Concurrency model: one worker process holds the model; its threads accept
requests. Transcriptions run inside the model, at most WHISPER_NUM_WORKERS at a
time (default 1); the other uploads wait in their thread, up to
WHISPER_MAX_IN_FLIGHT uploads in all (default 4). The next one is refused with
503 before its body is read, and /health and /ready keep answering.
"""

import os

bind = f"{os.environ.get('WHISPER_HOST', '0.0.0.0')}:{os.environ.get('WHISPER_PORT', '8001')}"

# One process: every worker would load its own copy of the model. At most
# WHISPER_MAX_IN_FLIGHT (default 4) uploads are served or wait for the model;
# the next is refused with 503, and the threads above that keep /health and
# /ready answering. Keep threads above WHISPER_MAX_IN_FLIGHT.
workers = 1
worker_class = "gthread"
threads = 8
# Connections gunicorn accepts and queues for its threads.
worker_connections = 32
# The model loads in a thread started when the app is imported; a preloaded app
# would start it in the master, and threads do not survive the fork.
preload_app = False

# The gthread worker keeps sending heartbeats while a long transcription runs in
# a thread; this only bounds how long the worker may stay unresponsive.
timeout = 600
graceful_timeout = 60

# No runtime control socket: it would live in the home of a user that has none.
control_socket_disable = True

accesslog = "-"
errorlog = "-"
