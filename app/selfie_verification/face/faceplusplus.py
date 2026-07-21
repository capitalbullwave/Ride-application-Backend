"""Face++ compare API provider."""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.selfie_verification.face.base import FaceMatchResult, FaceVerificationProvider


class FacePlusPlusProvider(FaceVerificationProvider):
    name = "facepp"

    async def verify_face(
        self,
        registered_image: bytes,
        live_selfie: bytes,
        *,
        threshold: float,
    ) -> FaceMatchResult:
        if not settings.facepp_api_key or not settings.facepp_api_secret:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="PROVIDER_NOT_CONFIGURED",
                error_message="Face++ API credentials are not configured.",
            )

        data = {
            "api_key": settings.facepp_api_key,
            "api_secret": settings.facepp_api_secret,
        }
        files = {
            "image_file1": ("registered.jpg", registered_image, "image/jpeg"),
            "image_file2": ("live.jpg", live_selfie, "image/jpeg"),
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(
                    "https://api-us.faceplusplus.com/facepp/v3/compare",
                    data=data,
                    files=files,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:  # noqa: BLE001
                return FaceMatchResult(
                    matched=False,
                    confidence=0.0,
                    provider=self.name,
                    error_code="PROVIDER_ERROR",
                    error_message=str(exc),
                )

        confidence = float(payload.get("confidence") or 0)
        return FaceMatchResult(
            matched=confidence >= threshold,
            confidence=round(confidence, 2),
            provider=self.name,
            details={"thresholds": payload.get("thresholds")},
        )
