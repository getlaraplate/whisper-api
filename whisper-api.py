import hmac
import os
import re
import tempfile

from faster_whisper import WhisperModel
from flask import Flask, jsonify, request

app = Flask(__name__)


def env_list(name):
    """Comma-separated values of an environment variable, blanks dropped."""
    return [value.strip() for value in os.environ.get(name, "").split(",") if value.strip()]


# faster-whisper (CTranslate2). CPU by default; for GPU set WHISPER_DEVICE=cuda
# and WHISPER_COMPUTE_TYPE=float16. The Laraplate client posts the media file to
# /transcribe and reads back {"text", "language", "duration"}. The model is fixed
# here: the client cannot name one, so there is nothing for it to download.
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")
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

# Media files are large; the server spools the body to disk, not to memory.
MAX_UPLOAD_MB = max(1, int(os.environ.get("WHISPER_MAX_UPLOAD_MB", "512")))
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024

# An ISO 639 code such as "it" or "haw"; anything else would fail inside the model.
LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}$")
# The temporary file keeps the upload's extension only when it looks like one.
SUFFIX_PATTERN = re.compile(r"^\.[A-Za-z0-9]{1,10}$")

# Loaded once at startup; cached on disk after the first download.
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE, num_workers=NUM_WORKERS)


def authorized():
    """Open when no key is set; otherwise require Authorization: Bearer <key>."""
    if not API_KEYS:
        return True
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        return False
    return any(hmac.compare_digest(token.encode(), key.encode()) for key in API_KEYS)


@app.errorhandler(413)
def request_too_large(error):
    return jsonify({"error": f"file too large (max {MAX_UPLOAD_MB} MB)"}), 413


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "model": MODEL_SIZE, "device": DEVICE})


@app.route("/transcribe", methods=["POST"])
def transcribe():
    # Checked before the body is read, so an unauthorized upload is never parsed.
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401, {"WWW-Authenticate": "Bearer"}

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
        segments, info = model.transcribe(tmp.name, language=language)
        text = " ".join(segment.text.strip() for segment in segments).strip()

    return jsonify({"text": text, "language": info.language, "duration": info.duration})


if __name__ == "__main__":
    # Development server only; production runs under gunicorn (gunicorn.conf.py).
    # threaded so /health answers while a transcription is running.
    app.run(host=HOST, port=PORT, threaded=True)
