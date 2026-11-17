# syntax=docker/dockerfile:1.7
# Build context is the REPO ROOT (see docker-compose.yml): docker build -f VENUAI/Dockerfile .
#
# Pin the base by digest for reproducible, tamper-evident builds:
#   docker buildx imagetools inspect <image>:<tag>   (prints the digest to use in FROM)
ARG PYTHON_IMAGE=python:3.12-slim

# ---- builder: has compilers, never shipped ----------------------------------
FROM ${PYTHON_IMAGE} AS builder
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*
RUN python -m venv /opt/venv
COPY VENUAI/requirements.lock /tmp/requirements.lock
# Hash-verified install: any tampered or unlisted artifact fails the build.
RUN /opt/venv/bin/pip install --require-hashes -r /tmp/requirements.lock

# ---- runtime: slim, non-root, no build tools --------------------------------
FROM ${PYTHON_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    APP_ENV=production \
    UVICORN_WORKERS=1 \
    FORWARDED_ALLOW_IPS=127.0.0.1 \
    HF_HOME=/app/VENUAI/data/hf_cache
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin appuser
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=root:root VENUAI ./VENUAI

# Every path the app writes to lives under data/ or a mounted volume, so the root
# filesystem can be read-only. The two JSON stores that the code keeps next to the
# source are symlinked into the writable data/ volume.
RUN mkdir -p /app/VENUAI/data /app/VENUAI/chroma_db /app/VENUAI/uploaded_docs /app/VENUAI/uploaded_datasets \
    && ln -sf /app/VENUAI/data/doc_metadata.json /app/VENUAI/doc_metadata.json \
    && ln -sf /app/VENUAI/data/datasets.json /app/VENUAI/datasets.json \
    && chown -R 10001:10001 /app/VENUAI/data /app/VENUAI/chroma_db /app/VENUAI/uploaded_docs /app/VENUAI/uploaded_datasets

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status==200 else 1)"]

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["sh", "-c", "exec uvicorn VENUAI.rag_server:app --host 0.0.0.0 --port 8000 --workers ${UVICORN_WORKERS} --proxy-headers --forwarded-allow-ips \"${FORWARDED_ALLOW_IPS}\""]
