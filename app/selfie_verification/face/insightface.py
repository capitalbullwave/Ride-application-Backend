"""InsightFace-style face recognition for selfie verify.

Primary: `insightface` package (buffalo_l / ArcFace) when installed.
Fallback (Windows-friendly): OpenCV YuNet + SFace (ArcFace-family ONNX).
No silent mock fallback — wrong faces must not match.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import urllib.request
from pathlib import Path
from typing import Any

from app.selfie_verification.face.base import FaceMatchResult, FaceVerificationProvider

logger = logging.getLogger(__name__)

_MODELS_DIR = Path.home() / ".wavego" / "face_models"
_YUNET_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
_SFACE_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx"
)

_lock = threading.Lock()
_insight_app: Any = None
_opencv_bundle: tuple[Any, Any, Any, Any] | None = None  # cv2, np, detector, recognizer
_insight_import_error: str | None = None


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 10_000:
        return dest
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    logger.info("Downloading face model %s → %s", url, dest)
    urllib.request.urlretrieve(url, tmp)  # noqa: S310 — trusted OpenCV zoo URLs
    tmp.replace(dest)
    return dest


def _try_load_insightface():
    global _insight_app, _insight_import_error
    if _insight_app is not None:
        return _insight_app
    with _lock:
        if _insight_app is not None:
            return _insight_app
        try:
            from insightface.app import FaceAnalysis  # type: ignore
        except ImportError as exc:
            _insight_import_error = str(exc)
            return None
        logger.info("Loading InsightFace buffalo_l…")
        app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        app.prepare(ctx_id=-1, det_size=(640, 640))
        _insight_app = app
        logger.info("InsightFace buffalo_l ready")
        return _insight_app


def _load_opencv_sface():
    global _opencv_bundle
    if _opencv_bundle is not None:
        return _opencv_bundle
    with _lock:
        if _opencv_bundle is not None:
            return _opencv_bundle
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        yunet = _download(_YUNET_URL, _MODELS_DIR / "face_detection_yunet_2023mar.onnx")
        sface = _download(_SFACE_URL, _MODELS_DIR / "face_recognition_sface_2021dec.onnx")
        detector = cv2.FaceDetectorYN.create(str(yunet), "", (320, 320), 0.7, 0.3)
        recognizer = cv2.FaceRecognizerSF.create(str(sface), "")
        _opencv_bundle = (cv2, np, detector, recognizer)
        logger.info("OpenCV YuNet+SFace ready (InsightFace-compatible ArcFace)")
        return _opencv_bundle


def get_face_analysis():
    """Warmup helper used by app lifespan."""
    app = _try_load_insightface()
    if app is not None:
        return app
    return _load_opencv_sface()


def _decode_bgr(raw: bytes, cv2, np):
    arr = np.frombuffer(raw, dtype=np.uint8)
    return cv2.imdecode(arr, cv2.IMREAD_COLOR)


def _largest_face(faces: list):
    if not faces:
        return None

    def _area(face) -> float:
        bbox = getattr(face, "bbox", None)
        if bbox is None or len(bbox) < 4:
            return 0.0
        return float(max(0.0, bbox[2] - bbox[0]) * max(0.0, bbox[3] - bbox[1]))

    return max(faces, key=_area)


def _compare_insightface(
    registered_image: bytes,
    live_selfie: bytes,
    *,
    threshold: float,
    provider_name: str,
    app,
) -> FaceMatchResult:
    import numpy as np  # type: ignore
    import cv2  # type: ignore

    reg_img = _decode_bgr(registered_image, cv2, np)
    live_img = _decode_bgr(live_selfie, cv2, np)
    if reg_img is None or live_img is None:
        return FaceMatchResult(
            matched=False,
            confidence=0.0,
            provider=provider_name,
            error_code="FACE_NOT_DETECTED",
            error_message="Unable to decode image.",
            details={"engine": "insightface_buffalo_l"},
        )

    reg_faces = app.get(reg_img)
    live_faces = app.get(live_img)
    reg_face = _largest_face(reg_faces)
    live_face = _largest_face(live_faces)
    if live_face is not None and len(live_faces) > 1:
        primary_area = float(
            max(0.0, live_face.bbox[2] - live_face.bbox[0])
            * max(0.0, live_face.bbox[3] - live_face.bbox[1])
        )
        others = [
            f
            for f in live_faces
            if f is not live_face
        ]
        for other in others:
            area = float(
                max(0.0, other.bbox[2] - other.bbox[0])
                * max(0.0, other.bbox[3] - other.bbox[1])
            )
            if primary_area > 0 and area / primary_area >= 0.55:
                return FaceMatchResult(
                    matched=False,
                    confidence=0.0,
                    provider=provider_name,
                    error_code="MULTIPLE_FACES",
                    error_message="Multiple faces detected. Only you should be in the frame.",
                    face_count=len(live_faces),
                    details={"engine": "insightface_buffalo_l"},
                )
    if reg_face is None or live_face is None:
        return FaceMatchResult(
            matched=False,
            confidence=0.0,
            provider=provider_name,
            error_code="FACE_NOT_DETECTED",
            error_message="We could not detect a clear face. Centre your face and try again.",
            face_count=len(live_faces),
            details={"engine": "insightface_buffalo_l"},
        )

    emb1 = reg_face.normed_embedding
    emb2 = live_face.normed_embedding
    cosine = float(np.dot(emb1, emb2))
    confidence = max(0.0, min(100.0, (cosine + 1.0) * 50.0))
    matched = confidence >= threshold
    return FaceMatchResult(
        matched=matched,
        confidence=round(confidence, 2),
        provider=provider_name,
        face_count=1,
        error_code=None if matched else "LOW_CONFIDENCE",
        error_message=None
        if matched
        else "We could not confirm your identity from this selfie. Please try again.",
        details={
            "engine": "insightface_buffalo_l",
            "model": "buffalo_l",
            "cosine": round(cosine, 4),
            "threshold": threshold,
        },
    )


def _detect_faces_opencv(detector, image, cv2):
    h, w = image.shape[:2]
    detector.setInputSize((w, h))
    _, faces = detector.detect(image)
    if faces is None:
        return []
    return faces


def _compare_opencv_sface(
    registered_image: bytes,
    live_selfie: bytes,
    *,
    threshold: float,
    provider_name: str,
) -> FaceMatchResult:
    cv2, np, detector, recognizer = _load_opencv_sface()
    reg_img = _decode_bgr(registered_image, cv2, np)
    live_img = _decode_bgr(live_selfie, cv2, np)
    if reg_img is None or live_img is None:
        return FaceMatchResult(
            matched=False,
            confidence=0.0,
            provider=provider_name,
            error_code="FACE_NOT_DETECTED",
            error_message="Unable to decode image.",
            details={"engine": "opencv_sface"},
        )

    reg_faces = _detect_faces_opencv(detector, reg_img, cv2)
    live_faces = _detect_faces_opencv(detector, live_img, cv2)

    if len(reg_faces) < 1 or len(live_faces) < 1:
        return FaceMatchResult(
            matched=False,
            confidence=0.0,
            provider=provider_name,
            error_code="FACE_NOT_DETECTED",
            error_message="We could not detect a clear face. Centre your face and try again.",
            face_count=len(live_faces),
            details={"engine": "opencv_sface"},
        )

    # Largest face by box area (x,y,w,h,… in YuNet output).
    # Extra tiny detections (mirrors / posters / false positives) are ignored
    # unless a second face is nearly as large as the primary.
    def _area(f) -> float:
        return float(f[2]) * float(f[3])

    def _pick(faces):
        return max(faces, key=_area)

    live_sorted = sorted(live_faces, key=_area, reverse=True)
    live_face = live_sorted[0]
    if len(live_sorted) > 1:
        secondary_ratio = _area(live_sorted[1]) / max(_area(live_face), 1.0)
        if secondary_ratio >= 0.55:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=provider_name,
                error_code="MULTIPLE_FACES",
                error_message="Multiple faces detected. Only you should be in the frame.",
                face_count=len(live_faces),
                details={
                    "engine": "opencv_sface",
                    "secondary_ratio": round(secondary_ratio, 3),
                },
            )

    reg_face = _pick(reg_faces)

    reg_align = recognizer.alignCrop(reg_img, reg_face)
    live_align = recognizer.alignCrop(live_img, live_face)
    reg_feat = recognizer.feature(reg_align)
    live_feat = recognizer.feature(live_align)

    # FR_COSINE: higher = more similar; OpenCV recommends ~0.363 for same person.
    cosine = float(
        recognizer.match(reg_feat, live_feat, cv2.FaceRecognizerSF_FR_COSINE)
    )
    # Map OpenCV cosine → 0–100 (0.363 ≈ 68). Same-person often lands 60–85.
    confidence = max(0.0, min(100.0, (cosine / 0.363) * 68.0))
    # Pass if either our scaled score clears threshold OR raw cosine clears OpenCV's bar.
    matched = confidence >= threshold or cosine >= 0.363
    return FaceMatchResult(
        matched=matched,
        confidence=round(confidence, 2),
        provider=provider_name,
        face_count=len(live_faces),
        error_code=None if matched else "LOW_CONFIDENCE",
        error_message=None
        if matched
        else "We could not confirm your identity from this selfie. Please try again.",
        details={
            "engine": "opencv_sface",
            "model": "yunet+sface",
            "cosine": round(cosine, 4),
            "opencv_cosine_threshold": 0.363,
            "threshold": threshold,
            "live_face_count": len(live_faces),
            "note": (
                "insightface pip package not installed; "
                "using OpenCV ArcFace SFace. "
                "Install Visual C++ Build Tools + `pip install insightface` for buffalo_l."
            ),
        },
    )


def _compare_sync(
    registered_image: bytes,
    live_selfie: bytes,
    *,
    threshold: float,
    provider_name: str,
) -> FaceMatchResult:
    app = _try_load_insightface()
    if app is not None:
        return _compare_insightface(
            registered_image,
            live_selfie,
            threshold=threshold,
            provider_name=provider_name,
            app=app,
        )
    try:
        return _compare_opencv_sface(
            registered_image,
            live_selfie,
            threshold=threshold,
            provider_name=provider_name,
        )
    except Exception as exc:
        logger.exception("face_engine_failed")
        return FaceMatchResult(
            matched=False,
            confidence=0.0,
            provider=provider_name,
            error_code="PROVIDER_UNAVAILABLE",
            error_message=(
                "Face recognition engine failed to start. "
                "Install: pip install opencv-python-headless onnxruntime numpy. "
                f"Detail: {exc}"
            ),
            details={
                "insightface_error": _insight_import_error,
                "error": str(exc),
            },
        )


class InsightFaceProvider(FaceVerificationProvider):
    name = "insightface"

    async def verify_face(
        self,
        registered_image: bytes,
        live_selfie: bytes,
        *,
        threshold: float,
    ) -> FaceMatchResult:
        if not registered_image:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="NO_REGISTERED_FACE",
                error_message=(
                    "Your account does not have a registered profile photo yet. "
                    "Please complete your profile photo and try again."
                ),
            )
        if not live_selfie or len(live_selfie) < 1_000:
            return FaceMatchResult(
                matched=False,
                confidence=0.0,
                provider=self.name,
                error_code="FACE_NOT_DETECTED",
                error_message=(
                    "We could not detect a clear face in the photo. "
                    "Centre your face in the frame and try again."
                ),
            )

        return await asyncio.to_thread(
            _compare_sync,
            registered_image,
            live_selfie,
            threshold=threshold,
            provider_name=self.name,
        )
