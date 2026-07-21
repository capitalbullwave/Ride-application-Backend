"""Pluggable face recognition providers."""

from app.selfie_verification.face.base import FaceMatchResult, FaceVerificationProvider
from app.selfie_verification.face.factory import get_face_provider

__all__ = ["FaceMatchResult", "FaceVerificationProvider", "get_face_provider"]
