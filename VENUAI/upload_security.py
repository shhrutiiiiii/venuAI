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

import re
import uuid
from pathlib import Path

from fastapi import HTTPException, UploadFile

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB
ALLOWED_EXTENSIONS = {".pdf"}
PDF_MAGIC_BYTES = b"%PDF-"


def sanitize_filename(filename: str) -> str:
    """Strip any path component and non-safe characters; never trust client input as a path."""
    name = Path(filename).name  # drops any directory traversal component
    name = re.sub(r"[^A-Za-z0-9_.\-]", "_", name)
    return name or "upload.pdf"


def generate_storage_name(original_filename: str) -> str:
    """A collision-proof, path-traversal-proof filename for disk storage."""
    safe = sanitize_filename(original_filename)
    ext = Path(safe).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        ext = ".pdf"
    return f"{uuid.uuid4().hex}{ext}"


async def validate_upload(file: UploadFile) -> bytes:
    """
    Reads and validates the full upload. Raises HTTPException on any violation.
    Returns the validated raw bytes (callers should not re-read `file` after this).
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.",
        )
    if not content.startswith(PDF_MAGIC_BYTES):
        raise HTTPException(
            status_code=422,
            detail="File content does not match a valid PDF (magic bytes check failed).",
        )

    return content
