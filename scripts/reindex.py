"""Re-embed the Chroma collection with a different embedding model into a NEW directory.

Dry-run by default (counts + plan only). With --run it reads the live collection read-only and writes
a fresh Chroma directory; the live chroma_db is never modified and nothing is swapped automatically.
After verifying, switch by setting EMBEDDING_MODEL=<model> and pointing the server's CHROMA dir at the
new directory (see Documents/AI/retrieval.md).

    python scripts/reindex.py --model BAAI/bge-small-en-v1.5            # dry run
    python scripts/reindex.py --model BAAI/bge-small-en-v1.5 --run      # writes chroma_db_reindexed_<model>
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
COLLECTION = "drdo_knowledge_base"


def plan(src_dir: Path, model: str, target: Path) -> dict:
    import chromadb
    col = chromadb.PersistentClient(path=str(src_dir)).get_collection(COLLECTION)
    return {"source": str(src_dir), "chunks": col.count(), "stored_model": (col.metadata or {}).get("embedding_model"),
            "new_model": model, "target": str(target)}


def reindex(src_dir: Path, model: str, target: Path, batch: int = 64, embedder=None) -> int:
    import chromadb
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"target {target} exists and is not empty; refusing to overwrite")
    src = chromadb.PersistentClient(path=str(src_dir)).get_collection(COLLECTION)
    dst = chromadb.PersistentClient(path=str(target)).get_or_create_collection(
        COLLECTION, metadata={"embedding_model": model})
    if embedder is None:
        from langchain_community.embeddings import HuggingFaceEmbeddings
        embedder = HuggingFaceEmbeddings(model_name=model, model_kwargs={"device": "cpu"},
                                         encode_kwargs={"normalize_embeddings": True})
    n, total = 0, src.count()
    while n < total:
        data = src.get(limit=batch, offset=n, include=["documents", "metadatas"])
        ids, docs, metas = data["ids"], data["documents"], data["metadatas"]
        if not ids:
            break
        texts = [((m or {}).get("context_prefix") or "") + ("\n" if (m or {}).get("context_prefix") else "") + d
                 for d, m in zip(docs, metas)]
        dst.upsert(ids=ids, documents=docs, metadatas=metas, embeddings=embedder.embed_documents(texts))
        n += len(ids)
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", required=True)
    ap.add_argument("--source", default=str(ROOT / "VENUAI" / "chroma_db"))
    ap.add_argument("--target", default=None)
    ap.add_argument("--run", action="store_true", help="actually write the new index (default: dry run)")
    a = ap.parse_args(argv)
    slug = re.sub(r"[^a-z0-9]+", "-", a.model.lower()).strip("-")
    target = Path(a.target) if a.target else ROOT / "VENUAI" / f"chroma_db_reindexed_{slug}"
    info = plan(Path(a.source), a.model, target)
    print(info)
    if not a.run:
        print("dry run: nothing written. Re-run with --run to build the new index.")
        return 0
    print(f"re-indexed {reindex(Path(a.source), a.model, target)} chunks into {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
