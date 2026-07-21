"""Secure selfie image persistence (encrypted at rest when enabled)."""
from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def _root() -> Path:
    root = Path(settings.upload_dir) / "selfies"
    root.mkdir(parents=True, exist_ok=True)
    return root


def persist_selfie_bytes(driver_id: str, raw: bytes, prefix: str = "live") -> str:
    """Store selfie bytes; optionally encrypt. Returns relative storage key."""
    folder = _root() / str(driver_id)
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{prefix}_{hashlib.sha256(raw).hexdigest()[:16]}.bin"
    path = folder / name
    payload = _fernet().encrypt(raw) if settings.selfie_encrypt_at_rest else raw
    path.write_bytes(payload)
    return f"selfies/{driver_id}/{name}"


def load_selfie_bytes(storage_key: str) -> bytes | None:
    if not storage_key:
        return None
    path = Path(settings.upload_dir) / storage_key
    if not path.is_file():
        # Also support legacy /uploads absolute-style keys
        alt = Path(settings.upload_dir) / storage_key.lstrip("/")
        if alt.is_file():
            path = alt
        else:
            return None
    raw = path.read_bytes()
    if not settings.selfie_encrypt_at_rest:
        return raw
    try:
        return _fernet().decrypt(raw)
    except InvalidToken:
        # File may have been stored unencrypted before the flag was enabled.
        return raw


def delete_selfie_file(storage_key: str | None) -> bool:
    """Delete stored selfie blob from disk. Returns True if a file was removed."""
    if not storage_key:
        return False
    candidates = [
        Path(settings.upload_dir) / storage_key,
        Path(settings.upload_dir) / storage_key.lstrip("/"),
    ]
    removed = False
    for path in candidates:
        if path.is_file():
            try:
                path.unlink()
                removed = True
            except OSError:
                pass
    return removed


def resolve_registered_face_bytes(profile_photo: str | None) -> bytes | None:
    """Load the driver's registered face image from disk, data-URL, or HTTP(S)."""
    if not profile_photo:
        return None
    text = profile_photo.strip()

    # data:image/...;base64,... stored during registration
    if text.startswith("data:image"):
        import re
        import binascii

        match = re.match(
            r"^data:image/[a-zA-Z0-9.+-]+;base64,(.+)$",
            text,
            re.DOTALL,
        )
        if not match:
            return None
        try:
            raw = base64.b64decode(match.group(1), validate=False)
            return raw or None
        except (binascii.Error, ValueError):
            return None

    if text.startswith(("http://", "https://")):
        try:
            import httpx

            # Local uploads often served as http://localhost:8000/uploads/...
            response = httpx.get(text, timeout=15.0, follow_redirects=True)
            if response.status_code == 200 and response.content:
                return response.content
        except Exception:
            pass
        # Fallback: extract /uploads/... path from URL and read from disk
        marker = "/uploads/"
        if marker in text:
            rel = text.split(marker, 1)[1]
            path = Path(settings.upload_dir) / rel
            if path.is_file():
                return path.read_bytes()
        return None

    candidates: list[Path] = []
    rel = text
    if rel.startswith("/uploads/"):
        rel = rel.removeprefix("/uploads/").lstrip("/")
    candidates.append(Path(settings.upload_dir) / rel)
    candidates.append(Path(settings.upload_dir) / text.lstrip("/"))
    if not text.startswith("drivers/"):
        # Common registration layout
        candidates.append(Path(settings.upload_dir) / "drivers" / text.lstrip("/"))

    for path in candidates:
        if path.is_file():
            return path.read_bytes()
    return None
