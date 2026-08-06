A self-hosted web application for uploading RPG PDF manuals, processing them into searchable tiered markdown with FTS5 full-text search, and asking conversational questions answered by an LLM via Ollama — with cited sources linking back to the original PDF.

## Features

- **PDF Processing Pipeline** — Upload PDFs and the 5-stage pipeline automatically extracts text, detects structure, generates tiered markdown, enriches each section with AI-generated summaries and keywords, and builds a full-text search index
- **Conversational Q&A** — Ask questions about your manuals in natural language. The agent loop searches the collection, reads relevant sections, and synthesizes an answer with citations
- **Citation Links** — Source citations link directly to the original PDF at the correct page, opening in a new tab
- **Book Covers** — First page of each PDF is extracted as a cover thumbnail for the collection grid view
- **Multi-User Support** — Each user gets isolated storage with a 1GB quota. Users cannot see or access each other's collections
- **Collection Management** — Organize books into collections. Search and Q&A are scoped to the active collection
- **Session History** — Conversations are saved per session with follow-up question suggestions
- **Dark RPG Theme** — Custom dark fantasy themed UI built on Pico.css

## Architecture

### Processing Pipeline (5 stages)

1. **Extract** — Extract text from PDF pages using pdfplumber (with optional OCR via pytesseract)
2. **Structure** — Detect document structure (chapters, sections) using an LLM
3. **Tier** — Write tiered markdown files (flat `.md` files with front-matter)
4. **Enrich** — Generate summary and keywords for each section using an LLM
5. **Index** — Build FTS5 full-text search index across all sections

### Agent Loop

The query agent uses an 8-tool palette to search and read documents:

| Tool | Purpose |
|------|---------|
| `fts_search` | Full-text search across the collection |
| `read_file` | Read a markdown file's full content |
| `grep` | Regex search across all files |
| `list_index` | Browse document hierarchy |
| `ls` | List files in a directory |
| `table_extract` | Parse markdown tables into JSON |
| `calc` | Evaluate dice notation and arithmetic |
| `done` | Submit final answer with citations |

The loop includes deduplication (won't re-read files or repeat searches), forced termination after 8 iterations, and a fallback answer synthesizer for when the budget is exhausted.

### Storage

- **SQLite** per user — each user gets their own database with collections, docs, sessions, and FTS5 index
- **Filesystem** per user — markdown files and PDFs stored under `data/{user_id}/{doc_id}/`
- **Shared SQLite** — user accounts and job queue in a shared database

## Quick Start

### Prerequisites

- Python 3.11+
- [Ollama](https://ollama.ai) running locally with at least one model that supports tool calling (e.g. `qwen2.5:7b`, `deepseek-v4-flash:cloud`)
- [poppler](https://poppler.freedesktop.org/) for PDF cover extraction (`apt install poppler-utils`)
- [tesseract](https://github.com/tesseract-ocr/tesseract) for optional OCR (`apt install tesseract-ocr`)

### Installation

```bash
git clone https://github.com/superversivesf/open-grimoire.git
cd open-grimoire
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Configuration

Edit `config.yaml`:

```yaml
ollama:
  host: http://localhost:11434

models:
  query: qwen2.5:7b        # model for Q&A agent loop
  enrich: qwen2.5:7b       # model for section enrichment
  structure: qwen2.5:7b    # model for structure detection
  vision: gemma3:4b        # model for OCR fallback

options:
  num_ctx: 16384           # context window size

paths:
  data_dir: ./data
  db_dir: ./db

server:
  host: 0.0.0.0
  port: 8050
  secret: change-me-in-production
```

### Create Admin User

```bash
python -m app.cli.user create --username admin --password admin --admin
```

### Run

```bash
python -m app
```

Open `http://localhost:8050` and log in.

## Usage

1. **Create a collection** — e.g. "Dragon Warriors"
2. **Upload PDFs** — drag and drop RPG manual PDFs into the collection
3. **Wait for processing** — the pipeline runs automatically (cover extraction, text extraction, structure detection, enrichment, FTS indexing)
4. **Ask questions** — type a question in the ask box, get an answer with cited sources
5. **Click citations** — opens the original PDF at the relevant page in a new tab
6. **Follow up** — click suggested questions to dig deeper

## Testing

```bash
# Run all tests (mocked LLM)
pytest tests/ -v

# Run e2e tests with real Ollama
pytest tests/test_e2e_journey.py -v -m e2e
```

187 tests total, including 38 end-to-end journey tests covering the full user experience from registration through Q&A.

## Deployment

### Docker Compose (Production + Test)

Two environments on the same server — production (port 8050) and test/staging (port 8051).

**Prerequisites:**
- Docker + Docker Compose installed
- Ollama running on the host (`ollama serve`)
- Pull required models: `ollama pull phi4-mini:3.8b deepseek-v4-flash:cloud`

**Deploy:**

```bash
# Deploy both prod + test
./scripts/deploy.sh all

# Or deploy individually
./scripts/deploy.sh prod    # port 8050
./scripts/deploy.sh test    # port 8051

# Check status
./scripts/deploy.sh status

# Stop
./scripts/deploy.sh stop
```

**First-time setup:**
- Both environments auto-create an `admin/admin` user on first deploy
- **Change the admin password immediately**: `docker exec open-grimoire-prod python -m app.cli.user passwd --username admin --password 'new-password'`
- Create a `.env` file with secure session secrets:
  ```
  SESSION_SECRET=your-random-prod-secret
  TEST_SESSION_SECRET=your-random-test-secret
  ```

**Nginx config** (for HTTPS on the test subdomain):
```nginx
server {
    listen 443 ssl;
    server_name test.grim.superversive.net;
    ssl_certificate     /etc/letsencrypt/live/test.grim.superversive.net/fullchain.pem;
    ssl_certificate_key  /etc/letsencrypt/live/test.grim.superversive.net/privkey.pem;
    location / {
        proxy_pass http://localhost:8051;
        client_max_body_size 100M;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

### Backup

```bash
# Manual backup
./scripts/backup.sh /opt/backups/open-grimoire

# Nightly backup (add to crontab)
# 0 3 * * * /home/jason/Repos/rpg-master/scripts/backup.sh /opt/backups/open-grimoire

# Restore from backup
./scripts/restore.sh /opt/backups/open-grimoire/20260806-120000.tar.gz
```

Backups include:
- SQLite databases (using `sqlite3 .backup` for consistency)
- Data directories (PDFs, markdown, covers)
- Config files

Keeps last 7 days of backups locally. Compressed with timestamp.

## Tech Stack

- **Backend**: FastAPI, Uvicorn, SQLite (FTS5)
- **PDF Processing**: pdfplumber, pypdf, pdf2image, pytesseract
- **LLM Gateway**: Ollama (local or cloud models)
- **Frontend**: Jinja2 templates, Pico.css, HTMX, custom dark RPG theme
- **CLI**: Click

## License

This work is licensed under a [Creative Commons Attribution-NonCommercial 4.0 International License](LICENSE).

You are free to:
- **Share** — copy and redistribute the material in any medium or format
- **Adapt** — remix, transform, and build upon the material

Under the following terms:
- **Attribution** — you must give appropriate credit
- **NonCommercial** — you may not use the material for commercial purposes