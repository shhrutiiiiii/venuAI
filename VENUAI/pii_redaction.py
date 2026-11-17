"""PII / secret redaction (Wave B contract 2.3; FROZEN signatures ``redact`` / ``redact_obj``).

Used for log lines, trace storage and guardrail output redaction. Deterministic, regex plus
checksum validation (so random digit strings are not mangled):

  * e-mail addresses                      -> [EMAIL]
  * Aadhaar numbers (12 digits, Verhoeff)  -> [AADHAAR]
  * payment card numbers (13-19, Luhn)     -> [CARD]
  * PAN (ABCDE1234F)                       -> [PAN]
  * Indian / international phone numbers   -> [PHONE]
  * API keys / bearer tokens / JWTs / PEM  -> [API_KEY]
  * ``key=value`` secrets (password=...)   -> value masked

Classification markings (SECRET, TOP SECRET, RESTRICTED, CONFIDENTIAL, UNCLASSIFIED) are labels,
not personal data: they are deliberately left untouched so a redacted trace still shows how a
document was marked. Never raises: on any internal error the text is returned fully masked
rather than leaked.
"""
from __future__ import annotations

import re
from typing import Any

EMAIL_RE = re.compile(r"(?<![\w.+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# 12 digits, optionally grouped 4-4-4 by space or hyphen; first digit 2-9 (UIDAI rule).
AADHAAR_RE = re.compile(r"(?<![\d-])[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}(?![\d-])")
CARD_RE = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
PAN_RE = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
PHONE_RE = re.compile(
    r"(?<![\w.])(?:\+91[ -]?|91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?![\w.])"      # Indian mobile
    r"|(?<![\w.])\+\d{1,3}[ -]?\(?\d{2,4}\)?[ -]?\d{3,4}[ -]?\d{3,4}(?![\w.])"  # +CC international
)
API_KEY_RES = [
    re.compile(r"\b(?:gsk|sk|pk|rk)[-_][A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
]
KV_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|token)\b(\s*[=:]\s*)"
    r"(?!\[)(\"[^\"\n]{3,}\"|'[^'\n]{3,}'|[^\s,;\"']{3,})"
)
_SENSITIVE_KEYS = re.compile(r"(?i)(password|passwd|secret|api[_-]?key|token|authorization|cookie|credential)")

_VERHOEFF_D = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 2, 3, 4, 0, 6, 7, 8, 9, 5), (2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
    (3, 4, 0, 1, 2, 8, 9, 5, 6, 7), (4, 0, 1, 2, 3, 9, 5, 6, 7, 8), (5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
    (6, 5, 9, 8, 7, 1, 0, 4, 3, 2), (7, 6, 5, 9, 8, 2, 1, 0, 4, 3), (8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
    (9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)
_VERHOEFF_P = (
    (0, 1, 2, 3, 4, 5, 6, 7, 8, 9), (1, 5, 7, 6, 2, 8, 3, 0, 9, 4), (5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
    (8, 9, 1, 6, 0, 4, 3, 5, 2, 7), (9, 4, 5, 3, 1, 2, 6, 8, 7, 0), (4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
    (2, 7, 9, 3, 8, 0, 6, 4, 1, 5), (7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)


def verhoeff_valid(number: str) -> bool:
    c = 0
    for i, ch in enumerate(reversed(number)):
        if not ch.isdigit():
            return False
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


def luhn_valid(number: str) -> bool:
    digits = [int(c) for c in number if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _redact_unsafe(text: str) -> str:
    for rx in API_KEY_RES:
        text = rx.sub("[API_KEY]", text)
    text = KV_SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)
    text = EMAIL_RE.sub("[EMAIL]", text)
    # Cards before Aadhaar: a Luhn-valid 16-digit run must not be split by the 12-digit pattern.
    text = CARD_RE.sub(lambda m: "[CARD]" if luhn_valid(_digits(m.group(0))) else m.group(0), text)
    text = AADHAAR_RE.sub(lambda m: "[AADHAAR]" if verhoeff_valid(_digits(m.group(0))) else m.group(0), text)
    text = PAN_RE.sub("[PAN]", text)
    text = PHONE_RE.sub(lambda m: "[PHONE]" if 10 <= len(_digits(m.group(0))) <= 15 else m.group(0), text)
    return text


def redact_identifiers(text: str) -> str:
    """Narrow variant for ANSWERS shown to users: masks government/financial identifiers and secrets
    (Aadhaar, card, PAN, API keys) but keeps e-mails/phones, which are often legitimate public contact
    details in DRDO documents. Logs and traces use the full ``redact``."""
    if not isinstance(text, str) or not text:
        return text
    try:
        for rx in API_KEY_RES:
            text = rx.sub("[API_KEY]", text)
        text = CARD_RE.sub(lambda m: "[CARD]" if luhn_valid(_digits(m.group(0))) else m.group(0), text)
        text = AADHAAR_RE.sub(lambda m: "[AADHAAR]" if verhoeff_valid(_digits(m.group(0))) else m.group(0), text)
        return PAN_RE.sub("[PAN]", text)
    except Exception:  # noqa: BLE001
        return "[REDACTED: redaction error]"


def redact(text: str) -> str:
    """Return ``text`` with PII and secrets masked. Non-strings are returned unchanged."""
    if not isinstance(text, str) or not text:
        return text
    try:
        return _redact_unsafe(text)
    except Exception:  # noqa: BLE001 - never leak on failure
        return "[REDACTED: redaction error]"


def redact_obj(obj: Any) -> Any:
    """Recursively redact every string in a JSON-like object (dict/list/tuple/str); returns a copy.
    Values under sensitive-looking keys (password, token, api_key, ...) are fully masked."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(k, str) and _SENSITIVE_KEYS.search(k) and isinstance(v, (str, bytes)) and v:
                out[k] = "[REDACTED]"
            else:
                out[k] = redact_obj(v)
        return out
    if isinstance(obj, list):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, tuple):
        return tuple(redact_obj(v) for v in obj)
    return obj
