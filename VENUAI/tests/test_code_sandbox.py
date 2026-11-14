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


_needs_docker = pytest.mark.skipif(not _docker_available(), reason="No reachable Docker daemon")


@_needs_docker
def test_runs_real_python_code():
    result = code_sandbox.run_python("print('hello'); print(2 + 2)")
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert "4" in result.stdout
    assert not result.timed_out


@_needs_docker
def test_captures_real_stderr_on_error():
    result = code_sandbox.run_python("raise ValueError('boom')")
    assert result.exit_code != 0
    assert "ValueError" in result.stderr
    assert "boom" in result.stderr


@_needs_docker
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


@_needs_docker
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


@_needs_docker
def test_output_is_truncated_not_unbounded():
    result = code_sandbox.run_python("print('x' * 100000)")
    assert len(result.stdout) <= code_sandbox.MAX_OUTPUT_CHARS


@_needs_docker
def test_is_available_returns_true_when_docker_reachable():
    assert code_sandbox.is_available() is True


@_needs_docker
def test_sandbox_unavailable_error_when_docker_missing(monkeypatch):
    monkeypatch.setattr(code_sandbox, "is_available", lambda: False)
    with pytest.raises(code_sandbox.SandboxUnavailableError):
        code_sandbox.run_python("print('should not run')")


# ── Hardening (S3) ────────────────────────────────────────────────────────────

def test_sandbox_cmd_has_hardening_flags():
    cmd = code_sandbox.build_docker_cmd("c1")
    joined = " ".join(cmd)
    for flag in ["--network none", "--cap-drop ALL", "--user 65534:65534", "--read-only", "--pids-limit",
                 "--memory-swap", "--ipc none", "--pull never", "no-new-privileges", "--cpus"]:
        assert flag in joined, flag
    assert "-v" not in cmd and "--volume" not in cmd and "--privileged" not in cmd
    assert code_sandbox.SANDBOX_IMAGE and ":" in code_sandbox.SANDBOX_IMAGE or "@sha256:" in code_sandbox.SANDBOX_IMAGE


@_needs_docker
def test_runs_as_nobody_and_cannot_write_rootfs_or_connect():
    r = code_sandbox.run_python(
        "import os,socket\nprint(os.getuid())\n"
        "try:\n open('/etc/x','w')\nexcept Exception as e: print('ro')\n"
        "try:\n socket.create_connection(('1.1.1.1',80),2)\nexcept Exception as e: print('nonet')\n")
    assert "65534" in r.stdout and "ro" in r.stdout and "nonet" in r.stdout


@_needs_docker
def test_output_flood_is_truncated():
    r = code_sandbox.run_python("while True: print('A'*1000)", timeout_seconds=5)
    assert len(r.stdout) <= code_sandbox.MAX_OUTPUT_CHARS


def test_per_user_concurrency_limit(monkeypatch):
    monkeypatch.setattr(code_sandbox, "_approval_hook", None)
    monkeypatch.setattr(code_sandbox, "is_available", lambda: True)
    monkeypatch.setitem(code_sandbox._running_by_user, "u1", code_sandbox.MAX_CONCURRENT_PER_USER)
    with pytest.raises(code_sandbox.SandboxBusyError):
        code_sandbox.run_python("print(1)", user_id="u1")
    code_sandbox._running_by_user.pop("u1", None)


def test_no_runtime_pull_by_default(monkeypatch):
    calls = []
    monkeypatch.delenv("SANDBOX_AUTO_PULL", raising=False)

    class R:
        returncode = 1

    monkeypatch.setattr(code_sandbox.subprocess, "run", lambda cmd, **k: (calls.append(cmd), R())[1])
    assert code_sandbox.ensure_image_pulled() is False
    assert not any("pull" in c for c in calls)
