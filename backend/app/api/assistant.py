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
from app.services.reasoning import get_reasoning_provider
from app.services.speech import get_speech_provider, mock_transcribe
from app.services.vision import VisionUnavailableError, get_vision_provider

logger = logging.getLogger("jarvis.assistant")
router = APIRouter()


@router.post("/v1/transcribe", response_model=TranscribeResponse)
def transcribe(payload: TranscribeRequest, _token: str = Depends(require_bearer_token)) -> TranscribeResponse:
    text = mock_transcribe(payload.audio_base64)
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid audio payload")
    return TranscribeResponse(text=text, confidence=0.9)


@router.post("/v1/reason", response_model=ReasonResponse)
def reason(
    payload: ReasonRequest,
    _token: str = Depends(require_bearer_token),
    settings: Settings = Depends(get_settings),
) -> ReasonResponse:
    provider = get_reasoning_provider(settings.reasoning_provider)
    logger.info(
        "reason() provider=%s question=%r available_tools=%r context_items=%d",
        type(provider).__name__, payload.question, payload.available_tools, len(payload.context),
    )
    try:
        result = provider.reason(payload.context, payload.question, payload.available_tools)
        logger.info("reason() -> spoken_answer=%r proposed_tool_call=%r", result.spoken_answer, result.proposed_tool_call)
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
    try:
        audio = provider.synthesize(payload.text, payload.voice_id)
    except SpeechSynthesisUnavailableError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audio_format = "mp3" if settings.tts_provider == "elevenlabs" else "wav"
    return SpeechSynthesizeResponse(audio_base64=base64.b64encode(audio).decode("ascii"), format=audio_format)
