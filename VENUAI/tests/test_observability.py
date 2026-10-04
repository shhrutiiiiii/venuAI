"""Real tests for VENUAI/observability.py's request tracing."""

import logging
import time

from VENUAI.observability import RequestTrace, new_request_id


def test_request_id_is_unique():
    ids = {new_request_id() for _ in range(100)}
    assert len(ids) == 100


def test_stage_timer_records_elapsed_time():
    trace = RequestTrace(request_id="abc123", route="/query", user_id="u1")
    with trace.stage("retrieve"):
        time.sleep(0.01)
    assert "retrieve" in trace._stage_timings
    assert trace._stage_timings["retrieve"] >= 10  # ms


def test_stage_timer_does_not_swallow_exceptions():
    trace = RequestTrace(request_id="abc123", route="/query")
    try:
        with trace.stage("generate"):
            raise ValueError("boom")
    except ValueError:
        pass
    else:
        assert False, "exception should have propagated"
    assert "generate" in trace._stage_timings


def test_log_summary_does_not_raise(caplog):
    caplog.set_level(logging.INFO, logger="drdo-rag")
    trace = RequestTrace(request_id="abc123", route="/query", user_id="u1")
    with trace.stage("retrieve"):
        pass
    trace.log_summary(success=True, extra={"foo": "bar"})
    assert any("trace=abc123" in r.message for r in caplog.records)
