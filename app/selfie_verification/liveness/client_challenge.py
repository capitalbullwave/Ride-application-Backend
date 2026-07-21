"""Client-driven liveness: blink / smile / head turn + basic anti-spoof checks."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any

from app.core.config import settings
from app.selfie_verification.liveness.base import (
    SUPPORTED_CHALLENGES,
    LivenessChallenge,
    LivenessProvider,
    LivenessResult,
)

_CHALLENGE_TTL_SECONDS = 120


class ClientChallengeLivenessProvider(LivenessProvider):
    """Issues signed challenges the Flutter app must complete with the live camera."""

    name = "client_challenge"

    def issue_challenge(self, driver_id: str) -> LivenessChallenge:
        actions = ["blink", "smile", "head_turn"]
        nonce = secrets.token_hex(8)
        expires = int(time.time()) + _CHALLENGE_TTL_SECONDS
        payload = f"{driver_id}:{','.join(actions)}:{expires}:{nonce}"
        signature = hmac.new(
            settings.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]
        challenge_id = f"{expires}.{nonce}.{signature}"
        from datetime import datetime, timezone

        return LivenessChallenge(
            challenge_id=challenge_id,
            actions=actions,
            expires_at=datetime.fromtimestamp(expires, tz=timezone.utc).isoformat(),
        )

    def _validate_challenge(self, challenge_id: str, driver_id: str) -> tuple[bool, str | None]:
        try:
            expires_s, nonce, signature = challenge_id.split(".", 2)
            expires = int(expires_s)
        except (ValueError, AttributeError):
            return False, "Invalid liveness challenge."

        if expires < int(time.time()):
            return False, "Liveness challenge expired. Please try again."

        actions = ["blink", "smile", "head_turn"]
        payload = f"{driver_id}:{','.join(actions)}:{expires}:{nonce}"
        expected = hmac.new(
            settings.secret_key.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:24]
        if not hmac.compare_digest(expected, signature):
            return False, "Liveness challenge signature invalid."
        return True, None

    async def verify(
        self,
        *,
        challenge_id: str,
        driver_id: str,
        client_results: dict[str, Any],
        live_selfie: bytes,
    ) -> LivenessResult:
        ok, err = self._validate_challenge(challenge_id, str(driver_id))
        if not ok:
            return LivenessResult(
                passed=False,
                provider=self.name,
                error_code="CHALLENGE_INVALID",
                error_message=err,
            )

        if not isinstance(client_results, dict):
            return LivenessResult(
                passed=False,
                provider=self.name,
                error_code="LIVENESS_FAILED",
                error_message="Missing liveness results.",
            )

        required = ["blink", "smile", "head_turn"]
        passed: list[str] = []
        failed: list[str] = []
        for action in required:
            value = client_results.get(action)
            if value is True or (isinstance(value, dict) and value.get("passed") is True):
                passed.append(action)
            else:
                failed.append(action)

        anti_spoof = client_results.get("anti_spoof")
        anti_score: float | None = None
        if isinstance(anti_spoof, dict):
            anti_score = float(anti_spoof.get("score") or 0)
            if anti_spoof.get("passed") is True and (anti_score is None or anti_score >= 0.5):
                passed.append("anti_spoof")
            else:
                failed.append("anti_spoof")
        elif anti_spoof is True:
            passed.append("anti_spoof")
            anti_score = 1.0
        else:
            # Soft-require anti-spoof when client omitted it (older app builds).
            if len(live_selfie) < 2_000:
                failed.append("anti_spoof")
            else:
                passed.append("anti_spoof")
                anti_score = 0.7

        # Reject obviously non-image / tiny payloads as spoof risk.
        if len(live_selfie) < 1_500:
            return LivenessResult(
                passed=False,
                provider=self.name,
                actions_passed=passed,
                actions_failed=failed + ["anti_spoof"],
                anti_spoof_score=anti_score,
                error_code="POOR_LIGHTING",
                error_message="Selfie quality too low. Improve lighting and retake.",
            )

        success = len(failed) == 0 and set(required).issubset(set(passed))
        return LivenessResult(
            passed=success,
            provider=self.name,
            actions_passed=passed,
            actions_failed=failed,
            anti_spoof_score=anti_score,
            error_code=None if success else "LIVENESS_FAILED",
            error_message=None if success else "Liveness checks failed. Follow on-screen prompts.",
            details={"required": required, "supported": list(SUPPORTED_CHALLENGES)},
        )
