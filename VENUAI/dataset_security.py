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

import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Tuple

import pandas as pd
from fastapi import HTTPException, UploadFile

MAX_UPLOAD_BYTES = 20 * 1024 * 1024   # 20 MB — datasets are tabular, not meant to be huge blobs
MAX_ROWS = 100_000                     # real resource cap — this endpoint is synchronous (no job queue, ADR-003)
MAX_COLUMNS = 200
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}
XLSX_MAGIC_BYTES = b"PK\x03\x04"       # XLSX is a real zip archive — this is its actual file signature


def sanitize_filename(filename: str) -> str:
    """Same rule as upload_security.sanitize_filename: strip any path
    component and non-safe characters — never trust client input as a path."""
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9_.\-]", "_", name)
    return name or "dataset.csv"


def generate_storage_name(original_filename: str, ext: str) -> str:
    """A collision-proof, path-traversal-proof filename for disk storage."""
    return f"{uuid.uuid4().hex}{ext}"


async def validate_and_parse_dataset(file: UploadFile) -> Tuple[bytes, pd.DataFrame, str]:
    """
    Validates the upload and returns (raw_bytes, parsed_dataframe, extension).
    Raises HTTPException on any violation — including a dataset that parses
    fine but exceeds MAX_ROWS/MAX_COLUMNS, since this endpoint has no
    background job queue and must bound its own processing cost per request.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required.")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only .csv and .xlsx files are supported.")

    content = await file.read()

    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit.",
        )

    if ext == ".xlsx" and not content.startswith(XLSX_MAGIC_BYTES):
        raise HTTPException(
            status_code=422,
            detail="File content does not match a valid XLSX archive (magic bytes check failed).",
        )

    try:
        if ext == ".csv":
            df = pd.read_csv(BytesIO(content))
        else:
            df = pd.read_excel(BytesIO(content))
    except Exception as e:
        import logging
        logging.getLogger("drdo-rag").warning("dataset parse failed: %s", type(e).__name__, exc_info=e)
        raise HTTPException(status_code=422, detail="Failed to parse dataset. Check that it is a valid CSV/XLSX file.")

    if df.shape[0] > MAX_ROWS:
        raise HTTPException(
            status_code=413,
            detail=f"Dataset has {df.shape[0]} rows; the limit is {MAX_ROWS} rows per upload.",
        )
    if df.shape[1] > MAX_COLUMNS:
        raise HTTPException(
            status_code=413,
            detail=f"Dataset has {df.shape[1]} columns; the limit is {MAX_COLUMNS} columns per upload.",
        )
    if df.shape[0] == 0 or df.shape[1] == 0:
        raise HTTPException(status_code=422, detail="Dataset has no rows/columns to analyze.")

    return content, df, ext
