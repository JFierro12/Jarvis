import re

_PATTERNS = [
    re.compile(r"(?i)(bearer)\s+[a-z0-9._-]+"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*[:=]\s*\S+"),
]


def redact(text: str) -> str:
    """Used before writing anything to logs. Never log raw tokens or full
    request bodies that might contain images/secrets (spec 25/29)."""
    result = text
    for pattern in _PATTERNS:
        result = pattern.sub(r"\1 [REDACTED]", result)
    return result
