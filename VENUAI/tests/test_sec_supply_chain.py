"""
Supply-chain and repo-hygiene smoke tests (S5): no secrets or databases are tracked,
dependency files are pinned, Docker and compose files follow the hardening policy.
These read files only; no network, no Docker daemon.
"""

import json
import re
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.security

ROOT = Path(__file__).resolve().parent.parent.parent

SECRET_RULES = {
    "groq": re.compile(r"gsk_[A-Za-z0-9]{30,}"),
    "aws": re.compile(r"AKIA[0-9A-Z]{16}"),
    "github": re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def _tracked() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
        ).stdout.decode()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    return [f for f in out.split("\0") if f]


def test_no_database_or_env_files_tracked():
    bad = []
    for f in _tracked():
        name = Path(f).name
        if re.search(r"\.(db|sqlite3?|pem|key|p12)$", name):
            bad.append(f)
        if name.startswith(".env") and not name.endswith(".example"):
            bad.append(f)
    assert not bad, f"sensitive files tracked in git: {bad}"


def test_no_secret_patterns_in_tracked_sources():
    hits = []
    for f in _tracked():
        if any(s in "/" + f for s in ("/tests/", "/Documents/", "package-lock.json", "requirements.lock", "scan_secrets.py")):
            continue
        p = ROOT / f
        if not p.is_file() or p.stat().st_size > 2_000_000:
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for rule, rx in SECRET_RULES.items():
            if rx.search(text):
                hits.append(f"{f}:{rule}")
    assert not hits, f"possible secrets committed: {hits}"


def test_env_example_has_placeholders_only():
    text = (ROOT / "VENUAI" / ".env.example").read_text(encoding="utf-8")
    for rule, rx in SECRET_RULES.items():
        assert not rx.search(text), rule


def test_gitignore_covers_user_data_and_secrets():
    gi = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for needle in ("uploaded_datasets", "datasets.json", "*.db", ".judge_cache", ".env.*", "uploaded_docs", "chroma_db"):
        assert needle in gi, f".gitignore is missing {needle}"


def test_python_lock_is_hash_pinned_and_requirements_defer_to_it():
    lock = (ROOT / "VENUAI" / "requirements.lock").read_text(encoding="utf-8")
    reqs = [l for l in lock.splitlines() if re.match(r"^[A-Za-z0-9_.\-\[\]]+==", l)]
    assert len(reqs) > 50
    assert lock.count("--hash=sha256:") >= len(reqs)
    assert "-r requirements.lock" in (ROOT / "VENUAI" / "requirements.txt").read_text(encoding="utf-8")


def test_requirements_in_has_upper_bounds():
    for line in (ROOT / "VENUAI" / "requirements.in").read_text(encoding="utf-8").splitlines():
        line = line.split("#")[0].strip()
        if line:
            assert "<" in line or "==" in line, f"unbounded requirement: {line}"


def test_next_and_react_pinned_exactly():
    pkg = json.loads((ROOT / "chat-app" / "package.json").read_text(encoding="utf-8"))
    for name in ("next", "react", "react-dom"):
        assert re.fullmatch(r"\d+\.\d+\.\d+", pkg["dependencies"][name]), name


def test_next_not_on_vulnerable_range():
    pkg = json.loads((ROOT / "chat-app" / "package.json").read_text(encoding="utf-8"))
    version = tuple(int(x) for x in pkg["dependencies"]["next"].split("."))
    assert version >= (16, 4, 1), "next is within the range affected by the critical next/og advisory (16.0.0 - 16.4.0)"


def test_backend_dockerfile_hardened():
    df = (ROOT / "VENUAI" / "Dockerfile").read_text(encoding="utf-8")
    stages = re.findall(r"^FROM .* AS (\w+)", df, flags=re.M)
    assert stages[:2] == ["builder", "runtime"]
    runtime = df.split("AS runtime", 1)[1]
    assert "build-essential" not in runtime
    assert re.search(r"^USER 10001", runtime, flags=re.M)
    assert "HEALTHCHECK" in runtime
    assert "--require-hashes" in df
    assert "tini" in runtime and "--proxy-headers" in runtime


def test_frontend_dockerfile_hardened():
    df = (ROOT / "chat-app" / "Dockerfile").read_text(encoding="utf-8")
    assert "node:20" not in df and "node:22" in df
    assert re.search(r"^USER 10001", df, flags=re.M)
    assert "HEALTHCHECK" in df and "NEXT_TELEMETRY_DISABLED=1" in df


def test_dockerignore_excludes_secrets_and_data():
    di = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for needle in ("**/.env", "**/*.db", "VENUAI/uploaded_docs", "VENUAI/chroma_db", ".git"):
        assert needle in di


def test_prod_compose_does_not_publish_backend_or_postgres():
    yaml = pytest.importorskip("yaml")
    prod = yaml.safe_load((ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8"))
    for name in ("backend", "postgres", "frontend"):
        assert "ports" not in prod["services"][name], f"{name} must not publish ports in prod"
    published = [str(p) for p in prod["services"]["proxy"]["ports"]]
    assert sorted(published) == ["443:443", "80:80"]
    text = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    assert "docker.sock" not in text
    for name in ("backend", "frontend", "proxy"):
        svc = prod["services"][name]
        assert svc.get("read_only") is True and svc.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name


def test_compose_has_no_default_passwords():
    for f in ("docker-compose.yml", "docker-compose.prod.yml"):
        text = (ROOT / f).read_text(encoding="utf-8")
        assert not re.search(r"POSTGRES_PASSWORD=(postgres|password|admin|changeme)\s*$", text, flags=re.M), f


def test_dev_compose_publishes_on_localhost_only():
    yaml = pytest.importorskip("yaml")
    dev = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    for svc in dev["services"].values():
        for port in svc.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), port


def test_ci_workflows_least_privilege_and_pinned():
    yaml = pytest.importorskip("yaml")
    wf_dir = ROOT / ".github" / "workflows"
    for wf in wf_dir.glob("*.yml"):
        doc = yaml.safe_load(wf.read_text(encoding="utf-8"))
        assert doc.get("permissions", {}).get("contents") == "read", wf.name
        for use in re.findall(r"uses:\s*(\S+)", wf.read_text(encoding="utf-8")):
            assert re.search(r"@[0-9a-f]{40}$", use) or use.startswith("./"), f"{wf.name}: action not pinned by SHA: {use}"
