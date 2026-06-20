# src/tts_service.py
"""Multi-provider TTS service — dispatches to local Kokoro, OpenAI-compatible API, or browser."""

import io
import re
import wave
import logging
import hashlib
import httpx
from pathlib import Path
from typing import Optional, Dict, Any, List

from src.constants import TTS_CACHE_DIR

logger = logging.getLogger(__name__)

# Kokoro is more reliable with bounded prose than with an entire long model
# response in one pipeline invocation. Three sentences keeps natural cadence
# while preventing large local synthesis jobs from ending early.
LOCAL_TTS_SENTENCES_PER_CHUNK = 3
LOCAL_TTS_MAX_CHARS_PER_CHUNK = 900
LOCAL_TTS_SILENCE_MS = 90
LOCAL_TTS_SAMPLE_RATE = 24000


def _safe_speed(value, default: float = 1.0) -> float:
    """Parse the stored tts_speed defensively."""
    try:
        speed = float(value)
    except (TypeError, ValueError):
        return default
    return speed if speed > 0 else default


def _split_long_tts_sentence(sentence: str, max_chars: int) -> List[str]:
    """Split a very long punctuation-free sentence on word boundaries."""
    words = sentence.split()
    if not words:
        return []

    chunks: List[str] = []
    current: List[str] = []
    current_length = 0
    for word in words:
        word_length = len(word)
        projected = current_length + (1 if current else 0) + word_length
        if current and projected > max_chars:
            chunks.append(" ".join(current))
            current = [word]
            current_length = word_length
        else:
            current.append(word)
            current_length = projected
    if current:
        chunks.append(" ".join(current))
    return chunks


def _split_text_for_local_tts(text: str) -> List[str]:
    """Return natural three-sentence groups sized for local Kokoro synthesis."""
    normalized = re.sub(r"\s+", " ", (text or "").strip())
    if not normalized:
        return []

    # Keep punctuation with its sentence. A no-punctuation tail is retained.
    sentences = [
        sentence.strip()
        for sentence in re.findall(r"[^.!?]+(?:[.!?]+(?=\s|$)|$)", normalized)
        if sentence.strip()
    ]
    if not sentences:
        sentences = [normalized]

    expanded: List[str] = []
    for sentence in sentences:
        if len(sentence) > LOCAL_TTS_MAX_CHARS_PER_CHUNK:
            expanded.extend(_split_long_tts_sentence(sentence, LOCAL_TTS_MAX_CHARS_PER_CHUNK))
        else:
            expanded.append(sentence)

    groups: List[str] = []
    group: List[str] = []
    group_length = 0
    for sentence in expanded:
        projected = group_length + (1 if group else 0) + len(sentence)
        if group and (
            len(group) >= LOCAL_TTS_SENTENCES_PER_CHUNK
            or projected > LOCAL_TTS_MAX_CHARS_PER_CHUNK
        ):
            groups.append(" ".join(group))
            group = []
            group_length = 0

        group.append(sentence)
        group_length += (1 if group_length else 0) + len(sentence)

    if group:
        groups.append(" ".join(group))
    return groups


class TTSService:
    """Multi-provider TTS service.

    Reads provider config from data/settings.json on each call.
    Providers:
      "disabled"        — no TTS
      "browser"         — client-side Web Speech API (no server synthesis)
      "local"           — Kokoro-82M on GPU
      "endpoint:<id>"   — OpenAI-compatible /audio/speech via ModelEndpoint
    """

    def __init__(self, cache_dir: str = TTS_CACHE_DIR):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._kokoro = None

    def _load_settings(self) -> dict:
        from src.settings import load_settings

        saved = load_settings()
        return {
            "tts_enabled": saved.get("tts_enabled", True),
            "tts_provider": saved.get("tts_provider", "disabled"),
            "tts_model": saved.get("tts_model", "tts-1"),
            "tts_voice": saved.get("tts_voice", "alloy"),
            "tts_speed": saved.get("tts_speed", "1"),
        }

    @property
    def available(self) -> bool:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return False
        provider = settings["tts_provider"]
        if provider == "disabled":
            return False
        if provider == "browser":
            return True
        if provider == "local":
            kokoro = self._get_kokoro()
            return kokoro is not None and kokoro.available
        if provider.startswith("endpoint:"):
            return True
        return False

    def _cache_key(self, text: str, provider: str, model: str, voice: str, speed: float = 1.0) -> str:
        raw = f"{provider}|{model}|{voice}|{speed}|{text}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_cached(self, key: str) -> Optional[bytes]:
        for ext in (".mp3", ".wav"):
            path = self.cache_dir / f"{key}{ext}"
            if path.exists():
                return path.read_bytes()
        return None

    def _put_cache(self, key: str, data: bytes):
        ext = ".mp3" if (
            len(data) >= 3
            and (data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0))
        ) else ".wav"
        (self.cache_dir / f"{key}{ext}").write_bytes(data)

    def clear_cache(self):
        count = 0
        for file_path in self.cache_dir.glob("*.*"):
            file_path.unlink()
            count += 1
        logger.info("Cleared %s cached TTS files", count)

    def _get_kokoro(self):
        if self._kokoro is None:
            self._kokoro = _KokoroPipeline()
        return self._kokoro

    def _synthesize_api(self, text: str, endpoint_id: str, model: str, voice: str, speed: float = 1.0) -> Optional[bytes]:
        from src.database import SessionLocal, ModelEndpoint

        db = SessionLocal()
        try:
            endpoint = db.query(ModelEndpoint).filter(ModelEndpoint.id == endpoint_id).first()
            if not endpoint:
                logger.error("TTS endpoint %s not found", endpoint_id)
                return None
            base_url = endpoint.base_url.rstrip("/")
            api_key = endpoint.api_key
        finally:
            db.close()

        url = base_url + "/audio/speech"
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
            "speed": speed,
        }

        try:
            response = httpx.post(url, json=payload, headers=headers, timeout=60)
            response.raise_for_status()
            logger.info("API TTS: %s bytes from %s", len(response.content), base_url)
            return response.content
        except Exception as error:
            logger.error("API TTS synthesis failed: %s", error)
            return None

    def synthesize(self, text: str, use_cache: bool = True) -> Optional[bytes]:
        settings = self._load_settings()
        if settings.get("tts_enabled") is False:
            return None

        text = (text or "").strip()
        if not text:
            return None

        provider = settings["tts_provider"]
        model = settings["tts_model"]
        voice = settings["tts_voice"]
        speed = _safe_speed(settings.get("tts_speed", "1"))

        if provider in ("disabled", "browser"):
            return None

        # Do not silently truncate a model reply. Local Kokoro synthesis now
        # processes the complete text in three-sentence groups instead.
        if use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            cached = self._get_cached(key)
            if cached:
                logger.info("TTS cache hit (%s chars)", len(text))
                return cached

        if provider == "local":
            kokoro = self._get_kokoro()
            if not (kokoro and kokoro.available):
                logger.warning("Kokoro TTS not available")
                return None
            audio_data = kokoro.synthesize_raw(text, voice, speed)
        elif provider.startswith("endpoint:"):
            endpoint_id = provider.split(":", 1)[1]
            audio_data = self._synthesize_api(text, endpoint_id, model, voice, speed)
        else:
            logger.error("Unknown TTS provider: %s", provider)
            return None

        if audio_data and use_cache:
            key = self._cache_key(text, provider, model, voice, speed)
            self._put_cache(key, audio_data)

        return audio_data

    def synthesize_to_base64(self, text: str) -> Optional[str]:
        import base64

        audio = self.synthesize(text)
        if audio:
            return base64.b64encode(audio).decode("utf-8")
        return None

    def set_voice(self, voice: str):
        """Legacy no-op — voice is managed through admin settings."""

    def get_stats(self) -> Dict[str, Any]:
        settings = self._load_settings()
        provider = settings["tts_provider"]
        tts_enabled = settings.get("tts_enabled", True)

        cache_files = list(self.cache_dir.glob("*.wav")) + list(self.cache_dir.glob("*.mp3"))
        cache_size = sum(file_path.stat().st_size for file_path in cache_files)

        is_available = self.available and tts_enabled
        stats = {
            "available": is_available,
            "ready": is_available,
            "provider": provider,
            "model": settings["tts_model"],
            "voice": settings["tts_voice"],
            "speed": _safe_speed(settings.get("tts_speed", "1")),
            "cache_entries": len(cache_files),
            "cache_size_mb": round(cache_size / (1024 * 1024), 2),
        }

        if provider == "local":
            kokoro = self._get_kokoro()
            stats["model"] = "Kokoro-82M (GPU)" if (kokoro and kokoro.available) else "Kokoro (not loaded)"
        elif provider == "browser":
            stats["model"] = "Browser (Web Speech API)"
        elif provider.startswith("endpoint:"):
            stats["endpoint_id"] = provider.split(":", 1)[1]

        return stats


class _KokoroPipeline:
    """Encapsulates the Kokoro-82M local pipeline on CUDA, MPS, or CPU."""

    def __init__(self):
        self.pipeline = None
        self.available = False
        self.device = None
        self._init()

    def _init(self):
        try:
            import torch
            from kokoro import KPipeline

            if torch.cuda.is_available():
                self.device = torch.device("cuda:0")
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self.device = torch.device("mps")
            else:
                self.device = torch.device("cpu")

            self.pipeline = KPipeline(lang_code="a")
            if hasattr(self.pipeline, "model"):
                try:
                    self.pipeline.model = self.pipeline.model.to(self.device)
                except Exception as error:
                    if str(self.device) == "cpu":
                        raise
                    logger.warning("Kokoro could not move to %s; falling back to CPU: %s", self.device, error)
                    self.device = torch.device("cpu")
                    self.pipeline.model = self.pipeline.model.to(self.device)

            self.available = True
            logger.info("Kokoro-82M TTS pipeline loaded on %s", self.device)
        except ImportError as error:
            logger.warning("Kokoro TTS not available: %s", error)
            logger.warning("Install with: pip install kokoro soundfile")
        except Exception as error:
            logger.error("Kokoro init failed: %s", error, exc_info=True)

    def synthesize_raw(self, text: str, voice: str = "af_heart", speed: float = 1.0) -> Optional[bytes]:
        if not self.available:
            return None

        try:
            import numpy as np

            text_chunks = _split_text_for_local_tts(text)
            if not text_chunks:
                return None

            logger.info(
                "Local Kokoro TTS synthesizing %s chunk(s), %s chars",
                len(text_chunks),
                len(text),
            )

            waveform_chunks = []
            silence = np.zeros(
                int(LOCAL_TTS_SAMPLE_RATE * LOCAL_TTS_SILENCE_MS / 1000),
                dtype=np.float32,
            )

            for chunk_index, text_chunk in enumerate(text_chunks):
                generator = self.pipeline(
                    text_chunk,
                    voice=voice or "af_heart",
                    speed=speed,
                    split_pattern=r"\n+",
                )

                yielded_audio = False
                for _, _, audio in generator:
                    if hasattr(audio, "detach"):
                        audio = audio.detach().cpu().numpy()
                    audio = np.asarray(audio, dtype=np.float32)
                    if audio.size:
                        waveform_chunks.append(audio)
                        yielded_audio = True

                if yielded_audio and chunk_index < len(text_chunks) - 1:
                    waveform_chunks.append(silence)

            if not waveform_chunks:
                return None

            full = np.concatenate(waveform_chunks)
            full = np.clip(full, -1.0, 1.0)
            buf = io.BytesIO()
            with wave.open(buf, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(LOCAL_TTS_SAMPLE_RATE)
                wav_file.writeframes((full * 32767).astype(np.int16).tobytes())
            return buf.getvalue()
        except Exception as error:
            logger.error("Kokoro synthesis failed: %s", error, exc_info=True)
            return None


_tts_service = None


def get_tts_service() -> TTSService:
    global _tts_service
    if _tts_service is None:
        _tts_service = TTSService()
    return _tts_service
