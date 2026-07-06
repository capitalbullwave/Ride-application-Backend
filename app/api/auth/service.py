"""Auth module service — wraps core AuthService."""
from app.auth.service import AuthService


class AuthApiService(AuthService):
    """Thin alias for domain auth service."""


__all__ = ["AuthApiService"]
