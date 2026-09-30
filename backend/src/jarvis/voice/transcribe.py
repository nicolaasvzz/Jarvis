"""Speech-to-text, on this machine.

Transcription runs locally through ``faster-whisper``. That is a deliberate
choice rather than an implementation detail: an always-listening microphone
streaming to somebody else's server is a different product from one that
does not, and Jarvis should not quietly be the first.

The model is loaded once, on the first utterance, and reused. Loading takes
seconds and occupies GPU memory, so paying that cost at import time — on a
machine that may never use the microphone — would be wasteful. Decoding
itself is blocking C++, so it runs on a worker thread.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import Any

from jarvis.config.schema import VoiceConfig
from jarvis.core.errors import VoiceError
from jarvis.logging import get_logger
from jarvis.voice.base import Transcript
from jarvis.voice.wake import match_wake_word

_log = get_logger(__name__)

#: Markers that mean "the GPU cannot run this", as opposed to "the audio was
#: bad". CUDA's runtime libraries (cuBLAS, cuDNN) ship separately from the
#: driver and are frequently absent, and the failure is reported as a
#: missing DLL rather than anything CUDA-shaped.
_GPU_FAILURE_MARKERS = (
    "cublas",
    "cudnn",
    "cuda",
    "cannot be loaded",
    "is not found",
    "no kernel image",
    "out of memory",
)


class _GpuUnavailable(Exception):
    """Internal signal: retry this on the CPU."""


def _is_gpu_failure(exc: Exception) -> bool:
    detail = str(exc).lower()
    return any(marker in detail for marker in _GPU_FAILURE_MARKERS)


class WhisperTranscriber:
    """Local speech recognition via faster-whisper."""

    def __init__(self, config: VoiceConfig) -> None:
        self._config = config
        self._model: Any | None = None
        # One model, one loader: concurrent first requests must not each
        # start their own multi-second load into the same GPU.
        self._lock = asyncio.Lock()
        # Set to "cpu" after a GPU failure, so the fallback is remembered
        # rather than rediscovered on every utterance.
        self._forced_device: str | None = None

    @property
    def model_name(self) -> str:
        return self._config.stt_model

    @property
    def available(self) -> bool:
        from jarvis.voice.synth import installed

        return installed("faster_whisper")

    async def transcribe(self, audio: bytes, *, suffix: str = ".webm") -> Transcript:
        if not audio:
            raise VoiceError("The recording was empty.")
        model = await self._ensure_model()
        try:
            text, details = await asyncio.to_thread(self._run, model, audio, suffix)
        except _GpuUnavailable as exc:
            # The model loaded on the GPU but could not actually run there —
            # the CUDA runtime libraries (cuBLAS, cuDNN) are a separate
            # install from the driver, and are often missing. This only
            # surfaces on the first real inference, never at load time, so
            # it has to be recovered from here rather than in _load().
            _log.warning(
                "GPU transcription unavailable; falling back to CPU",
                extra={"error": str(exc.__cause__ or exc)},
            )
            async with self._lock:
                self._forced_device = "cpu"
                self._model = None
            model = await self._ensure_model()
            text, details = await asyncio.to_thread(self._run, model, audio, suffix)
        match = match_wake_word(
            text,
            self._config.wake_word,
            required=self._config.wake_word_required,
        )
        return Transcript(
            text=text,
            addressed=match.addressed,
            command=match.command,
            language=str(details.get("language") or "") or None,
            confidence=_as_confidence(details.get("language_probability")),
            details={"wake_score": round(match.score, 3), **details},
        )

    async def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model
        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(self._load)
        return self._model

    def _load(self) -> Any:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - depends on install
            raise VoiceError(
                "Listening needs the faster-whisper package. Install it with: "
                'pip install "jarvis-assistant[listen]" — or set '
                "voice.stt_provider: none to use the dashboard without a mic."
            ) from exc

        device, compute_type = _resolve_device(
            self._forced_device or self._config.stt_device,
            self._config.stt_compute_type,
        )
        _log.info(
            "loading speech model",
            extra={
                "model": self._config.stt_model,
                "device": device,
                "compute_type": compute_type,
            },
        )
        try:
            return WhisperModel(
                self._config.stt_model, device=device, compute_type=compute_type
            )
        except Exception as exc:  # noqa: BLE001 - CUDA/driver failures vary
            if device == "cuda":
                _log.warning(
                    "GPU speech model failed to load; falling back to CPU",
                    extra={"error": str(exc)},
                )
                try:
                    return WhisperModel(
                        self._config.stt_model, device="cpu", compute_type="int8"
                    )
                except Exception as cpu_exc:  # noqa: BLE001
                    raise VoiceError(_friendly_load_error(cpu_exc)) from cpu_exc
            raise VoiceError(_friendly_load_error(exc)) from exc

    def _run(self, model: Any, audio: bytes, suffix: str) -> tuple[str, dict[str, Any]]:
        """Decode one clip. Runs on a worker thread."""
        # faster-whisper reads from a path or file object and decodes the
        # container itself, so browser webm/opus needs no conversion here.
        with tempfile.TemporaryDirectory() as folder:
            clip = Path(folder) / f"utterance{suffix}"
            clip.write_bytes(audio)
            try:
                segments, info = model.transcribe(
                    str(clip),
                    beam_size=1,
                    language="en",
                    vad_filter=True,
                    condition_on_previous_text=False,
                )
                # faster-whisper returns a lazy generator, so decoding only
                # actually happens here — which is where a broken GPU
                # runtime finally shows itself.
                text = "".join(segment.text for segment in segments).strip()
            except Exception as exc:  # noqa: BLE001 - decode failures vary
                if _is_gpu_failure(exc):
                    raise _GpuUnavailable from exc
                raise VoiceError(_friendly_decode_error(exc)) from exc

        return text, {
            "language": getattr(info, "language", None),
            "language_probability": getattr(info, "language_probability", None),
            "duration": round(float(getattr(info, "duration", 0.0) or 0.0), 2),
        }


def _resolve_device(device: str, compute_type: str) -> tuple[str, str]:
    """Turn the ``auto`` settings into a concrete device and precision."""
    resolved = device
    if resolved == "auto":
        resolved = "cuda" if _cuda_available() else "cpu"
    if compute_type != "auto":
        return resolved, compute_type
    # float16 is the fast path on any CUDA card; int8 keeps CPU usable.
    return resolved, "float16" if resolved == "cuda" else "int8"


def _cuda_available() -> bool:
    """Detect a usable GPU without importing a deep-learning framework.

    ``ctranslate2`` is already a faster-whisper dependency and answers this
    directly, so nothing heavier needs to be pulled in just to choose a
    device.
    """
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count()) > 0
    except Exception:  # noqa: BLE001 - no CUDA, no ctranslate2, no problem
        return False


def _as_confidence(value: object) -> float | None:
    try:
        return round(float(value), 3)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _friendly_load_error(exc: Exception) -> str:
    detail = str(exc)
    lowered = detail.lower()
    if "out of memory" in lowered or "cuda" in lowered:
        return (
            "The speech model would not fit on the GPU. Try a smaller "
            "voice.stt_model (base.en), or set voice.stt_device: cpu."
        )
    if "connection" in lowered or "download" in lowered or "resolve" in lowered:
        return (
            "The speech model could not be downloaded. It is fetched once on "
            "first use and needs an internet connection for that download."
        )
    return f"Could not load the speech model: {detail}"


def _friendly_decode_error(exc: Exception) -> str:
    lowered = str(exc).lower()
    if "ffmpeg" in lowered or "av" in lowered or "codec" in lowered:
        return (
            "The recording could not be decoded. This usually means the audio "
            "backend (PyAV/FFmpeg) is missing from the faster-whisper install."
        )
    return f"Could not transcribe the recording: {exc}"


def build_transcriber(config: VoiceConfig) -> WhisperTranscriber | None:
    """Pick a transcriber from config, or ``None`` when listening is off."""
    if not config.enabled or not config.listen_enabled:
        return None
    if config.stt_provider == "none":
        return None
    return WhisperTranscriber(config)
