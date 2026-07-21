"""Pydantic schemas for selfie verification & shift APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class LivenessClientResults(BaseModel):
    blink: bool | dict[str, Any] = True
    smile: bool | dict[str, Any] = True
    head_turn: bool | dict[str, Any] = True
    anti_spoof: bool | dict[str, Any] | None = True


class SelfieVerifyRequest(BaseModel):
    """Live camera selfie only — gallery uploads must not be accepted by clients."""

    selfie_base64: str = Field(..., min_length=100, description="data:image/...;base64,... or raw base64")
    challenge_id: str = Field(..., min_length=8)
    liveness: LivenessClientResults
    device_id: Optional[str] = None
    source: str = Field(default="live_camera", pattern="^live_camera$")


class SelfieVerifyResponse(BaseModel):
    verified: bool
    matched: bool
    confidence_score: Optional[float] = None
    liveness_passed: bool
    verification_id: Optional[UUID] = None
    error_code: Optional[str] = None
    message: str
    steps: dict[str, bool] = Field(default_factory=dict)


class LivenessChallengeResponse(BaseModel):
    challenge_id: str
    actions: list[str]
    expires_at: str


class ShiftResponse(BaseModel):
    shift_id: UUID
    driver_id: UUID
    started_at: datetime
    ended_at: Optional[datetime] = None
    status: str
    selfie_verified: bool
    selfie_verified_at: Optional[datetime] = None
    force_close_reason: Optional[str] = None


class VerificationStatusResponse(BaseModel):
    can_go_online: bool
    selfie_required: bool
    has_active_shift: bool
    active_shift: Optional[ShiftResponse] = None
    pending_verification_id: Optional[UUID] = None
    failed_attempts: int = 0
    locked_until: Optional[datetime] = None
    message: str


class GoOnlineResponse(BaseModel):
    status: str
    shift: ShiftResponse
    message: str


class GoOfflineResponse(BaseModel):
    status: str
    shift: Optional[ShiftResponse] = None
    message: str
