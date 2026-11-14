"""
Dataset Upload Security — validates CSV/XLSX uploads for the Data Analyst
Agent before they ever reach pandas.

Mirrors VENUAI/upload_security.py's pattern (size limit, extension
allowlist, content verification, filename sanitization) rather than
duplicating that module's PDF-specific logic — this is a separate module
because CSV/XLSX have different magic-byte and structural-validation needs
(XLSX is a real zip archive; CSV has no magic bytes at all, so it's
validated by actually parsing it with pandas under a resource cap instead).
"""

import logging
import os
import re
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Tuple

import pandas as pd
from fastapi import HTTPException, UploadFile

from VENUAI.upload_security import read_upload_capped, validate_filename

logger = logging.getLogger("drdo-rag")

MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # 20 MB — datasets are tabular, not meant to be huge blobs
MAX_ROWS = int(os.getenv("DATASET_MAX_ROWS", "100000"))  # real resource cap — endpoint is synchronous (ADR-003)
MAX_COLUMNS = int(os.getenv("DATASET_MAX_COLUMNS", "200"))
MAX_ZIP_ENTRIES = int(os.getenv("DATASET_MAX_ZIP_ENTRIES", "500"))
MAX_UNCOMPRESSED_BYTES = int(os.getenv("DATASET_MAX_UNCOMPRESSED_MB", "100")) * 1024 * 1024
MAX_COMPRESSION_RATIO = int(os.getenv("DATASET_MAX_COMPRESSION_RATIO", "100"))
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
XLSX_MAGIC_BYTES = b"PK\x03\x04"       # XLSX is a real zip archive — this is its actual file signature
_FORBIDDEN_ZIP_MEMBERS = ("vbaproject.bin", "externallinks/", "activex/", "embeddings/", "oleobject")
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_filename(filename: str) -> str:
    """Same rule as upload_security.sanitize_filename: strip any path
    component and non-safe characters — never trust client input as a path."""
    name = Path((filename or "").replace("\\", "/")).name
    name = re.sub(r"[^A-Za-z0-9_.\-]", "_", name)[:255]
    return name or "dataset.csv"


def generate_storage_name(original_filename: str, ext: str) -> str:
    """A collision-proof, path-traversal-proof filename for disk storage."""
    return f"{uuid.uuid4().hex}{ext}"


def neutralise_formula(value):
    """Defuse CSV/Excel formula injection for any string cell that will be exported or echoed."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def neutralise_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out.columns = [neutralise_formula(str(c)) for c in out.columns]
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(neutralise_formula)
    return out


def check_xlsx_archive(content: bytes) -> None:
    """Inspect the zip central directory WITHOUT decompressing: entry count, declared
    uncompressed size, compression ratio, macros/external links/encryption. 422/413 on violation."""
    try:
        zf = zipfile.ZipFile(BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=422, detail="File is not a valid XLSX archive.")
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise HTTPException(status_code=413, detail="XLSX archive has too many entries.")
        total = 0
        names = set()
        for zi in infos:
            n = zi.filename.lower()
            names.add(n)
            if zi.flag_bits & 0x1:
                raise HTTPException(status_code=422, detail="Encrypted XLSX files are not supported.")
            if n.startswith("/") or ".." in n.split("/"):
                raise HTTPException(status_code=422, detail="XLSX archive contains an unsafe path.")
            if any(bad in n for bad in _FORBIDDEN_ZIP_MEMBERS):
                raise HTTPException(status_code=422, detail="XLSX contains macros or external content and was rejected.")
            total += zi.file_size
            if zi.file_size > 1024 and zi.compress_size and zi.file_size / zi.compress_size > MAX_COMPRESSION_RATIO:
                raise HTTPException(status_code=413, detail="XLSX archive compression ratio is too high.")
            if total > MAX_UNCOMPRESSED_BYTES:
                raise HTTPException(status_code=413, detail="XLSX archive expands beyond the allowed size.")
        if "[content_types].xml" not in names or not any(n.startswith("xl/") for n in names):
            raise HTTPException(status_code=422, detail="File is not a valid XLSX workbook.")
        ct = zf.read([i for i in infos if i.filename.lower() == "[content_types].xml"][0])
        if b"macroEnabled" in ct or b"vbaProject" in ct:
            raise HTTPException(status_code=422, detail="XLSX contains macros and was rejected.")


def _parse_csv(content: bytes) -> pd.DataFrame:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8 encoded.")
    if "\x00" in text[:65536]:
        raise HTTPException(status_code=422, detail="File is not a text CSV.")
    import csv
    try:
        sep = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|").delimiter
    except csv.Error:
        sep = ","
    # nrows is bounded so a huge-row file never builds an oversized frame.
    return pd.read_csv(BytesIO(text.encode("utf-8")), sep=sep, nrows=MAX_ROWS + 1, low_memory=False)


def _parse_xlsx(content: bytes) -> pd.DataFrame:
    return pd.read_excel(BytesIO(content), nrows=MAX_ROWS + 1, engine="openpyxl")


async def validate_and_parse_dataset(file: UploadFile, content_length: Optional[str] = None) -> Tuple[bytes, pd.DataFrame, str]:
    """
    Validates the upload and returns (raw_bytes, parsed_dataframe, extension).
    Raises HTTPException on any violation — including a dataset that parses
    fine but exceeds MAX_ROWS/MAX_COLUMNS, since this endpoint has no
    background job queue and must bound its own processing cost per request.
    """
    validate_filename(file.filename)

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are supported.")

    content = await read_upload_capped(file, MAX_UPLOAD_BYTES, content_length)

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    if ext == ".xlsx":
        if not content.startswith(XLSX_MAGIC_BYTES):
            raise HTTPException(
                status_code=422,
                detail="File content does not match a valid XLSX archive (magic bytes check failed).",
            )
        check_xlsx_archive(content)
    elif content.startswith(XLSX_MAGIC_BYTES) or content.startswith(b"%PDF-") or content.startswith(b"MZ"):
        raise HTTPException(status_code=422, detail="File content does not match a CSV file.")

    try:
        df = _parse_csv(content) if ext == ".csv" else _parse_xlsx(content)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("[dataset_security] parse failed: %s", e)
        raise HTTPException(status_code=422, detail="Failed to parse dataset.")

    if df.shape[0] > MAX_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"Dataset exceeds the limit of {MAX_ROWS} rows per upload.",
        )
    if df.shape[1] > MAX_COLUMNS:
        raise HTTPException(
            status_code=413,
            detail=f"Dataset has {df.shape[1]} columns; the limit is {MAX_COLUMNS} columns per upload.",
        )
    if df.shape[0] == 0 or df.shape[1] == 0:
        raise HTTPException(status_code=422, detail="Dataset has no rows/columns to analyze.")

    return content, df, ext
