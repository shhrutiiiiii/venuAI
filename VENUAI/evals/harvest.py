"""Harvest: run questions through the REAL pipeline and write JSONL.

No fake fallback: if the pipeline is unreachable or ingest/auth fails the
harvest raises HarvestError and writes nothing that looks like a result.
Scoring is a separate step (score.py) so re-scoring never re-calls the LLM.
"""
import io
import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

DATASETS = Path(__file__).parent / "datasets"
GUARDRAIL_MODEL = "DRDO Defence Guardrails Policy"
KNOWN_KEYS = {"answer", "sources", "model", "chunks_retrieved", "annotated_answer"}


class HarvestError(RuntimeError):
    pass


def load_corpus() -> List[dict]:
    return json.loads((DATASETS / "synthetic_corpus.json").read_text(encoding="utf-8"))["documents"]


def make_pdf(title: str, text: str) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    y = 800
    for line in text.split("\n"):
        while len(line) > 95:
            cut = max(line.rfind(" ", 0, 95), 1)
            c.drawString(50, y, line[:cut]); line = line[cut:].lstrip(); y -= 14
        c.drawString(50, y, line); y -= 14
        if y < 60:
            c.showPage(); y = 800
    c.save()
    return buf.getvalue()


class Session:
    """Authenticated client against a real VenuAI backend (HTTP or in-process)."""

    def __init__(self, client, email=None, password=None):
        self.client = client
        self.tenant = None
        self.doc_ids: List[str] = []
        self.owned_user = email is None
        if email is None:
            tag = uuid.uuid4().hex[:8]
            email, password = f"eval-{tag}@example.com", f"Ev@l-{uuid.uuid4().hex}"
            # organization_name makes the server create a brand-new tenant -> isolated retrieval scope
            r = self._req("post", "/auth/register", json={"email": email, "password": password, "organization_name": f"eval-{tag}"})
            if r.status_code >= 300:
                raise HarvestError(f"register failed {r.status_code}: {r.text[:200]}")
        r = self._req("post", "/auth/login", json={"email": email, "password": password})
        if r.status_code >= 300:
            raise HarvestError(f"login failed {r.status_code}: {r.text[:200]}")
        body = r.json()
        self.headers = {"Authorization": f"Bearer {body['access_token']}"}
        self.tenant = self.tenant or body.get("user", {}).get("tenant_id")

    def _req(self, method, path, retries=4, **kw):
        for i in range(retries):
            try:
                r = getattr(self.client, method)(path, headers=getattr(self, "headers", None), **kw)
            except Exception as e:
                raise HarvestError(f"pipeline unreachable ({path}): {e}")
            if r.status_code != 429:
                return r
            time.sleep(15 * (i + 1))
        return r

    def ingest(self, docs: List[dict]):
        for d in docs:
            pdf = make_pdf(d["title"], d["text"])
            r = self._req("post", "/upload", files={"file": (d["id"], pdf, "application/pdf")}, timeout=300)
            if r.status_code >= 300:
                raise HarvestError(f"upload {d['id']} failed {r.status_code}: {r.text[:200]}")
            self.doc_ids.append(r.json()["id"])

    def cleanup(self):
        for i in self.doc_ids:
            try:
                self._req("delete", f"/documents/{i}")
            except Exception:
                pass

    def query(self, question: str, top_k: int = 4, extra: Optional[dict] = None) -> dict:
        t0 = time.perf_counter()
        payload = {"question": question, "top_k": top_k, "session_id": f"eval-{uuid.uuid4().hex[:8]}"}
        payload.update(extra or {})
        r = self._req("post", "/query", json=payload, timeout=180)
        return {"status": r.status_code, "latency_ms": (time.perf_counter() - t0) * 1000,
                "body": r.json() if r.headers.get("content-type", "").startswith("application/json") else {"detail": r.text[:300]}}


def expand_preview(preview: str, filename: str, corpus: Dict[str, str], width: int = 800) -> Optional[str]:
    """/query returns only a 120-char chunk_preview. For the synthetic corpus we know the
    full text, so re-expand the preview to the chunk window. Returns None if not found."""
    text = corpus.get(filename)
    p = (preview or "").rstrip("…").strip()
    if not text or not p:
        return None
    i = text.find(p[:60])
    return text[i:i + width] if i >= 0 else None


def build_record(item: dict, res: dict, corpus: Dict[str, str]) -> dict:
    body = res["body"]
    ok = res["status"] < 300
    chunks = []
    raw = body.get("retrieved_chunks") or body.get("chunks")
    if ok and isinstance(raw, list):  # preferred: pipeline exposes real chunks
        for i, c in enumerate(raw):
            chunks.append({"id": c.get("id") or c.get("chunk_id"), "doc": c.get("doc") or c.get("document") or c.get("filename"),
                           "text": c.get("text") or c.get("page_content") or "", "score": c.get("score"), "text_source": "pipeline"})
    elif ok:
        for s in body.get("sources", []):
            full = expand_preview(s.get("chunk_preview", ""), s.get("document", ""), corpus)
            chunks.append({"id": None, "doc": s.get("document"), "page": s.get("page"), "score": None,
                           "text": full or s.get("chunk_preview", ""),
                           "text_source": "expanded_from_corpus" if full else "preview_only"})
    flags = body.get("verification_flags") or []
    return {
        "id": item["id"], "question": item["question"], "status": res["status"],
        "error": None if ok else str(body.get("detail"))[:300],
        "answer": body.get("answer", "") if ok else "",
        "retrieved": chunks,
        "citations": body.get("citations"),  # None => field absent
        "model_used": body.get("model"), "latency_ms": round(res["latency_ms"], 1),
        "ttft_ms": body.get("ttft_ms"), "tokens": body.get("usage") or body.get("tokens"),
        "blocked": bool(ok and (body.get("model") == GUARDRAIL_MODEL or any(str(f).startswith("GUARDRAIL_TRIGGERED") for f in flags))),
        "extra": {k: v for k, v in body.items() if k not in KNOWN_KEYS and k not in ("sources", "citations", "retrieved_chunks", "chunks")} if ok else {},
    }


def completed_ids(out_path: Path) -> set:
    """ids already harvested in an existing JSONL (tolerates a truncated last line)."""
    done = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                done.add(json.loads(line)["id"])
            except Exception:
                continue
    return done


def harvest(client, items: List[dict], out_path: Path, email=None, password=None, top_k=4, ingest=True,
            resume=False) -> Path:
    """resume=True: keep the records already in out_path and only query the missing items (appends), so an
    interrupted run (rate limit / daily token cap) does not repeat paid LLM calls."""
    done = completed_ids(out_path) if resume else set()
    if resume and out_path.exists():  # drop a truncated trailing line so appended records stay valid JSONL
        good = []
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                json.loads(line)
                good.append(line)
            except Exception:
                continue
        out_path.write_text("".join(g + chr(10) for g in good), encoding="utf-8")
    items = [it for it in items if it["id"] not in done]
    corpus_docs = load_corpus()
    corpus = {d["id"]: d["text"] for d in corpus_docs}
    sess = Session(client, email, password)
    try:
        if ingest:
            sess.ingest(corpus_docs)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a" if resume else "w", encoding="utf-8") as f:
            for it in items:
                rec = build_record(it, sess.query(it["question"], top_k), corpus)
                if rec["status"] >= 500:
                    raise HarvestError(f"pipeline error on {it['id']}: {rec['error']}")
                f.write(json.dumps(rec) + "\n")
                f.flush()
                time.sleep(2.1)  # stay under the 30/min limit
    finally:
        sess.cleanup()
    return out_path
