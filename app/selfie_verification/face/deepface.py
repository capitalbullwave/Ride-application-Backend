"""DeepFace verify provider (optional dependency)."""
from __future__ import annotations

import tempfile
from pathlib import Path

from app.selfie_verification.face.base import FaceMatchResult, FaceVerificationProvider
from app.selfie_verification.face.mock import MockFaceProvider


class DeepFaceProvider(FaceVerificationProvider):
    name = "deepface"

    async def verify_face(
        self,
        registered_image: bytes,
        live_selfie: bytes,
        *,
        threshold: float,
    ) -> FaceMatchResult:
        try:
            from deepface import DeepFace  # type: ignore
        except ImportError:
            mock = await MockFaceProvider().verify_face(
                registered_image, live_selfie, threshold=threshold
            )
            return FaceMatchResult(
                matched=mock.matched,
                confidence=mock.confidence,
                provider=self.name,
                error_code=mock.error_code or "PROVIDER_FALLBACK",
                error_message=mock.error_message or "DeepFace not installed; used fallback matcher.",
                details={"fallback": "mock"},
            )

        with tempfile.TemporaryDirectory() as tmp:
            reg_path = Path(tmp) / "registered.jpg"
            live_path = Path(tmp) / "live.jpg"
            reg_path.write_bytes(registered_image)
            live_path.write_bytes(live_selfie)
            try:
                result = DeepFace.verify(
                    img1_path=str(reg_path),
                    img2_path=str(live_path),
                    enforce_detection=True,
                )
            except Exception as exc:  # noqa: BLE001
                message = str(exc).lower()
                code = "FACE_NOT_DETECTED"
                if "more than one" in message or "multiple" in message:
                    code = "MULTIPLE_FACES"
                return FaceMatchResult(
                    matched=False,
                    confidence=0.0,
                    provider=self.name,
                    error_code=code,
                    error_message=str(exc),
                )

        distance = float(result.get("distance") or 1.0)
        # Convert common distance metrics into a 0–100 confidence estimate.
        confidence = max(0.0, min(100.0, (1.0 - distance) * 100.0))
        verified = bool(result.get("verified"))
        return FaceMatchResult(
            matched=verified and confidence >= threshold,
            confidence=round(confidence, 2),
            provider=self.name,
            details={"distance": distance, "model": result.get("model")},
        )
