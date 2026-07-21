"""AWS Rekognition CompareFaces provider."""
from __future__ import annotations

from app.core.config import settings
from app.selfie_verification.face.base import FaceMatchResult, FaceVerificationProvider


class AwsRekognitionFaceProvider(FaceVerificationProvider):
    name = "aws_rekognition"

    async def verify_face(
        self,
        registered_image: bytes,
        live_selfie: bytes,
        *,
        threshold: float,
    ) -> FaceMatchResult:
        try:
            import boto3  # type: ignore
        except ImportError:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="PROVIDER_UNAVAILABLE",
                error_message="boto3 is not installed.",
            )

        if not settings.aws_access_key_id or not settings.aws_secret_access_key:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="PROVIDER_NOT_CONFIGURED",
                error_message="AWS credentials are not configured.",
            )

        client = boto3.client(
            "rekognition",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
        try:
            response = client.compare_faces(
                SourceImage={"Bytes": registered_image},
                TargetImage={"Bytes": live_selfie},
                SimilarityThreshold=float(threshold),
            )
        except Exception as exc:  # noqa: BLE001 — surface provider errors cleanly
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="PROVIDER_ERROR",
                error_message=str(exc),
            )

        matches = response.get("FaceMatches") or []
        unmatched = response.get("UnmatchedFaces") or []
        if not matches and unmatched:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="LOW_CONFIDENCE",
                error_message=(
                    "We could not confirm your identity from this selfie. "
                    "Please face the camera clearly and try again."
                ),
                face_count=len(unmatched),
            )
        if not matches:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="FACE_NOT_DETECTED",
                error_message="Face not detected.",
            )

        best = max(float(m.get("Similarity", 0)) for m in matches)
        return FaceMatchResult(
            matched=best >= threshold,
            confidence=round(best, 2),
            provider=self.name,
            face_count=len(matches),
            details={"face_matches": len(matches)},
        )
