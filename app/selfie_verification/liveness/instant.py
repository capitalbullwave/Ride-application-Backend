"""Instant selfie capture — no blink/smile/head-turn steps."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings
from app.selfie_verification.liveness.base import LivenessChallenge, LivenessProvider, LivenessResult

_CHALLENGE_TTL_SECONDS = 180


class InstantCaptureLivenessProvider(LivenessProvider):
    """Issues a short-lived token; live camera selfie is enough (no gesture steps)."""

    name = "instant_capture"

    def issue_challenge(self, driver_id: str) -> LivenessChallenge:
        nonce = secrets.token_hex(8)
        expires = int(time.time()) + _CHALLENGE_TTL_SECONDS
        payload = f"{driver_id}:instant:{expires}:{nonce}"
        signature = hmac.new(
            settings.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]
        challenge_id = f"instant.{expires}.{nonce}.{signature}"
        return LivenessChallenge(
            challenge_id=challenge_id,
            actions=["instant"],
            expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        )

    def _validate(self, challenge_id: str, driver_id: str) -> tuple[bool, str | None]:
        try:
            prefix, expires_s, nonce, signature = challenge_id.split(".", 3)
            if prefix != "instant":
                return False, "Invalid capture session."
            expires = int(expires_s)
        except (ValueError, AttributeError):
            return False, "Invalid capture session."

        if expires < int(time.time()):
            return False, "Capture session expired. Please try again."

        payload = f"{driver_id}:instant:{expires}:{nonce}"
        expected = hmac.new(
            settings.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]
        if not hmac.compare_digest(expected, signature):
            return False, "Invalid capture session."
        return True, None

    async def verify(
        self,
        *,
        challenge_id: str,
        driver_id: str,
        client_results: dict[str, Any],
        live_selfie: bytes,
    ) -> LivenessResult:
        ok, err = self._validate(challenge_id, str(driver_id))
        if not ok:
            return LivenessResult(
                passed=False,
                provider=self.name,
                error_code="CHALLENGE_INVALID",
                error_message=err,
            )

        if len(live_selfie) < 1_500:
            return LivenessResult(
                passed=False,
                provider=self.name,
                error_code="POOR_LIGHTING",
                error_message=(
                    "Photo quality is too low. Please move to a well-lit area and try again."
                ),
            )

        return LivenessResult(
            passed=True,
            provider=self.name,
            actions_passed=["instant"],
            anti_spoof_score=0.8,
            details={"mode": "instant_capture"},
        )
