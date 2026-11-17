"""POST /export/report (Wave B / B4): markdown report -> DOCX / PDF / MD download."""
from __future__ import annotations

import json
import re
from typing import List, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from VENUAI.auth import AuthenticatedUser, get_current_user

router = APIRouter(tags=["export"])

MAX_EXPORT_BYTES = 2 * 1024 * 1024
_MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
    "md": "text/markdown; charset=utf-8",
}


class ExportRequest(BaseModel):
    format: Literal["docx", "pdf", "md"]
    title: str = Field(default="Report", max_length=300)
    markdown: str
    citations: List[dict] = Field(default_factory=list)


def safe_filename(title: str, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (title or "report")).strip("._-")[:60] or "report"
    return f"{base}.{ext}"


@router.post("/export/report")
def export_report(body: ExportRequest, user: AuthenticatedUser = Depends(get_current_user)) -> Response:
    size = len(body.markdown.encode("utf-8")) + len(json.dumps(body.citations, default=str).encode("utf-8"))
    if size > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="Report too large to export (2 MB limit).")
    if not body.markdown.strip():
        raise HTTPException(status_code=400, detail="markdown must not be empty.")

    from VENUAI import report_export
    try:
        if body.format == "docx":
            data = report_export.render_docx(body.markdown, body.citations, body.title)
        elif body.format == "pdf":
            data = report_export.render_pdf(body.markdown, body.citations, body.title)
        else:
            data = report_export.render_md(body.markdown, body.citations, body.title)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Export failed: {type(e).__name__}") from e

    fname = safe_filename(body.title, body.format)
    return Response(
        content=data, media_type=_MEDIA[body.format],
        headers={"Content-Disposition": f'attachment; filename="{fname}"', "X-Content-Type-Options": "nosniff"},
    )
