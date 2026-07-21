"""Liveness detection provider interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


SUPPORTED_CHALLENGES = ("blink", "smile", "head_turn", "anti_spoof")


@dataclass(frozen=True)
class LivenessChallenge:
    challenge_id: str
    actions: list[str]
    expires_at: str


@dataclass(frozen=True)
class LivenessResult:
    passed: bool
    provider: str
    actions_passed: list[str] = field(default_factory=list)
    actions_failed: list[str] = field(default_factory=list)
    anti_spoof_score: Optional[float] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    details: Optional[dict[str, Any]] = None


class LivenessProvider(ABC):
    name: str

    @abstractmethod
    def issue_challenge(self, driver_id: str) -> LivenessChallenge:
        """Issue a short-lived challenge set for the client to perform."""

    @abstractmethod
    async def verify(
        self,
        *,
        challenge_id: str,
        driver_id: str,
        client_results: dict[str, Any],
        live_selfie: bytes,
    ) -> LivenessResult:
        """Validate client challenge results and optional anti-spoof signals."""
