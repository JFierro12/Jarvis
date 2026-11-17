import base64
import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import Settings, get_settings
from app.core.security import require_bearer_token
from app.models.schemas import (
    ReasonRequest,
    ReasonResponse,
    SpeechSynthesizeRequest,
    SpeechSynthesizeResponse,
    TranscribeRequest,
    TranscribeResponse,
    VisionAnalyzeRequest,
    VisionAnalyzeResponse,
)
from app.services.anthropic_reasoning import LanguageReasoningUnavailableError
from app.services.elevenlabs_speech import SpeechSynthesisUnavailableError
from app.services.local_speech import LocalTranscriptionError, get_local_transcriber
from app.services.reasoning import UnknownContextSourceError, get_reasoning_provider, validate_context_sources
from app.services.speech import get_speech_provider, mock_transcribe
from app.services.vision import VisionUnavailableError, get_vision_provider

logger = logging.getLogger("jarvis.assistant")
router = APIRouter()


@router.post("/v1/transcribe", response_model=TranscribeResponse)
async def transcribe(
    payload: TranscribeRequest,
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> TranscribeResponse:
    if settings.stt_provider != "local":
        if settings.live_mode:
            raise HTTPException(status_code=503, detail="Configure a real transcription provider")
        text = mock_transcribe(payload.audio_base64)
        if not text:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid audio payload")
        return TranscribeResponse(text=text, confidence=0.9)

    try:
        audio_bytes = base64.b64decode(payload.audio_base64, validate=True)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid audio payload") from exc
    if not audio_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty audio payload")

    transcriber = get_local_transcriber(settings.whisper_model, settings.whisper_device, settings.whisper_compute_type)
    try:
        text = await transcriber.transcribe(audio_bytes)
    except LocalTranscriptionError as exc:
        logger.info("transcribe() -> unavailable: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Silence (or audio the VAD filtered away entirely) is a normal outcome
    # for the wake-word path, not an error — an empty string lets the caller
    # simply move on to the next chunk.
    return TranscribeResponse(text=text, confidence=1.0 if text else 0.0)


@router.post("/v1/reason", response_model=ReasonResponse)
def reason(
    payload: ReasonRequest,
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> ReasonResponse:
    # Reject unrecognized provenance rather than letting an unknown label
    # slip past the untrusted-source handling.
    try:
        validate_context_sources(payload.context)
    except UnknownContextSourceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    provider = get_reasoning_provider(settings.reasoning_provider)
    if settings.live_mode and settings.reasoning_provider not in {"local", "anthropic"}:
        raise HTTPException(status_code=503, detail="Configure a real reasoning provider")
    logger.info(
        "reason() provider=%s context_items=%d",
        type(provider).__name__, len(payload.context),
    )
    try:
        result = provider.reason(payload.context, payload.question, payload.available_tools)
        return result
    except LanguageReasoningUnavailableError as exc:
        logger.info("reason() -> unavailable: %s", exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/v1/vision/analyze", response_model=VisionAnalyzeResponse)
def analyze_vision(
    payload: VisionAnalyzeRequest,
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> VisionAnalyzeResponse:
    provider = get_vision_provider(settings.vision_provider)
    if settings.live_mode and settings.vision_provider == "mock":
        raise HTTPException(status_code=503, detail="Vision is not configured")
    try:
        return provider.analyze(payload.image_base64, payload.question)
    except VisionUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/v1/speech/synthesize", response_model=SpeechSynthesizeResponse)
def synthesize_speech(
    payload: SpeechSynthesizeRequest,
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> SpeechSynthesizeResponse:
    if not payload.text.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="text must not be empty")
    provider = get_speech_provider(settings.tts_provider)
    if settings.live_mode and settings.tts_provider == "mock":
        raise HTTPException(status_code=503, detail="Use device speech or configure a real speech provider")
    try:
        audio = provider.synthesize(payload.text, payload.voice_id)
    except SpeechSynthesisUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audio_format = "mp3" if settings.tts_provider == "elevenlabs" else "wav"
    return SpeechSynthesizeResponse(audio_base64=base64.b64encode(audio).decode("ascii"), format=audio_format)
