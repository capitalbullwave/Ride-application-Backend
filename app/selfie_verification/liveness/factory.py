"""Resolve the configured liveness provider."""
from __future__ import annotations

from app.core.config import settings
from app.core.constants import LivenessProvider as LivenessProviderName
from app.selfie_verification.liveness.base import LivenessProvider
from app.selfie_verification.liveness.client_challenge import ClientChallengeLivenessProvider
from app.selfie_verification.liveness.instant import InstantCaptureLivenessProvider
from app.selfie_verification.liveness.mock import MockLivenessProvider

_PROVIDERS: dict[str, type[LivenessProvider]] = {
    LivenessProviderName.MOCK.value: MockLivenessProvider,
    LivenessProviderName.CLIENT_CHALLENGE.value: ClientChallengeLivenessProvider,
    LivenessProviderName.INSTANT_CAPTURE.value: InstantCaptureLivenessProvider,
    LivenessProviderName.AWS_REKOGNITION.value: ClientChallengeLivenessProvider,
    LivenessProviderName.AZURE_FACE.value: ClientChallengeLivenessProvider,
}


def get_liveness_provider(name: str | None = None) -> LivenessProvider:
    key = (name or settings.liveness_provider or LivenessProviderName.INSTANT_CAPTURE.value).lower()
    cls = _PROVIDERS.get(key) or InstantCaptureLivenessProvider
    return cls()
