FROM python:3.12-slim

WORKDIR /app

# System dependencies for PDF processing
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY pyproject.toml .
COPY app ./app
RUN pip install --no-cache-dir -e ".[dev]"

# Copy app code
COPY . .

# Expose port (overridden per environment)
EXPOSE 8050

# Run the app
CMD ["python", "-m", "app"]