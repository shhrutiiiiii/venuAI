"""Voice endpoints (Wave B / B4): speech-to-text and text-to-speech via Groq.

  POST /voice/transcribe  multipart `audio` -> {text, language, duration_s}
  POST /voice/speak       {text, voice?}    -> audio/wav
  GET  /voice/status      -> {stt, tts}

Guarantees: env-gated (VOICE_ENABLED, default off -> 503 {"detail":"voice disabled"});
audio is processed in memory only and never written to disk or logged; uploads are
checked for size (<=10 MB), container magic bytes, and duration (<=60 s); 10
requests/min/user. Transcripts are returned as text only - the client submits them
through the normal /query path, so they get the full input-safety treatment.
Duration: WAV is measured exactly before any API call; for other containers the
duration reported by Whisper is enforced after transcription (the 10 MB cap bounds
the cost of a rejected call).
"""
from __future__ import annotations

import io
import logging
import os
import re
import wave
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from VENUAI import simple_rate_limit
from VENUAI.auth import AuthenticatedUser, get_current_user

logger = logging.getLogger("drdo-rag")
router = APIRouter(tags=["voice"])

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_AUDIO_SECONDS = 60.0
MAX_TTS_CHARS = 1500
RATE_PER_MIN = 10
DEFAULT_STT_MODEL = "whisper-large-v3-turbo"
DEFAULT_TTS_MODEL = "canopylabs/orpheus-v1-english"


def voice_enabled() -> bool:
    return os.getenv("VOICE_ENABLED", "off").strip().lower() in ("on", "1", "true", "yes")


def _require_enabled() -> None:
    if not voice_enabled():
        raise HTTPException(status_code=503, detail="voice disabled")
    if not os.getenv("GROQ_API_KEY"):
        raise HTTPException(status_code=503, detail="voice unavailable: GROQ_API_KEY is not configured")


def _model_from_registry(capability_name: str, default: str, env_name: str) -> str:
    """Model id: env override > gateway registry (first enabled model with the
    capability) > documented default."""
    if os.getenv(env_name):
        return os.environ[env_name]
    try:
        from VENUAI.ai_gateway import model_registry as mr
        cap = getattr(mr.ModelCapability, capability_name, None)
        if cap is not None:
            models = mr.get_models_with_capability(cap)
            if models:
                return models[0].model_id
    except Exception:  # noqa: BLE001
        pass
    return default


def stt_model() -> str:
    return _model_from_registry("SPEECH_TO_TEXT", DEFAULT_STT_MODEL, "VOICE_STT_MODEL")


def tts_model() -> str:
    return _model_from_registry("TEXT_TO_SPEECH", DEFAULT_TTS_MODEL, "VOICE_TTS_MODEL")


# ── container sniffing ───────────────────────────────────────────────────────
def sniff_container(data: bytes) -> Optional[str]:
    """Return 'wav'|'webm'|'ogg'|'mp3'|'m4a' from magic bytes, else None."""
    if len(data) < 12:
        return None
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    if data[4:8] == b"ftyp":
        return "m4a"
    return None


def wav_duration_seconds(data: bytes) -> Optional[float]:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            rate = w.getframerate()
            return w.getnframes() / float(rate) if rate else None
    except Exception:  # noqa: BLE001
        return None


# ── provider calls (patched in tests) ────────────────────────────────────────
def _groq():
    from VENUAI.ai_gateway.provider import get_groq_client
    return get_groq_client()


def _transcribe(data: bytes, filename: str, model: str) -> dict:
    res = _groq().audio.transcriptions.create(
        file=(filename, data), model=model, response_format="verbose_json", temperature=0.0)
    get = (lambda k: res.get(k)) if isinstance(res, dict) else (lambda k: getattr(res, k, None))
    return {"text": (get("text") or "").strip(), "language": get("language"), "duration": get("duration")}


def _synthesize_chunk(text: str, voice: str, model: str) -> bytes:
    resp = _groq().audio.speech.create(model=model, voice=voice, input=text, response_format="wav")
    if hasattr(resp, "read"):
        return resp.read()
    return getattr(resp, "content", resp)


def split_for_tts(text: str, limit: int) -> List[str]:
    """Split on sentence boundaries into chunks <= limit chars (hard-splits long runs)."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    chunks: List[str] = []
    cur = ""
    for s in sentences:
        while len(s) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            cut = s.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            chunks.append(s[:cut].strip())
            s = s[cut:].strip()
        if cur and len(cur) + 1 + len(s) > limit:
            chunks.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


def concat_wavs(parts: List[bytes]) -> bytes:
    if len(parts) == 1:
        return parts[0]
    out = io.BytesIO()
    params = None
    with wave.open(out, "wb") as dst:
        for p in parts:
            with wave.open(io.BytesIO(p), "rb") as src:
                if params is None:
                    params = src.getparams()
                    dst.setparams(params)
                dst.writeframes(src.readframes(src.getnframes()))
    return out.getvalue()


# ── endpoints ────────────────────────────────────────────────────────────────
class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    voice: Literal["troy", "hannah", "austin"] = "troy"


@router.get("/voice/status")
def voice_status(user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    on = voice_enabled() and bool(os.getenv("GROQ_API_KEY"))
    return {"stt": on, "tts": on}


@router.post("/voice/transcribe")
async def voice_transcribe(audio: UploadFile = File(...),
                           user: AuthenticatedUser = Depends(get_current_user)) -> dict:
    _require_enabled()
    simple_rate_limit.check("voice", user.id, RATE_PER_MIN)

    data = bytearray()
    while True:
        chunk = await audio.read(1024 * 1024)
        if not chunk:
            break
        data.extend(chunk)
        if len(data) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio too large (10 MB limit).")
    data = bytes(data)
    if not data:
        raise HTTPException(status_code=400, detail="Empty audio upload.")

    kind = sniff_container(data)
    if kind is None:
        raise HTTPException(status_code=415, detail="Unsupported audio format (webm, ogg, wav, mp3, m4a).")
    if kind == "wav":
        dur = wav_duration_seconds(data)
        if dur is None:
            raise HTTPException(status_code=400, detail="Malformed WAV file.")
        if dur > MAX_AUDIO_SECONDS:
            raise HTTPException(status_code=413, detail="Audio too long (60 s limit).")

    try:
        res = _transcribe(data, f"audio.{kind}", stt_model())
    except Exception as e:  # noqa: BLE001
        logger.warning("[voice] transcription failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="Transcription service failed.") from e
    finally:
        del data  # never retain audio

    duration = res.get("duration")
    if isinstance(duration, (int, float)) and duration > MAX_AUDIO_SECONDS:
        raise HTTPException(status_code=413, detail="Audio too long (60 s limit).")
    return {"text": res["text"], "language": res.get("language"),
            "duration_s": float(duration) if isinstance(duration, (int, float)) else None}


@router.post("/voice/speak")
def voice_speak(body: SpeakRequest, user: AuthenticatedUser = Depends(get_current_user)) -> Response:
    _require_enabled()
    simple_rate_limit.check("voice", user.id, RATE_PER_MIN)
    model = tts_model()
    chunk_chars = max(50, int(os.getenv("VOICE_TTS_CHUNK_CHARS", "200")))
    try:
        parts = [_synthesize_chunk(c, body.voice, model) for c in split_for_tts(body.text, chunk_chars)]
        audio = concat_wavs(parts)
    except Exception as e:  # noqa: BLE001
        logger.warning("[voice] synthesis failed: %s", type(e).__name__)
        raise HTTPException(status_code=502, detail="Speech synthesis failed.") from e
    return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})
