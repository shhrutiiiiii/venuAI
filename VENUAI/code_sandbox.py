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
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("drdo-rag")

SANDBOX_IMAGE = "python:3.12-slim"
DEFAULT_TIMEOUT_SECONDS = 10
MAX_TIMEOUT_SECONDS = 30
MAX_OUTPUT_CHARS = 8000
MEMORY_LIMIT = "256m"
PIDS_LIMIT = "64"


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


def run_python(code: str, timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS) -> SandboxResult:
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
    if not is_available():
        raise SandboxUnavailableError(
            "Docker is not available on this host — sandboxed code execution requires a "
            "running Docker daemon (see VENUAI/code_sandbox.py is_available())."
        )

    timeout_seconds = min(max(1, timeout_seconds), MAX_TIMEOUT_SECONDS)
    container_name = f"venuai-sandbox-{uuid.uuid4().hex[:12]}"

    cmd = [
        "docker", "run", "--rm", "-i",
        "--name", container_name,
        "--network", "none",
        "--memory", MEMORY_LIMIT,
        "--pids-limit", PIDS_LIMIT,
        "--read-only",
        "--tmpfs", "/tmp:rw,size=16m",
        "--security-opt", "no-new-privileges",
        "--cpus", "0.5",
        SANDBOX_IMAGE, "python", "-",
    ]

    t_start = time.time()
    try:
        proc = subprocess.run(
            cmd, input=code, capture_output=True, text=True,
            timeout=timeout_seconds + 5,  # generous outer bound beyond the container's own limits
        )
        duration = time.time() - t_start
        return SandboxResult(
            stdout=proc.stdout[:MAX_OUTPUT_CHARS],
            stderr=proc.stderr[:MAX_OUTPUT_CHARS],
            exit_code=proc.returncode,
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
        pull = subprocess.run(["docker", "pull", SANDBOX_IMAGE], capture_output=True, timeout=300)
        return pull.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        logger.warning(f"[code_sandbox] ensure_image_pulled failed: {e}")
        return False
