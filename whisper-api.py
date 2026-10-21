import hmac
import logging
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path

import av
from faster_whisper import WhisperModel
from faster_whisper.utils import download_model
from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

app = Flask(__name__)
logger = logging.getLogger("whisper_api")
if not logger.handlers:
    _log_handler = logging.StreamHandler()
    _log_handler.setFormatter(logging.Formatter("[%(asctime)s] [%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(_log_handler)
    logger.setLevel(logging.INFO)


def env_list(name):
    """Comma-separated values of an environment variable, blanks dropped."""
    return [value.strip() for value in os.environ.get(name, "").split(",") if value.strip()]


# faster-whisper (CTranslate2). CPU by default; for GPU set WHISPER_DEVICE=cuda
# and WHISPER_COMPUTE_TYPE=float16. The Laraplate client posts the media file to
# /transcribe and reads back {"text", "language", "duration"}. The model is fixed
# here: the client cannot name one, so there is nothing for it to download.
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")
# Empty: the latest revision on Hugging Face. Set only to pin a revision when a
# model release breaks compatibility.
MODEL_REVISION = os.environ.get("WHISPER_MODEL_REVISION") or None
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
# Transcriptions that may run at the same time inside the model. With 1, requests
# are transcribed one after the other while the server threads keep answering.
NUM_WORKERS = max(1, int(os.environ.get("WHISPER_NUM_WORKERS", "1")))
# Bearer keys accepted on /transcribe; several may be given, comma-separated, to
# rotate a key. No key means the service is open (trusted network only).
API_KEYS = env_list("WHISPER_API_KEY")
HOST = os.environ.get("WHISPER_HOST", "0.0.0.0")
PORT = int(os.environ.get("WHISPER_PORT", "8001"))

# Production readiness of this release, reported by /health and stated in the
# README: "experimental" until every requirement for production is met.
READINESS = "experimental"
# The release, from the VERSION file next to this one, and the version of the HTTP
# contract described in openapi.json; a change that breaks a client raises it.
VERSION_FILE = Path(__file__).with_name("VERSION")
VERSION = VERSION_FILE.read_text().strip() if VERSION_FILE.is_file() else "unknown"
API_VERSION = 1

# Media files are large; the server spools the body to disk, not to memory.
MAX_UPLOAD_MB = max(1, int(os.environ.get("WHISPER_MAX_UPLOAD_MB", "512")))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024
# Longest media accepted, read from the container header before transcription.
MAX_DURATION_SECONDS = max(1, int(os.environ.get("WHISPER_MAX_DURATION_SECONDS", "10800")))

# Uploads being transcribed or waiting for the model. One more is refused with
# 503 at once, before its body is read: below gunicorn's thread count, so
# /health, /ready and the refusals always find a thread (gunicorn.conf.py).
MAX_IN_FLIGHT = max(1, int(os.environ.get("WHISPER_MAX_IN_FLIGHT", "4")))
_in_flight = threading.BoundedSemaphore(MAX_IN_FLIGHT)

# An incoming X-Request-Id is kept when it looks like an id; otherwise one is made.
REQUEST_ID_PATTERN = re.compile(r"[A-Za-z0-9._-]{1,128}")

# An ISO 639 code such as "it" or "haw"; anything else would fail inside the model.
LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}$")
# The temporary file keeps the upload's extension only when it looks like one.
SUFFIX_PATTERN = re.compile(r"^\.[A-Za-z0-9]{1,10}$")

def resolve_model():
    """The path to load the model from and the revision commit it holds (None when unknown).

    A local directory is used as it is. A size or repository id goes through the
    Hugging Face cache, which keeps each revision under snapshots/<commit>/; with
    HF_HUB_OFFLINE=1 it is read from the cache without contacting Hugging Face.
    """
    if os.path.isdir(MODEL_SIZE):
        return MODEL_SIZE, None
    path = download_model(MODEL_SIZE, revision=MODEL_REVISION)
    parts = Path(path).parts
    revision = parts[parts.index("snapshots") + 1] if "snapshots" in parts[:-1] else None
    return path, revision


# gunicorn's exit code for a worker that failed to boot: gunicorn stops instead of
# starting the worker again, and systemd or the container runtime restarts it.
LOAD_FAILURE_EXIT_CODE = 3

# Set once the model is loaded; until then model and model_revision are None.
ready = threading.Event()
model = None
model_revision = None


def load_model():
    """Load the model once; cached on disk after the first download.

    Runs in a background thread so the server answers /health at once and /ready
    says when the model is there. A model that cannot be loaded ends the process
    instead of leaving it to answer 503 forever.
    """
    global model, model_revision
    try:
        path, revision = resolve_model()
        loaded = WhisperModel(path, device=DEVICE, compute_type=COMPUTE_TYPE, num_workers=NUM_WORKERS)
    except Exception:  # noqa: BLE001
        logger.exception("The model failed to load; stopping the process")
        os._exit(LOAD_FAILURE_EXIT_CODE)
        return
    model, model_revision = loaded, revision
    logger.info("Loaded model %s at revision %s", MODEL_SIZE, revision or "unknown")
    ready.set()


# Started at import, so in the gunicorn worker: the app must not be preloaded in
# the master (gunicorn.conf.py), whose threads do not survive the fork.
loader = threading.Thread(target=load_model, name="model-loader", daemon=True)
loader.start()


def authorized():
    """Open when no key is set; otherwise require Authorization: Bearer <key>."""
    if not API_KEYS:
        return True
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return False
    return any(hmac.compare_digest(token.encode(), key.encode()) for key in API_KEYS)


def media_duration(path):
    """Seconds the media lasts according to its container, or None when it declares none.

    Raises av.error.FFmpegError when the file is not a media file PyAV can open.
    """
    with av.open(path) as container:
        if container.duration is None:
            return None
        return container.duration / av.time_base


def request_id():
    """The id of the current request, made on first use."""
    if "request_id" not in g:
        incoming = request.headers.get("X-Request-Id", "")
        g.request_id = incoming if REQUEST_ID_PATTERN.fullmatch(incoming) else uuid.uuid4().hex
    return g.request_id


@app.before_request
def assign_request_id():
    request_id()


@app.after_request
def add_request_id(response):
    response.headers["X-Request-Id"] = request_id()
    return response


@app.errorhandler(413)
def request_too_large(error):
    return jsonify({"error": f"file too large (max {MAX_UPLOAD_MB} MB)"}), 413


@app.errorhandler(Exception)
def internal_error(error):
    """A generic answer for anything unexpected; the details go to the log only."""
    if isinstance(error, HTTPException):
        return error
    logger.exception("Unhandled error on %s (request %s)", request.path, request_id())
    return jsonify({"error": "internal error", "request_id": request_id()}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "healthy",
        "readiness": READINESS,
        "version": VERSION,
        "api_version": API_VERSION,
        "model": MODEL_SIZE,
        "model_revision": model_revision,
        "device": DEVICE,
    })


@app.route("/ready", methods=["GET"])
def ready_check():
    """Readiness: 200 once the model is loaded, 503 until then. Always open."""
    if ready.is_set():
        return jsonify({"status": "ready"})
    return jsonify({"status": "loading"}), 503


@app.route("/transcribe", methods=["POST"])
def transcribe():
    # Checked before the body is read, so an unauthorized upload is never parsed.
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401, {"WWW-Authenticate": "Bearer"}
    if not ready.is_set():
        return jsonify({"error": "loading model"}), 503, {"Retry-After": "10"}
    if not _in_flight.acquire(blocking=False):
        return jsonify({"error": "busy"}), 503, {"Retry-After": "30"}
    try:
        return transcribe_upload()
    finally:
        _in_flight.release()


def transcribe_upload():
    """The body of /transcribe once the request holds an in-flight slot."""
    file = request.files.get("file")
    if file is None:
        return jsonify({"error": "missing file"}), 400

    language = request.args.get("language") or request.form.get("language") or None
    if language is not None and not LANGUAGE_PATTERN.match(language):
        return jsonify({"error": "language must be an ISO 639 code such as 'it'"}), 400

    suffix = os.path.splitext(file.filename or "")[1]
    if not SUFFIX_PATTERN.match(suffix):
        suffix = ".bin"

    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        file.save(tmp.name)
        try:
            duration = media_duration(tmp.name)
        except av.error.FFmpegError:
            # The PyAV message quotes the temporary path: not logged.
            logger.warning("Refused an upload that is not readable media (request %s)", request_id())
            return jsonify({"error": "unreadable media"}), 400
        if duration is None:
            logger.info("The upload declares no duration; transcribing it within the upload limit")
        elif duration > MAX_DURATION_SECONDS:
            return jsonify({"error": f"media too long (max {MAX_DURATION_SECONDS} s)"}), 413
        segments, info = model.transcribe(tmp.name, language=language)
        text = " ".join(segment.text.strip() for segment in segments).strip()

    return jsonify({"text": text, "language": info.language, "duration": info.duration})


if __name__ == "__main__":
    # Development server only; production runs under gunicorn (gunicorn.conf.py).
    # threaded so /health answers while a transcription is running.
    app.run(host=HOST, port=PORT, threaded=True)
