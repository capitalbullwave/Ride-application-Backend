"""Face verification provider interface — do not hardcode a vendor."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class FaceMatchResult:
    matched: bool
    confidence: float
    provider: str
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    face_count: int = 1
    details: Optional[dict] = None


class FaceVerificationProvider(ABC):
    """Pluggable face comparison contract.

    Implementations: AWS Rekognition, Azure Face, Face++, InsightFace, DeepFace, Mock.
    """

    name: str

    @abstractmethod
    async def verify_face(
        self,
        registered_image: bytes,
        live_selfie: bytes,
        *,
        threshold: float,
    ) -> FaceMatchResult:
        """Compare registered profile face with a live selfie.

        Returns confidence score (0–100) and matched true/false.
        """
