"""Resolve the configured face verification provider."""
from __future__ import annotations

from app.core.config import settings
from app.core.constants import FaceProvider
from app.selfie_verification.face.aws_rekognition import AwsRekognitionFaceProvider
from app.selfie_verification.face.azure_face import AzureFaceProvider
from app.selfie_verification.face.base import FaceVerificationProvider
from app.selfie_verification.face.deepface import DeepFaceProvider
from app.selfie_verification.face.faceplusplus import FacePlusPlusProvider
from app.selfie_verification.face.insightface import InsightFaceProvider
from app.selfie_verification.face.mock import MockFaceProvider

_PROVIDERS: dict[str, type[FaceVerificationProvider]] = {
    FaceProvider.MOCK.value: MockFaceProvider,
    FaceProvider.AWS_REKOGNITION.value: AwsRekognitionFaceProvider,
    FaceProvider.AZURE_FACE.value: AzureFaceProvider,
    FaceProvider.FACEPP.value: FacePlusPlusProvider,
    FaceProvider.INSIGHTFACE.value: InsightFaceProvider,
    FaceProvider.DEEPFACE.value: DeepFaceProvider,
}


def get_face_provider(name: str | None = None) -> FaceVerificationProvider:
    key = (name or settings.face_provider or FaceProvider.MOCK.value).lower().strip()
    cls = _PROVIDERS.get(key) or MockFaceProvider
    return cls()
