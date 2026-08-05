import base64
import binascii
from typing import Protocol

from app.models.schemas import VisionAnalyzeResponse


class VisionReasoningProvider(Protocol):
    def analyze(self, image_base64: str, question: str) -> VisionAnalyzeResponse: ...


class VisionUnavailableError(Exception):
    pass


class MockVisionReasoningProvider:
    """Deterministic vision responses for demo mode / offline dev.

    Never overstates certainty, never invents precise instructions from an
    unclear image — matches the contract in
    ios/JarvisKit/Sources/JarvisKit/Mocks/MockVisionReasoningProvider.swift.
    """

    def analyze(self, image_base64: str, question: str) -> VisionAnalyzeResponse:
        try:
            data = base64.b64decode(image_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise VisionUnavailableError("image could not be decoded") from exc

        if len(data) == 0:
            raise VisionUnavailableError("empty image")

        lowered = question.lower()
        if "read" in lowered or "error" in lowered:
            return VisionAnalyzeResponse(
                answer="I can see some text, but it's too small to read reliably in this image.",
                confidence=0.4,
                detected_text=None,
                uncertainty_note="Text legibility could not be confirmed.",
                suggested_follow_up="Try moving closer or improving lighting.",
            )

        return VisionAnalyzeResponse(
            answer="That looks like a laptop on a desk, with a coffee mug to its left.",
            confidence=0.82,
        )


def get_vision_provider(provider_name: str) -> VisionReasoningProvider:
    if provider_name == "anthropic":
        from app.core.config import get_settings
        from app.services.anthropic_vision import AnthropicVisionReasoningProvider

        return AnthropicVisionReasoningProvider(get_settings())
    return MockVisionReasoningProvider()
