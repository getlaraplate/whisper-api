import os
import tempfile

from faster_whisper import WhisperModel
from flask import Flask, jsonify, request

app = Flask(__name__)

# faster-whisper (CTranslate2). CPU by default; for GPU set WHISPER_DEVICE=cuda
# and WHISPER_COMPUTE_TYPE=float16. The Laraplate client posts the media file to
# /transcribe and reads back {"text", "language", "duration"}.
MODEL_SIZE = os.environ.get("WHISPER_MODEL", "base")
DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")
COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
API_KEY = os.environ.get("WHISPER_API_KEY", "")
PORT = int(os.environ.get("WHISPER_PORT", "8001"))

# Loaded once at startup; cached on disk after the first download.
model = WhisperModel(MODEL_SIZE, device=DEVICE, compute_type=COMPUTE_TYPE)


def authorized():
    """Open when no key is set; otherwise require Authorization: Bearer <key>."""
    if not API_KEY:
        return True
    return request.headers.get("Authorization", "") == f"Bearer {API_KEY}"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "model": MODEL_SIZE, "device": DEVICE})


@app.route("/transcribe", methods=["POST"])
def transcribe():
    if not authorized():
        return jsonify({"error": "unauthorized"}), 401

    file = request.files.get("file")
    if file is None:
        return jsonify({"error": "missing file"}), 400

    language = request.args.get("language") or request.form.get("language")
    suffix = os.path.splitext(file.filename or "")[1] or ".bin"

    with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
        file.save(tmp.name)
        segments, info = model.transcribe(tmp.name, language=language)
        text = " ".join(segment.text.strip() for segment in segments).strip()

    return jsonify({"text": text, "language": info.language, "duration": info.duration})


if __name__ == "__main__":
    # threaded so /health answers while a transcription is running.
    app.run(host="0.0.0.0", port=PORT, threaded=True)
