"""Azure Face API verify provider."""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.selfie_verification.face.base import FaceMatchResult, FaceVerificationProvider


class AzureFaceProvider(FaceVerificationProvider):
    name = "azure_face"

    async def verify_face(
        self,
        registered_image: bytes,
        live_selfie: bytes,
        *,
        threshold: float,
    ) -> FaceMatchResult:
        endpoint = (settings.azure_face_endpoint or "").rstrip("/")
        key = settings.azure_face_key
        if not endpoint or not key:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="PROVIDER_NOT_CONFIGURED",
                error_message="Azure Face endpoint/key not configured.",
            )

        headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/octet-stream",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                detect_url = f"{endpoint}/face/v1.0/detect?returnFaceId=true"
                reg = await client.post(detect_url, headers=headers, content=registered_image)
                live = await client.post(detect_url, headers=headers, content=live_selfie)
                reg.raise_for_status()
                live.raise_for_status()
                reg_faces = reg.json()
                live_faces = live.json()
            except Exception as exc:  # noqa: BLE001
                return FaceMatchResult(
                    matched=False,
                    confidence=0.0,
                    provider=self.name,
                    error_code="PROVIDER_ERROR",
                    error_message=str(exc),
                )

        if len(live_faces) > 1:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="MULTIPLE_FACES",
                error_message="Multiple faces detected. Only the driver should be in frame.",
                face_count=len(live_faces),
            )
        if not reg_faces or not live_faces:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="FACE_NOT_DETECTED",
                error_message="Face not detected.",
                face_count=len(live_faces),
            )

        face_id_1 = reg_faces[0]["faceId"]
        face_id_2 = live_faces[0]["faceId"]
        verify_headers = {
            "Ocp-Apim-Subscription-Key": key,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                verify = await client.post(
                    f"{endpoint}/face/v1.0/verify",
                    headers=verify_headers,
                    json={"faceId1": face_id_1, "faceId2": face_id_2},
                )
                verify.raise_for_status()
                payload = verify.json()
            except Exception as exc:  # noqa: BLE001
                return FaceMatchResult(
                    matched=False,
                    confidence=0.0,
                    provider=self.name,
                    error_code="PROVIDER_ERROR",
                    error_message=str(exc),
                )

        confidence = float(payload.get("confidence", 0)) * 100.0
        is_identical = bool(payload.get("isIdentical"))
        return FaceMatchResult(
            matched=is_identical and confidence >= threshold,
            confidence=round(confidence, 2),
            provider=self.name,
            face_count=1,
            details=payload,
        )
