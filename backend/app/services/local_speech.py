import asyncio
import io
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional


class LocalTranscriptionError(Exception):
    pass


class LocalWhisperTranscriber:
    """Local speech-to-text via faster-whisper — no network, no cloud STT.

    Replaces the browser's Web Speech API path, which routed every
    utterance through Google's cloud service (the source of the recurring
    "network" recognition errors) and only worked inside a browser engine
    that would grant it a microphone.

    Model loading and transcription both run on one dedicated worker
    thread: a WhisperModel isn't safe to drive from several threads at
    once, and serializing costs nothing here because utterances arrive one
    at a time anyway.
    """

    def __init__(self, model_size: str, device: str, compute_type: str) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._model = None
        self._load_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

    def _ensure_model(self):
        # First call pays a one-time load (~2s for base.en on CPU) and, on a
        # cold machine, the model download. Deliberately lazy so importing
        # this module — or running the backend with STT disabled — never
        # pulls weights or holds memory it isn't using.
        with self._load_lock:
            if self._model is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise LocalTranscriptionError("faster-whisper is not installed") from exc

                # Prefer the already-downloaded model: the default path
                # pings huggingface.co to check for a newer revision, which
                # is a pointless stall (or timeout) on a machine that's
                # offline — the whole point of local STT. Falls through to a
                # normal download only when nothing is cached yet.
                try:
                    self._model = WhisperModel(
                        self._model_size,
                        device=self._device,
                        compute_type=self._compute_type,
                        local_files_only=True,
                    )
                    return self._model
                except Exception:
                    pass

                try:
                    self._model = WhisperModel(self._model_size, device=self._device, compute_type=self._compute_type)
                except Exception as exc:
                    raise LocalTranscriptionError(
                        f"Could not load Whisper model '{self._model_size}' on {self._device}: {exc}"
                    ) from exc
            return self._model

    def _transcribe_blocking(self, audio_bytes: bytes) -> str:
        model = self._ensure_model()
        try:
            # vad_filter drops silence before decoding, which matters most
            # for the wake-word path: most captured chunks are ambient room
            # noise, and skipping them keeps latency down.
            segments, _info = model.transcribe(io.BytesIO(audio_bytes), beam_size=1, vad_filter=True)
            return " ".join(segment.text for segment in segments).strip()
        except Exception as exc:
            raise LocalTranscriptionError(f"Transcription failed: {exc}") from exc

    async def transcribe(self, audio_bytes: bytes) -> str:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._transcribe_blocking, audio_bytes)


_transcriber: Optional[LocalWhisperTranscriber] = None
_transcriber_lock = threading.Lock()


def get_local_transcriber(model_size: str, device: str, compute_type: str) -> LocalWhisperTranscriber:
    global _transcriber
    with _transcriber_lock:
        if _transcriber is None:
            _transcriber = LocalWhisperTranscriber(model_size, device, compute_type)
        return _transcriber
