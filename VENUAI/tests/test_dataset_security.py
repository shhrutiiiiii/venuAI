"""Real tests for VENUAI/dataset_security.py — actual pandas parsing,
actual validation logic, no mocking of the validation itself."""

import io

import pytest
import pandas as pd
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from VENUAI.dataset_security import (
    MAX_COLUMNS, MAX_ROWS, MAX_UPLOAD_BYTES,
    sanitize_filename, validate_and_parse_dataset,
)


def _make_upload(filename: str, content: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content), headers=Headers({}))


def _real_csv_bytes() -> bytes:
    df = pd.DataFrame({"category": ["a", "b"], "revenue": [100.0, 200.0]})
    return df.to_csv(index=False).encode()


def _real_xlsx_bytes() -> bytes:
    df = pd.DataFrame({"category": ["a", "b"], "revenue": [100.0, 200.0]})
    buf = io.BytesIO()
    df.to_excel(buf, index=False)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_valid_csv_is_accepted_and_parsed():
    f = _make_upload("data.csv", _real_csv_bytes())
    content, df, ext = await validate_and_parse_dataset(f)
    assert ext == ".csv"
    assert list(df.columns) == ["category", "revenue"]
    assert len(df) == 2


@pytest.mark.asyncio
async def test_valid_xlsx_is_accepted_and_parsed():
    f = _make_upload("data.xlsx", _real_xlsx_bytes())
    content, df, ext = await validate_and_parse_dataset(f)
    assert ext == ".xlsx"
    assert len(df) == 2


@pytest.mark.asyncio
async def test_disallowed_extension_is_rejected():
    f = _make_upload("data.txt", b"not a real dataset")
    with pytest.raises(HTTPException) as exc:
        await validate_and_parse_dataset(f)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_empty_file_is_rejected():
    f = _make_upload("data.csv", b"")
    with pytest.raises(HTTPException) as exc:
        await validate_and_parse_dataset(f)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_malformed_csv_is_rejected():
    f = _make_upload("data.csv", b"\x00\x01\x02\x03\xff\xfe")
    with pytest.raises(HTTPException) as exc:
        await validate_and_parse_dataset(f)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_fake_xlsx_with_wrong_magic_bytes_is_rejected():
    """A file named .xlsx but whose content isn't actually a zip archive —
    never trust the extension alone."""
    f = _make_upload("data.xlsx", b"this is not a real xlsx file at all")
    with pytest.raises(HTTPException) as exc:
        await validate_and_parse_dataset(f)
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_oversized_file_is_rejected():
    huge_csv = b"a,b\n" + b"1,2\n" * (MAX_UPLOAD_BYTES // 4 + 1000)
    f = _make_upload("data.csv", huge_csv)
    with pytest.raises(HTTPException) as exc:
        await validate_and_parse_dataset(f)
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_too_many_rows_is_rejected():
    df = pd.DataFrame({"a": range(MAX_ROWS + 10)})
    content = df.to_csv(index=False).encode()
    f = _make_upload("data.csv", content)
    with pytest.raises(HTTPException) as exc:
        await validate_and_parse_dataset(f)
    assert exc.value.status_code == 413
    assert "rows" in exc.value.detail


@pytest.mark.asyncio
async def test_too_many_columns_is_rejected():
    df = pd.DataFrame({f"col_{i}": [1, 2] for i in range(MAX_COLUMNS + 10)})
    content = df.to_csv(index=False).encode()
    f = _make_upload("data.csv", content)
    with pytest.raises(HTTPException) as exc:
        await validate_and_parse_dataset(f)
    assert exc.value.status_code == 413
    assert "columns" in exc.value.detail


def test_sanitize_filename_strips_path_traversal():
    assert sanitize_filename("../../etc/passwd.csv") == "passwd.csv"
