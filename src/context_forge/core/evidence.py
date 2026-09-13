from __future__ import annotations

import re

SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|secret|token|password|auth|authorization)\s*[:=]\s*['\"]?[\w\-\.]{8,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]+?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),                              # AWS access key ID
    re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b"),          # GitHub Tokens
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}\b"),                          # OpenAI / Anthropic / Generic sk- API key
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9_\-\.]{16,}\b"),              # Bearer Authorization headers
    re.compile(r"://([^:\s/]+):([^@\s/]+)@"),                          # URL with embedded credentials
]


def screen_secrets(text: str) -> str:
    """Scrub sensitive credentials and keys from any text that may be stored or sent to LLMs."""
    out = text or ""
    for pat in SECRET_PATTERNS:
        if pat.pattern.startswith("://"):
            out = pat.sub(r"://\1:[REDACTED]@", out)
        elif "bearer" in pat.pattern.lower():
            out = pat.sub("Bearer [REDACTED]", out)
        else:
            out = pat.sub("[REDACTED]", out)
    return out


def sanitize_evidence(value: str, label: str = "evidence") -> str:
    """Validate and sanitize evidence strings. Rejects secrets rather than silently storing redacting placeholders."""
    cleaned = screen_secrets(value).strip()
    if not cleaned:
        raise ValueError(f"{label} must not be empty")
    if "[REDACTED]" in cleaned:
        raise ValueError(f"{label} appears to contain a secret; write a sanitized record instead")
    return cleaned
