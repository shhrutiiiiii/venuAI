"""
Real tests of upload validation — magic-byte content checking, size limits,
and filename/path-traversal sanitization. No network, no embedding model
involved; pure validation logic.
"""

import io

import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from VENUAI.upload_security import (
    MAX_UPLOAD_BYTES,
    generate_storage_name,
    sanitize_filename,
    validate_upload,
)


def _real_pdf() -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=100, height=100)
    b = io.BytesIO()
    w.write(b)
    return b.getvalue()


def _make_upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(
        filename=filename,
        file=io.BytesIO(content),
        headers=Headers({"content-type": "application/pdf"}),
    )


@pytest.mark.asyncio
async def test_valid_pdf_is_accepted():
    f = _make_upload("report.pdf", _real_pdf())
    content = await validate_upload(f)
    assert content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_non_pdf_content_with_pdf_extension_is_rejected():
    """Never trust the client-supplied extension alone — check actual bytes."""
    f = _make_upload("evil.pdf", b"MZ-this-is-not-a-pdf-just-named-like-one")
    with pytest.raises(HTTPException) as exc:
        await validate_upload(f)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_non_pdf_extension_is_rejected():
    f = _make_upload("report.exe", b"%PDF-1.4" + b"0" * 10)
    with pytest.raises(HTTPException) as exc:
        await validate_upload(f)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_empty_file_is_rejected():
    f = _make_upload("empty.pdf", b"")
    with pytest.raises(HTTPException) as exc:
        await validate_upload(f)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_oversized_file_is_rejected():
    f = _make_upload("big.pdf", b"%PDF-1.4" + b"0" * (MAX_UPLOAD_BYTES + 1))
    with pytest.raises(HTTPException) as exc:
        await validate_upload(f)
    assert exc.value.status_code == 413


def test_path_traversal_filename_is_sanitized():
    assert sanitize_filename("../../etc/passwd.pdf") == "passwd.pdf"
    assert sanitize_filename("..\\..\\windows\\system32\\evil.pdf") == "evil.pdf"
    assert "/" not in sanitize_filename("a/b/c.pdf")


def test_storage_name_is_not_derived_from_client_filename():
    """Storage filenames must be server-generated, never attacker-controlled."""
    name1 = generate_storage_name("../../etc/passwd.pdf")
    name2 = generate_storage_name("../../etc/passwd.pdf")
    assert name1 != name2  # collision-proof: each call generates a fresh UUID
    assert ".." not in name1
    assert "/" not in name1 and "\\" not in name1
