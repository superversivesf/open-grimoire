"""Centralized application constants.

All hardcoded limits, timeouts, and magic numbers should be defined here.
Values can be overridden via environment variables where appropriate.
"""

# ─── Upload / Storage Limits ──────────────────────────────────────
MAX_UPLOAD_BYTES = 200 * 1024 * 1024          # 200 MB per file
USER_STORAGE_LIMIT = 1024 * 1024 * 1024       # 1 GB per user

# ─── Pipeline / Processing ────────────────────────────────────────
WORKER_POLL_INTERVAL = 2.0                    # seconds between queue polls
JOB_LEASE_SECONDS = 300                       # lease duration for a claimed job
MAX_JOB_ATTEMPTS = 3                          # max claim attempts before giving up
REGISTER_RATE_LIMIT = "5/hour"                # slowapi limit for /register
OLLAMA_TIMEOUT = 300.0                        # seconds for Ollama HTTP requests
READYZ_TIMEOUT = 2.0                          # seconds for /readyz health checks
ENRICH_SUMMARY_MAX_CHARS = 60                 # chars for enrichment summary preview
ENRICH_EST_OUTPUT_TOKENS_PER_SECTION = 60     # estimated output tokens per enriched section
COVER_DPI = 150                               # DPI for cover image extraction

# ─── Session / Auth ───────────────────────────────────────────────
SESSION_TTL_SECONDS = 86400                   # 24 hours
SESSION_COOKIE_MAX_AGE = 86400                # 24 hours
LOGIN_RATE_LIMIT = "5/minute"                 # slowapi rate limit string

# ─── Agent Loop ───────────────────────────────────────────────────
DEFAULT_MAX_ITERATIONS = 15                   # default max agent iterations
STATE_MAX_ITERATIONS = {                      # per-state iteration limits
    "searching": 5,
    "reading": 5,
    "synthesizing": 3,
}

# ─── Sandbox / Tools ──────────────────────────────────────────────
SANDBOX_TRUNCATE_CHARS = 16000                # max chars for tool results

# ─── Ollama / Models ──────────────────────────────────────────────
DEFAULT_NUM_CTX = 32768                       # default context window

# ─── Enrichment Scoring (for reference) ──────────────────────────
ENRICH_OPTIMAL_WORDS_MIN = 10
ENRICH_OPTIMAL_WORDS_MAX = 50
ENRICH_OPTIMAL_KEYWORDS = 8
ENRICH_OPTIMAL_ANSWER_WORDS_MIN = 50
ENRICH_OPTIMAL_ANSWER_WORDS_MAX = 150