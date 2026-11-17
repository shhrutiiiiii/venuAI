"""JWT secret policy shared by auth (boot-time refusal) and config.validate_config (S1 + S4)."""
from typing import Optional

_WEAK_SECRET_MARKERS = ("changeme", "generate", "your-secret", "your_secret", "placeholder", "example")
_WEAK_SECRETS = {"secret", "password", "default", "jwt_secret", "jwtsecret", "test", "dev"}


def validate_jwt_secret(secret: Optional[str]) -> str:
    """Return the stripped secret or raise RuntimeError. The server refuses to
    boot with a missing, short (<32 bytes), placeholder or low-entropy secret."""
    s = (secret or "").strip()
    if not s:
        raise RuntimeError(
            "JWT_SECRET_KEY not set. Generate one with `python -c \"import secrets; "
            "print(secrets.token_urlsafe(48))\"` and put it in your .env — the "
            "server refuses to start with an insecure/default secret."
        )
    low = s.lower()
    if (
        len(s.encode()) < 32
        or low.startswith("<")
        or low in _WEAK_SECRETS
        or any(m in low for m in _WEAK_SECRET_MARKERS)
        or len(set(s)) < 8
    ):
        raise RuntimeError(
            "JWT_SECRET_KEY is weak or a placeholder (need >= 32 bytes of real randomness). "
            "Generate one with `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`."
        )
    return s
