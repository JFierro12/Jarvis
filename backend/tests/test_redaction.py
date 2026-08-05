from app.security.redaction import redact


def test_bearer_token_is_redacted():
    result = redact("Authorization: Bearer abc123.def456-ghi")
    assert "abc123" not in result
    assert "[REDACTED]" in result


def test_api_key_is_redacted():
    result = redact("api_key: sk-1234567890abcdef")
    assert "sk-1234567890abcdef" not in result


def test_normal_text_is_unaffected():
    text = "Your next event is Data Structures at 2:30 PM."
    assert redact(text) == text
