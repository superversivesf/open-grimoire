# Open Grimoire app image
FROM python:3.12-slim AS base

# System deps for PDF processing: poppler (pdf2image), tesseract (OCR)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy project files (needed for pip install)
COPY pyproject.toml ./
COPY app/ ./app/

# Install Python deps
RUN pip install --no-cache-dir -e ".[dev]"

COPY config.yaml ./

# Create runtime dirs (writable volumes mount over these)
RUN mkdir -p /app/data /app/db

# Default env vars (overridable at runtime)
ENV OLLAMA_HOST=http://ollama:11434 \
    DATA_DIR=/app/data \
    DB_DIR=/app/db \
    HOST=0.0.0.0 \
    PORT=8000

EXPOSE 8000

# Run as non-root for safety
RUN useradd -r -s /bin/bash appuser && chown -R appuser:appuser /app
USER appuser

CMD ["python", "-m", "app"]