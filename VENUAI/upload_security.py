"""
DRDO VenuAI — Upload Security
================================
Validates uploaded files before they ever reach the PDF parser:
  - size limit
  - extension allowlist
  - magic-byte (content) verification — never trust the client-supplied
    filename/extension/MIME type alone
  - filename sanitization (no path traversal, no directory components)

This exists because file upload is the single largest untrusted-input
surface in the backend — see Documents/Database/architecture.md and
Documents/Backend/architecture.md.
"""

import logging
import os
import re
import unicodedata
import uuid
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, UploadFile

logger = logging.getLogger("drdo-rag")

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
CHUNK_BYTES = 1024 * 1024            # read uploads 1 MB at a time
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "300"))
MAX_FILENAME_CHARS = 255
ALLOWED_EXTENSIONS = {".pdf"}
PDF_MAGIC_BYTES = b"%PDF-"
# Executable-ish extensions: a "double extension" like x.pdf.exe is rejected outright.
_DANGEROUS_EXTS = {
    ".exe", ".dll", ".bat", ".cmd", ".com", ".scr", ".msi", ".ps1", ".vbs", ".js", ".jse", ".jar",
    ".sh", ".php", ".py", ".html", ".htm", ".svg", ".hta", ".lnk", ".xlsm", ".docm",
}
# PDF active content / auto-actions. Names are matched on the raw bytes (also catches #-escaped
# names, handled by _normalise_pdf_names) — a document with any of these is rejected.
_PDF_ACTIVE_RE = re.compile(
    rb"/(JavaScript|JS|Launch|EmbeddedFiles?|OpenAction|RichMedia|SubmitForm|ImportData|GoToR|GoToE)(?![A-Za-z])"
)
_PDF_HEX_NAME_RE = re.compile(rb"#([0-9A-Fa-f]{2})")


def _normalise_pdf_names(data: bytes) -> bytes:
    """Resolve PDF name #XX escapes (e.g. /J#61vaScript) so obfuscated names are still matched."""
    return _PDF_HEX_NAME_RE.sub(lambda m: bytes([int(m.group(1), 16)]), data)


def validate_filename(filename: Optional[str]) -> str:
    """Reject hostile client filenames (NUL/control/bidi chars, too long, double extensions,
    traversal). Returns the original name unchanged when acceptable."""
    if not filename or not isinstance(filename, str):
        raise HTTPException(status_code=400, detail="Filename is required.")
    if len(filename) > MAX_FILENAME_CHARS:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    for ch in filename:
        cat = unicodedata.category(ch)
        if cat in ("Cc", "Cf", "Zl", "Zp") or ch in ("\\", "/"):
            raise HTTPException(status_code=400, detail="Invalid filename.")
    if ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename.")
    suffixes = [x.lower() for x in Path(filename).suffixes]
    if any(x in _DANGEROUS_EXTS for x in suffixes):
        raise HTTPException(status_code=400, detail="Invalid filename.")
    return filename


def sanitize_filename(filename: str) -> str:
    """Strip any path component and non-safe characters; never trust client input as a path."""
    name = Path((filename or "").replace("\\", "/")).name  # drops any directory traversal component
    name = re.sub(r"[^A-Za-z0-9_.\-]", "_", name)[:MAX_FILENAME_CHARS]
    return name or "upload.pdf"


def generate_storage_name(original_filename: str) -> str:
    """A collision-proof, path-traversal-proof filename for disk storage."""
    safe = sanitize_filename(original_filename)
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".pdf"
    return f"{uuid.uuid4().hex}{ext}"


async def read_upload_capped(file: UploadFile, max_bytes: int, content_length: Optional[str] = None) -> bytes:
    """Read an upload in 1 MB chunks, aborting (413) the moment the running total exceeds the cap.
    A declared Content-Length above the cap is rejected before anything is read."""
    if content_length:
        try:
            if int(content_length) > max_bytes + 64 * 1024:  # multipart framing overhead allowance
                raise HTTPException(status_code=413, detail="File exceeds the upload size limit.")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.")
    buf = bytearray()
    while True:
        chunk = await file.read(CHUNK_BYTES)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {max_bytes // (1024 * 1024)} MB upload limit.",
            )
    return bytes(buf)


def scan_pdf_bytes(content: bytes) -> None:
    """Structural PDF checks on raw bytes: header near the start, %%EOF near the end,
    and no active content / auto-actions. Raises HTTPException(422)."""
    if PDF_MAGIC_BYTES not in content[:1024] or not content.startswith(PDF_MAGIC_BYTES):
        raise HTTPException(
            status_code=422,
            detail="File content does not match a valid PDF (magic bytes check failed).",
        )
    if b"%%EOF" not in content[-2048:]:
        raise HTTPException(status_code=422, detail="File is not a complete PDF (missing end-of-file marker).")
    if _PDF_ACTIVE_RE.search(content) or _PDF_ACTIVE_RE.search(_normalise_pdf_names(content)):
        logger.warning("[upload_security] PDF with active content rejected")
        raise HTTPException(status_code=422, detail="PDF contains active content (scripts, launch actions or embedded files) and was rejected.")


def check_pdf_page_count(page_count: int) -> None:
    if page_count > MAX_PDF_PAGES:
        raise HTTPException(status_code=413, detail=f"PDF has {page_count} pages; the limit is {MAX_PDF_PAGES}.")


def inspect_pdf_structure(content: bytes) -> int:
    """Parse the PDF catalog with pypdf (catches active content hidden in compressed object
    streams that the raw-byte scan cannot see), enforce the page cap, return the page count."""
    from io import BytesIO
    try:
        from pypdf import PdfReader
        reader = PdfReader(BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise HTTPException(status_code=422, detail="Encrypted PDFs are not supported.")
        root = reader.trailer["/Root"].get_object()
        bad = [k for k in ("/OpenAction", "/AA", "/JavaScript", "/JS", "/Launch") if k in root]
        names = root.get("/Names")
        if names is not None:
            names = names.get_object()
            bad += [k for k in ("/JavaScript", "/EmbeddedFiles") if k in names]
        if bad:
            logger.warning("[upload_security] PDF catalog active content: %s", bad)
            raise HTTPException(status_code=422, detail="PDF contains active content (scripts, launch actions or embedded files) and was rejected.")
        count = len(reader.pages)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[upload_security] PDF parse failed: %s", e)
        raise HTTPException(status_code=422, detail="Failed to parse PDF.")
    check_pdf_page_count(count)
    return count


def run_with_timeout(fn, timeout_seconds: float, *args, **kwargs):
    """Run a blocking parser in a worker thread and give up after `timeout_seconds` (504-style 422)."""
    import concurrent.futures
    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = ex.submit(fn, *args, **kwargs)
    try:
        return fut.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError:
        raise HTTPException(status_code=422, detail="Document processing timed out.")
    finally:
        ex.shutdown(wait=False)


async def validate_upload(file: UploadFile, content_length: Optional[str] = None) -> bytes:
    """
    Reads and validates the full upload. Raises HTTPException on any violation.
    Returns the validated raw bytes (callers should not re-read `file` after this).
    """
    validate_filename(file.filename)

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    content = await read_upload_capped(file, MAX_UPLOAD_BYTES, content_length)

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    scan_pdf_bytes(content)
    inspect_pdf_structure(content)

    return content
