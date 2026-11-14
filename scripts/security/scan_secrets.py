#!/usr/bin/env python3
"""Dependency-free fallback secret scan (use gitleaks when available).

Scans tracked files (and optionally all git history with --history) for high-signal
key patterns. Never prints the secret itself: only file, line and rule name.
Exit code 1 when anything is found.
"""
import re
import subprocess
import sys

RULES = {
    "groq-key": r"gsk_[A-Za-z0-9]{30,}",
    "openai-style-key": r"sk-[A-Za-z0-9]{32,}",
    "aws-access-key": r"AKIA[0-9A-Z]{16}",
    "github-token": r"gh[pousr]_[A-Za-z0-9]{36,}",
    "slack-token": r"xox[baprs]-[A-Za-z0-9-]{10,}",
    "google-api-key": r"AIza[0-9A-Za-z_\-]{35}",
    "private-key": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
}
SKIP_PARTS = ("package-lock.json", "requirements.lock", "/tests/", "/Documents/", "scan_secrets.py")


def tracked_files() -> list[str]:
    out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True).stdout
    return [f for f in out.decode().split("\0") if f]


def scan_tree() -> list[tuple[str, int, str]]:
    compiled = {k: re.compile(v) for k, v in RULES.items()}
    found = []
    for path in tracked_files():
        if any(p in "/" + path for p in SKIP_PARTS):
            continue
        try:
            text = open(path, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            for name, rx in compiled.items():
                if rx.search(line):
                    found.append((path, lineno, name))
    return found


def main() -> int:
    hits = scan_tree()
    for path, lineno, name in hits:
        print(f"{path}:{lineno}: {name}")
    if "--history" in sys.argv:
        for name, pat in RULES.items():
            log = subprocess.run(
                ["git", "log", "--all", "-G" + pat, "--format=%h", "--", ".",
                 ":!*package-lock.json", ":!*tests/*", ":!Documents/*", ":!scripts/security/*"],
                capture_output=True, text=True,
            ).stdout.split()
            for commit in log:
                print(f"history: commit {commit}: {name}")
                hits.append((commit, 0, name))
    print(f"{len(hits)} potential secret(s) found")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
