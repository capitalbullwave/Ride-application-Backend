"""Always-pass liveness provider for local development."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from app.selfie_verification.liveness.base import LivenessChallenge, LivenessProvider, LivenessResult


class MockLivenessProvider(LivenessProvider):
    name = "mock"

    def issue_challenge(self, driver_id: str) -> LivenessChallenge:
        return LivenessChallenge(
            challenge_id=f"mock-{uuid4().hex}",
            actions=["blink", "smile", "head_turn"],
            expires_at=(datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
        )

    async def verify(
        self,
        *,
        challenge_id: str,
        driver_id: str,
        client_results: dict[str, Any],
        live_selfie: bytes,
    ) -> LivenessResult:
        return LivenessResult(
            passed=True,
            provider=self.name,
            actions_passed=["blink", "smile", "head_turn", "anti_spoof"],
            anti_spoof_score=1.0,
            details={"mode": "mock"},
        )
