# ── builder: install runtime deps + app into a venv (no dev tooling) ────────
FROM python:3.12-slim AS builder

WORKDIR /build

# All runtime deps ship cp312 manylinux/pure wheels, so no build-essential needed.
COPY pyproject.toml ./
COPY app ./app

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .

# ── runtime: slim image with only runtime libs + the venv ───────────────────
FROM python:3.12-slim

# System libraries for PDF processing: poppler (pdf2image) + tesseract (OCR).
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# App + dependencies live in the venv; templates/static are bundled inside the
# installed package, so the source tree is not copied into the runtime image.
COPY --from=builder /opt/venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH

WORKDIR /app

# Default config (overridden in production by a mounted config.yaml). Kept
# only so the image runs standalone; secrets come from the environment.
COPY config.yaml ./

EXPOSE 8050

CMD ["python", "-m", "app"]