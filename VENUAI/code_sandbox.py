"""
Sandboxed code execution (Phase 5) — spec's "real sandbox runtime" item.

This is a genuinely isolated execution path, not a fake/simulated one:
code runs inside an ephemeral Docker container (`docker run --rm`), with
no network access, a read-only root filesystem, a memory/pids ceiling,
and a wall-clock timeout — then the container is destroyed. Requires a
real, running Docker daemon; see `is_available()`. If Docker is not
reachable, every call fails loud with `SandboxUnavailableError`, never a
silently-faked "result."

This module does NOT attempt userspace sandboxing (no seccomp/gVisor/
Firecracker-level guarantees) — Docker's default container isolation plus
the flags below (no network, read-only, capped memory/pids, no new
privileges) is the real, achievable isolation level in this environment.
A production deployment wanting stronger isolation (gVisor, Firecracker,
a dedicated execution service like E2B/Modal) is a genuine infrastructure
upgrade on top of this, not something fabricable without that
infrastructure actually existing — see Documents/AI/code-sandbox.md
"Not Yet Implemented".
"""

import logging
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger("drdo-rag")

# Pin by digest in production: SANDBOX_IMAGE=python@sha256:<digest>. The explicit tag is the
# fallback; the image is NEVER pulled at request time (see ensure_image_pulled / SANDBOX_AUTO_PULL).
SANDBOX_IMAGE = os.getenv("SANDBOX_IMAGE", "python:3.12-slim")
DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARS = 8000
MEMORY_LIMIT = "256m"
PIDS_LIMIT = "64"
SANDBOX_USER = "65534:65534"        # nobody:nogroup
MAX_CONCURRENT_PER_USER = int(os.getenv("SANDBOX_MAX_CONCURRENT_PER_USER", "2"))
MAX_STDIN_CHARS = 100_000

_running_lock = threading.Lock()
_running_by_user: dict = {}


class SandboxBusyError(RuntimeError):
    """Per-user concurrency limit reached."""


def build_docker_cmd(container_name: str) -> list:
    """The hardened `docker run` command line (separate so tests can assert the flags)."""
    return [
        "docker", "run", "--rm", "-i",
        "--name", container_name,
        "--pull", "never",                       # no runtime pull; image must be provisioned at deploy time
        "--network", "none",
        "--memory", MEMORY_LIMIT,
        "--memory-swap", MEMORY_LIMIT,           # no swap
        "--pids-limit", PIDS_LIMIT,
        "--cpus", "0.5",
        "--cap-drop", "ALL",
        "--user", SANDBOX_USER,
        "--ipc", "none",
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
        "--security-opt", "no-new-privileges",
        "--ulimit", "nofile=64:64",
        "--ulimit", "fsize=1048576:1048576",
        "--ulimit", "nproc=64:64",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-e", "HOME=/tmp",
        SANDBOX_IMAGE, "python", "-",
    ]


class SandboxApprovalDenied(RuntimeError):
    """The registered approval hook refused this execution (see set_approval_hook)."""


_approval_hook: Optional[Callable[[str], bool]] = None


def set_approval_hook(hook: Optional[Callable[[str], bool]]) -> None:
    """Optional human-in-the-loop gate (Wave B / B3): `hook(code) -> bool`. Default None = no gate."""
    global _approval_hook
    _approval_hook = hook


class SandboxUnavailableError(RuntimeError):
    """Raised when Docker itself isn't reachable — never caught and
    silently papered over with a fake result."""


@dataclass(frozen=True)
class SandboxResult:
    stdout: str
    stderr: str
    exit_code: Optional[int]
    timed_out: bool
    duration_seconds: float


def is_available() -> bool:
    """True only if a real Docker daemon actually responds — checked
    fresh each call (no cached assumption), since the daemon can stop
    between requests in a real deployment."""
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return False


def _run_bounded(cmd: list, code: str, timeout: float, container_name: str):
    """Run `cmd` feeding `code` on stdin, reading stdout/stderr with a hard byte cap AT READ TIME
    (an output flood never gets buffered in full). Exceeding the cap kills the container."""
    cap = MAX_OUTPUT_CHARS * 4
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    bufs = {"out": bytearray(), "err": bytearray()}
    flooded = threading.Event()

    def _drain(stream, key):
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    return
                if len(bufs[key]) < cap:
                    bufs[key].extend(chunk[: cap - len(bufs[key])])
                else:
                    flooded.set()
                    return
        except Exception:
            return

    threads = [threading.Thread(target=_drain, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=_drain, args=(proc.stderr, "err"), daemon=True)]
    for t in threads:
        t.start()
    try:
        proc.stdin.write(code.encode("utf-8"))
        proc.stdin.close()
    except OSError:
        pass
    deadline = time.time() + timeout
    while proc.poll() is None:
        if flooded.is_set():
            proc.kill()
            subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=10)
            break
        if time.time() > deadline:
            proc.kill()
            raise subprocess.TimeoutExpired(cmd, timeout)
        time.sleep(0.02)
    for t in threads:
        t.join(timeout=2)
    dec = lambda b: bytes(b).decode("utf-8", errors="replace")[:MAX_OUTPUT_CHARS]
    err = dec(bufs["err"])
    if flooded.is_set():
        err = (err + "\n[output truncated: limit exceeded, execution terminated]")[:MAX_OUTPUT_CHARS]
    return dec(bufs["out"]), err, proc.returncode


def run_python(code: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS, user_id: str = "anonymous") -> SandboxResult:
    """
    Execute `code` as Python 3 inside an ephemeral, network-disabled
    Docker container. Code is passed over stdin to `python -` (never via
    a shell string or `-c`, which would need escaping and risks shell
    injection if this is ever wired to accept code from a less-trusted
    path than it is today) — see Documents/AI/code-sandbox.md Security
    Considerations.

    Raises SandboxUnavailableError if Docker isn't reachable. Never
    returns a fabricated result for a real execution failure — a
    container crash or Docker error surfaces as a real, non-zero
    `exit_code` with the real stderr, not a swallowed exception.
    """
    # Defence in depth: every caller (agent tool, API route, MCP) hits the operator switch here.
    from VENUAI.policy_engine import code_execution_enabled
    if not code_execution_enabled():
        raise SandboxUnavailableError(
            "Code execution is disabled on this deployment (operator must set ENABLE_CODE_EXECUTION=1)."
        )
    if _approval_hook is not None and not _approval_hook(code):
        raise SandboxApprovalDenied("code execution was not approved")
    if not is_available():
        raise SandboxUnavailableError(
            "Docker is not available on this host — sandboxed code execution requires a "
            "running Docker daemon (see VENUAI/code_sandbox.py is_available())."
        )

    with _running_lock:
        if _running_by_user.get(user_id, 0) >= MAX_CONCURRENT_PER_USER:
            raise SandboxBusyError("too many concurrent sandbox executions")
        _running_by_user[user_id] = _running_by_user.get(user_id, 0) + 1
    try:
        return _run_python_locked(code, timeout_seconds, container_name_prefix="venuai-sandbox")
    finally:
        with _running_lock:
            _running_by_user[user_id] = max(0, _running_by_user.get(user_id, 1) - 1)


def _run_python_locked(code: str, timeout_seconds: int, container_name_prefix: str) -> SandboxResult:
    timeout_seconds = min(max(1, timeout_seconds), MAX_TIMEOUT_SECONDS)
    container_name = f"{container_name_prefix}-{uuid.uuid4().hex[:12]}"

    if len(code) > MAX_STDIN_CHARS:
        raise ValueError("code too large")
    cmd = build_docker_cmd(container_name)

    t_start = time.time()
    try:
        out, err, rc = _run_bounded(cmd, code, timeout_seconds + 5, container_name)
        duration = time.time() - t_start
        return SandboxResult(
            stdout=out,
            stderr=err,
            exit_code=rc,
            timed_out=False,
            duration_seconds=round(duration, 3),
        )
    except subprocess.TimeoutExpired:
        duration = time.time() - t_start
        # Killing the `docker run` client process (what subprocess.run's
        # timeout does on its own) does NOT stop the container running
        # server-side in the Docker daemon — confirmed by observation
        # during development: an infinite-loop payload left a live,
        # still-looping container behind every time, a real resource
        # leak. `docker kill` on the named container is what actually
        # stops it; combined with --rm above, the daemon then removes it
        # too.
        #
        # `docker kill` returning is not the same as the container
        # actually having stopped — a real race the first fix attempt
        # missed: an automated test (test_infinite_loop_times_out_and_
        # leaves_no_running_container) caught the container still
        # `docker ps`-visible immediately after `run_python()` returned,
        # because `docker kill` only sends the signal and returns
        # as soon as the daemon acknowledges it, before the container's
        # process has actually exited and `--rm` has removed it.
        # `docker wait` blocks until the container actually exits, so by
        # the time this function returns, it genuinely has. Best-effort
        # beyond that: if even `kill`/`wait` fail (daemon restarted
        # mid-call, etc.), log it rather than raise — the caller already
        # has a valid timed_out result to return.
        try:
            subprocess.run(["docker", "kill", container_name], capture_output=True, timeout=10)
            subprocess.run(["docker", "wait", container_name], capture_output=True, timeout=10)
        except Exception as kill_err:
            logger.error(f"[code_sandbox] Failed to kill/wait timed-out container {container_name}: {kill_err}")
        logger.warning(f"[code_sandbox] Execution timed out after {timeout_seconds}s")
        return SandboxResult(
            stdout="", stderr=f"Execution exceeded the {timeout_seconds}s timeout and was terminated.",
            exit_code=None, timed_out=True, duration_seconds=round(duration, 3),
        )


def ensure_image_pulled() -> bool:
    """
    Pulls SANDBOX_IMAGE if not already present locally — a real network
    call to Docker Hub, run once (e.g. at deployment setup), not on every
    request. Returns False (not an exception) if the pull fails, since a
    missing image on an otherwise-healthy Docker daemon is a deployment
    configuration issue the caller should surface, not a crash.
    """
    try:
        inspect = subprocess.run(
            ["docker", "image", "inspect", SANDBOX_IMAGE], capture_output=True, timeout=10,
        )
        if inspect.returncode == 0:
            return True
        if os.getenv("SANDBOX_AUTO_PULL", "0") != "1":
            logger.warning("[code_sandbox] image %s not present and SANDBOX_AUTO_PULL!=1; provision it at deploy time", SANDBOX_IMAGE)
            return False
        pull = subprocess.run(["docker", "pull", SANDBOX_IMAGE], capture_output=True, timeout=300)
        return pull.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"[code_sandbox] ensure_image_pulled failed: {e}")
        return False
