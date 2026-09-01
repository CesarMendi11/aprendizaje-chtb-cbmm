from typing import TYPE_CHECKING

from .errors import *  # noqa: F403
from .ollama_structured_client import (
    OllamaStructuredGenerationClient,
    StructuredGenerationResponse,
)

if TYPE_CHECKING:
    from .screen_purpose_service import ScreenPurposeInferenceService

__all__ = [
    "OllamaStructuredGenerationClient",
    "ScreenPurposeInferenceService",
    "StructuredGenerationResponse",
]


def __getattr__(name: str):
    if name == "ScreenPurposeInferenceService":
        from .screen_purpose_service import ScreenPurposeInferenceService

        globals()[name] = ScreenPurposeInferenceService
        return ScreenPurposeInferenceService
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
