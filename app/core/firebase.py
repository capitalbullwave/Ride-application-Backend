"""Firebase Admin SDK singleton initialization."""
from __future__ import annotations

from pathlib import Path
from threading import Lock

import firebase_admin
from firebase_admin import credentials

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_lock = Lock()
_initialized = False


def _candidate_credential_paths() -> list[Path]:
    """Resolve credential file from env path and common project locations."""
    backend_root = Path(__file__).resolve().parents[2]
    repo_root = backend_root.parent
    configured = Path(settings.firebase_credentials_path)
    candidates = [
        configured,
        Path.cwd() / configured,
        backend_root / configured.name,
        backend_root / "app" / "serviceAccountKey.json",
        backend_root / "serviceAccountKey.json",
        backend_root / "firebase-credentials.json",
        backend_root / "app" / "firebase-credentials.json",
        repo_root / "firebase-credentials.json",
    ]
    # Preserve order, drop duplicates
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def initialize_firebase() -> bool:
    """Initialize Firebase Admin once. Returns True when ready for messaging."""
    global _initialized
    if _initialized or firebase_admin._apps:
        _initialized = True
        return True

    with _lock:
        if _initialized or firebase_admin._apps:
            _initialized = True
            return True

        credential_path: Path | None = None
        for path in _candidate_credential_paths():
            if path.is_file():
                credential_path = path
                break

        if credential_path is None:
            logger.warning(
                "firebase_credentials_missing",
                searched=[str(p) for p in _candidate_credential_paths()[:6]],
                hint="Place serviceAccountKey.json at Backend/app/serviceAccountKey.json "
                "or set FIREBASE_CREDENTIALS_PATH",
            )
            return False

        try:
            cred = credentials.Certificate(str(credential_path))
            firebase_admin.initialize_app(cred)
            _initialized = True
            logger.info(
                "firebase_admin_initialized",
                credentials_path=str(credential_path),
            )
            return True
        except Exception as exc:
            logger.error(
                "firebase_admin_init_failed",
                error=str(exc),
                credentials_path=str(credential_path),
            )
            return False


def is_firebase_ready() -> bool:
    return bool(firebase_admin._apps) or _initialized
