FROM python:3.12-slim AS base

# System deps for pypdf/sentence-transformers wheels and bcrypt build fallback.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY VENUAI/requirements.txt ./VENUAI/requirements.txt
RUN pip install --no-cache-dir -r VENUAI/requirements.txt

COPY VENUAI ./VENUAI

RUN useradd --create-home --uid 1000 appuser \
    && mkdir -p /app/VENUAI/data /app/VENUAI/chroma_db /app/VENUAI/uploaded_docs \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health', timeout=3).status==200 else 1)"

CMD ["uvicorn", "VENUAI.rag_server:app", "--host", "0.0.0.0", "--port", "8000"]
