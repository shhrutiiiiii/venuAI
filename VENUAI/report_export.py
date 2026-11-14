"""Report export (Wave B / B4): markdown report + citations -> DOCX / PDF / MD.

FROZEN SEAM: render_docx(report_md, citations, title) -> bytes and
render_pdf(report_md, citations, title) -> bytes.

The markdown is parsed by a deliberately small block parser (headings, bullet and
numbered lists, fenced code, paragraphs; inline bold/italic/code). Raw HTML is
never passed through: tags are stripped before rendering and, for PDF, all text
is XML-escaped before our own formatting tags are added. Control characters are
removed (they are illegal in DOCX XML).
"""
from __future__ import annotations

import io
import re
from typing import List, Optional, Tuple

_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_TAG_RE = re.compile(r"<[^>]{1,200}>")
_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")
_BULLET_RE = re.compile(r"^\s*[-*+]\s+(.*)$")
_NUM_RE = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_INLINE_RE = re.compile(r"(\*\*[^*]+\*\*|\*[^*\s][^*]*\*|`[^`]+`)")

MAX_TITLE_CHARS = 200


def _clean(text: str) -> str:
    return _CTRL_RE.sub("", text or "")


def strip_html(text: str) -> str:
    return _TAG_RE.sub("", _clean(text))


def parse_blocks(md: str) -> List[Tuple[str, object]]:
    """-> [("h", (level, text)) | ("li", text) | ("ol", text) | ("code", text) | ("p", text)]"""
    blocks: List[Tuple[str, object]] = []
    para: List[str] = []
    code: Optional[List[str]] = None

    def flush():
        if para:
            blocks.append(("p", strip_html(" ".join(para)).strip()))
            para.clear()

    for raw in _clean(md).splitlines():
        if raw.strip().startswith("```"):
            if code is None:
                flush()
                code = []
            else:
                blocks.append(("code", "\n".join(code)))
                code = None
            continue
        if code is not None:
            code.append(raw)
            continue
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        m = _HEADING_RE.match(line)
        if m:
            flush()
            blocks.append(("h", (len(m.group(1)), strip_html(m.group(2)).strip())))
            continue
        m = _BULLET_RE.match(line)
        if m:
            flush()
            blocks.append(("li", strip_html(m.group(1)).strip()))
            continue
        m = _NUM_RE.match(line)
        if m:
            flush()
            blocks.append(("ol", strip_html(m.group(1)).strip()))
            continue
        para.append(line.strip())
    if code is not None:
        blocks.append(("code", "\n".join(code)))
    flush()
    return [b for b in blocks if b[1]]


def _inline_parts(text: str) -> List[Tuple[str, str]]:
    """Split into (style, text) where style in {"", "b", "i", "c"}."""
    parts: List[Tuple[str, str]] = []
    pos = 0
    for m in _INLINE_RE.finditer(text):
        if m.start() > pos:
            parts.append(("", text[pos:m.start()]))
        tok = m.group(0)
        if tok.startswith("**"):
            parts.append(("b", tok[2:-2]))
        elif tok.startswith("`"):
            parts.append(("c", tok[1:-1]))
        else:
            parts.append(("i", tok[1:-1]))
        pos = m.end()
    if pos < len(text):
        parts.append(("", text[pos:]))
    return parts


def _citation_line(c: dict) -> str:
    idx = c.get("index", "?")
    src = c.get("filename") or c.get("doc") or c.get("doc_id") or "unknown source"
    page = c.get("page")
    line = f"[{idx}] {strip_html(str(src))}"
    if page not in (None, ""):
        line += f", p. {page}"
    quote = c.get("quote")
    if quote:
        line += f' - "{strip_html(str(quote))[:300]}"'
    if c.get("verified") is False:
        line += " (quote not verified)"
    return line


def _sorted_citations(citations: Optional[List[dict]]) -> List[dict]:
    cs = [c for c in (citations or []) if isinstance(c, dict)]
    return sorted(cs, key=lambda c: (c.get("index") if isinstance(c.get("index"), int) else 10**6))


def render_md(report_md: str, citations: List[dict], title: str) -> bytes:
    out = [f"# {strip_html(title)[:MAX_TITLE_CHARS]}", "", strip_html_keep_lines(report_md)]
    cs = _sorted_citations(citations)
    if cs:
        out += ["", "## Sources", ""] + [f"- {_citation_line(c)}" for c in cs]
    return "\n".join(out).encode("utf-8")


def strip_html_keep_lines(md: str) -> str:
    return "\n".join(_TAG_RE.sub("", ln) for ln in _clean(md).splitlines())


def render_docx(report_md: str, citations: List[dict], title: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading(strip_html(title)[:MAX_TITLE_CHARS] or "Report", level=0)

    def add_runs(par, text: str):
        for style, chunk in _inline_parts(text):
            run = par.add_run(chunk)
            if style == "b":
                run.bold = True
            elif style == "i":
                run.italic = True
            elif style == "c":
                run.font.name = "Courier New"

    for kind, val in parse_blocks(report_md):
        if kind == "h":
            level, text = val  # type: ignore[misc]
            doc.add_heading(text, level=min(level, 4))
        elif kind == "li":
            add_runs(doc.add_paragraph(style="List Bullet"), str(val))
        elif kind == "ol":
            add_runs(doc.add_paragraph(style="List Number"), str(val))
        elif kind == "code":
            p = doc.add_paragraph()
            r = p.add_run(str(val))
            r.font.name = "Courier New"
        else:
            add_runs(doc.add_paragraph(), str(val))

    cs = _sorted_citations(citations)
    if cs:
        doc.add_heading("Sources", level=1)
        for c in cs:
            doc.add_paragraph(_citation_line(c))

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _pdf_text(s: str) -> str:
    """Helvetica (built-in) only covers cp1252; replace anything else visibly."""
    return s.encode("cp1252", "replace").decode("cp1252")


def _pdf_escape(s: str) -> str:
    return _pdf_text(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pdf_inline(text: str) -> str:
    out = []
    for style, chunk in _inline_parts(text):
        e = _pdf_escape(chunk)
        if style == "b":
            e = f"<b>{e}</b>"
        elif style == "i":
            e = f"<i>{e}</i>"
        elif style == "c":
            e = f'<font face="Courier">{e}</font>'
        out.append(e)
    return "".join(out)


def render_pdf(report_md: str, citations: List[dict], title: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import ListFlowable, ListItem, Paragraph, Preformatted, SimpleDocTemplate, Spacer

    ss = getSampleStyleSheet()
    body, code_style = ss["BodyText"], ParagraphStyle("code", parent=ss["Code"], fontSize=8)
    heads = {1: ss["Heading1"], 2: ss["Heading2"], 3: ss["Heading3"], 4: ss["Heading4"]}

    buf = io.BytesIO()
    pdf = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm,
                            topMargin=18 * mm, bottomMargin=18 * mm,
                            title=_pdf_text(strip_html(title))[:MAX_TITLE_CHARS])
    story: list = [Paragraph(_pdf_escape(strip_html(title)[:MAX_TITLE_CHARS] or "Report"), ss["Title"]), Spacer(1, 6)]

    pending: List[Paragraph] = []
    pending_kind = ""

    def flush_list():
        nonlocal pending, pending_kind
        if pending:
            story.append(ListFlowable([ListItem(p) for p in pending],
                                      bulletType="1" if pending_kind == "ol" else "bullet"))
            pending, pending_kind = [], ""

    for kind, val in parse_blocks(report_md):
        if kind in ("li", "ol"):
            if pending_kind and pending_kind != kind:
                flush_list()
            pending_kind = kind
            pending.append(Paragraph(_pdf_inline(str(val)), body))
            continue
        flush_list()
        if kind == "h":
            level, text = val  # type: ignore[misc]
            story.append(Paragraph(_pdf_inline(text), heads[min(level, 4)]))
        elif kind == "code":
            story.append(Preformatted(_pdf_text(str(val)), code_style))
        else:
            story.append(Paragraph(_pdf_inline(str(val)), body))
    flush_list()

    cs = _sorted_citations(citations)
    if cs:
        story.append(Paragraph("Sources", ss["Heading1"]))
        for c in cs:
            story.append(Paragraph(_pdf_escape(_citation_line(c)), body))

    pdf.build(story)
    return buf.getvalue()
