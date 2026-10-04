"""
Real tests for VENUAI/code_sandbox.py — Phase 5's sandboxed code
execution. Skipped (not faked) when Docker isn't reachable on the test
host, same pattern as the Postgres-backend tests in
test_knowledge_graph_store.py. When Docker IS available, these run real
containers — no mocking of subprocess/docker.
"""

import pytest

from VENUAI import code_sandbox


def _docker_available() -> bool:
    return code_sandbox.is_available()


pytestmark = pytest.mark.skipif(not _docker_available(), reason="No reachable Docker daemon")


def test_runs_real_python_code():
    result = code_sandbox.run_python("print('hello'); print(2 + 2)")
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert "4" in result.stdout
    assert not result.timed_out


def test_captures_real_stderr_on_error():
    result = code_sandbox.run_python("raise ValueError('boom')")
    assert result.exit_code != 0
    assert "ValueError" in result.stderr
    assert "boom" in result.stderr


def test_network_is_genuinely_disabled():
    """Not a configuration check — an actual attempted connection from
    inside the container, which must fail."""
    result = code_sandbox.run_python(
        "import socket\n"
        "socket.setdefaulttimeout(3)\n"
        "socket.create_connection(('8.8.8.8', 53))\n"
    )
    assert result.exit_code != 0
    assert "unreachable" in result.stderr.lower() or "network" in result.stderr.lower()


def test_infinite_loop_times_out_and_leaves_no_running_container():
    import subprocess

    result = code_sandbox.run_python("while True:\n    pass\n", timeout_seconds=2)
    assert result.timed_out is True
    assert result.exit_code is None

    # Regression check for a real leak found during development: killing
    # the `docker run` client process does NOT stop the container
    # server-side — only an explicit `docker kill` does. Confirm no
    # venuai-sandbox-* container is left running.
    ps = subprocess.run(
        ["docker", "ps", "--filter", "name=venuai-sandbox-", "--format", "{{.Names}}"],
        capture_output=True, text=True, timeout=10,
    )
    assert ps.stdout.strip() == ""


def test_output_is_truncated_not_unbounded():
    result = code_sandbox.run_python("print('x' * 100000)")
    assert len(result.stdout) <= code_sandbox.MAX_OUTPUT_CHARS


def test_is_available_returns_true_when_docker_reachable():
    assert code_sandbox.is_available() is True


def test_sandbox_unavailable_error_when_docker_missing(monkeypatch):
    monkeypatch.setattr(code_sandbox, "is_available", lambda: False)
    with pytest.raises(code_sandbox.SandboxUnavailableError):
        code_sandbox.run_python("print('should not run')")
