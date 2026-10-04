"""
Real tests for VENUAI/observability.py's structured JSON logging (Phase 5).
Plain-text remains the default; LOG_FORMAT=json opts into the structured
formatter. Not mocked — a real logging.LogRecord is formatted and the
actual JSON output is parsed back.
"""

import json
import logging

from VENUAI.observability import JSONLogFormatter, configure_logging


def _make_record(msg, extra=None):
    record = logging.LogRecord(
        name="drdo-rag", level=logging.INFO, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


def test_json_formatter_produces_valid_parseable_json():
    formatter = JSONLogFormatter()
    record = _make_record("hello world")
    output = formatter.format(record)

    parsed = json.loads(output)  # raises if not real JSON
    assert parsed["message"] == "hello world"
    assert parsed["level"] == "INFO"
    assert parsed["logger"] == "drdo-rag"
    assert "timestamp" in parsed


def test_json_formatter_includes_extra_fields():
    formatter = JSONLogFormatter()
    record = _make_record("request completed", extra={"request_id": "abc123", "user_id": "u1", "chunks_retrieved": 4})
    parsed = json.loads(formatter.format(record))

    assert parsed["request_id"] == "abc123"
    assert parsed["user_id"] == "u1"
    assert parsed["chunks_retrieved"] == 4


def test_json_formatter_never_crashes_on_unserializable_extra():
    """An extra field that isn't JSON-serializable (e.g. an arbitrary
    object) must be stringified, not raise and lose the whole log line."""
    class _Unserializable:
        def __str__(self):
            return "<unserializable>"

    formatter = JSONLogFormatter()
    record = _make_record("weird extra", extra={"weird": _Unserializable()})
    output = formatter.format(record)  # must not raise

    parsed = json.loads(output)
    assert parsed["weird"] == "<unserializable>"


def test_configure_logging_defaults_to_plain_text(monkeypatch, capsys):
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    configure_logging()
    logging.getLogger("drdo-rag").info("plain text check")

    captured = capsys.readouterr()
    assert "plain text check" in captured.err
    try:
        json.loads(captured.err.strip().splitlines()[-1])
        assert False, "default output should not be JSON"
    except json.JSONDecodeError:
        pass


def test_configure_logging_json_mode_produces_real_json_lines(monkeypatch, capsys):
    monkeypatch.setenv("LOG_FORMAT", "json")
    configure_logging()
    logging.getLogger("drdo-rag").info("json mode check")

    captured = capsys.readouterr()
    last_line = captured.err.strip().splitlines()[-1]
    parsed = json.loads(last_line)  # must be real, parseable JSON
    assert parsed["message"] == "json mode check"
