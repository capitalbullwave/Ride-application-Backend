"""Dev/local face matcher — compares live selfie to registered profile photo.

Uses center-crop + aHash/dHash/pixel MAE. Histogram is intentionally NOT used
to inflate scores (skin-tone histograms look alike across different people).

Not a replacement for AWS/Azure/Face++ in production.
"""
from __future__ import annotations

import io
from typing import Sequence

from app.selfie_verification.face.base import FaceMatchResult, FaceVerificationProvider

# Same person (good lighting, facing camera) typically lands ~60–85.
# Different people usually stay below ~50 on the structural gate.
_MOCK_PASS_THRESHOLD = 62.0


def _open_rgb(data: bytes):
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data))
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def _normalize_face_frame(img):
    """Center-crop square; light inset to reduce background."""
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    cropped = img.crop((left, top, left + side, top + side))
    inset = int(side * 0.10)
    if inset > 0 and side - 2 * inset > 32:
        cropped = cropped.crop((inset, inset, side - inset, side - inset))
    # No autocontrast — it washes faces toward a common look and causes false matches.
    return cropped.resize((256, 256))


def _average_hash(img, hash_size: int = 24) -> str:
    gray = img.convert("L").resize((hash_size, hash_size))
    pixels: Sequence[int] = list(gray.getdata())
    avg = sum(pixels) / max(len(pixels), 1)
    return "".join("1" if p >= avg else "0" for p in pixels)


def _difference_hash(img, hash_size: int = 24) -> str:
    gray = img.convert("L").resize((hash_size + 1, hash_size))
    pixels = list(gray.getdata())
    bits: list[str] = []
    for row in range(hash_size):
        row_start = row * (hash_size + 1)
        for col in range(hash_size):
            left = pixels[row_start + col]
            right = pixels[row_start + col + 1]
            bits.append("1" if left > right else "0")
    return "".join(bits)


def _hamming_score(a: str, b: str) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dist = sum(x != y for x, y in zip(a, b))
    return max(0.0, (1.0 - dist / len(a)) * 100.0)


def _pixel_similarity(a, b) -> float:
    """Downscaled grayscale MAE → 0–100 score."""
    ga = a.convert("L").resize((96, 96))
    gb = b.convert("L").resize((96, 96))
    pa = list(ga.getdata())
    pb = list(gb.getdata())
    if not pa or len(pa) != len(pb):
        return 0.0
    mae = sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)
    # Stricter than before: MAE 0 → 100, MAE 55+ → ~0
    return max(0.0, min(100.0, (1.0 - mae / 55.0) * 100.0))


def _block_correlation(a, b, blocks: int = 4) -> float:
    """Mean per-block brightness correlation — penalizes mismatched face layout."""
    ga = a.convert("L").resize((blocks * 16, blocks * 16))
    gb = b.convert("L").resize((blocks * 16, blocks * 16))
    scores: list[float] = []
    bw = ga.width // blocks
    bh = ga.height // blocks
    for by in range(blocks):
        for bx in range(blocks):
            box = (bx * bw, by * bh, (bx + 1) * bw, (by + 1) * bh)
            pa = list(ga.crop(box).getdata())
            pb = list(gb.crop(box).getdata())
            if not pa:
                continue
            mae = sum(abs(x - y) for x, y in zip(pa, pb)) / len(pa)
            scores.append(max(0.0, min(100.0, (1.0 - mae / 50.0) * 100.0)))
    if not scores:
        return 0.0
    return sum(scores) / len(scores)


def _similarity(registered: bytes, live: bytes) -> tuple[float, dict]:
    try:
        img_a = _normalize_face_frame(_open_rgb(registered))
        img_b = _normalize_face_frame(_open_rgb(live))
    except Exception:
        return 0.0, {"error": "decode_failed"}

    ahash = _hamming_score(_average_hash(img_a), _average_hash(img_b))
    dhash = _hamming_score(_difference_hash(img_a), _difference_hash(img_b))
    pixels = _pixel_similarity(img_a, img_b)
    blocks = _block_correlation(img_a, img_b)

    structural = min(ahash, dhash)
    details = {
        "ahash": round(ahash, 2),
        "dhash": round(dhash, 2),
        "pixels": round(pixels, 2),
        "blocks": round(blocks, 2),
        "structural": round(structural, 2),
    }

    # Hard gate: different faces rarely agree on BOTH perceptual hashes.
    if ahash < 55.0 or dhash < 55.0:
        score = round(structural * 0.80, 2)
        details["gated"] = True
        return score, details

    if pixels < 40.0 or blocks < 40.0:
        score = round(min(structural, pixels, blocks) * 0.85, 2)
        details["gated"] = True
        return score, details

    # Both hashes agree — blend structure + appearance (no soft boost).
    score = ahash * 0.30 + dhash * 0.30 + pixels * 0.20 + blocks * 0.20
    # Cap by average hash so weak structure cannot be inflated.
    score = min(score, (ahash + dhash) / 2.0)
    details["gated"] = False
    return round(score, 2), details


class MockFaceProvider(FaceVerificationProvider):
    name = "mock"

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

        confidence, score_details = _similarity(registered_image, live_selfie)
        # Mock scores softer than cloud APIs; floor is still high enough to reject strangers.
        pass_at = min(float(threshold), _MOCK_PASS_THRESHOLD)
        matched = confidence >= pass_at
        return FaceMatchResult(
            matched=matched,
            confidence=confidence,
            provider=self.name,
            face_count=1,
            error_code=None if matched else "LOW_CONFIDENCE",
            error_message=None
            if matched
            else (
                "We could not confirm your identity from this selfie. "
                "Please face the camera clearly and try again."
            ),
            details={
                "mode": "strict_hash_blend",
                "pass_at": pass_at,
                "registered_bytes": len(registered_image),
                "live_bytes": len(live_selfie),
                **score_details,
            },
        )
