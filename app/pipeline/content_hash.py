"""Content hashing for book deduplication.

Strips DriveThruRPG watermarks from PDF text before hashing, so two users
uploading the same book get the same hash even if their copies are watermarked
differently.

Watermark patterns stripped:
- Email addresses (common watermark format)
- Buyer name lines
- "Purchased by" / "Prepared for" lines
- Transaction/order IDs
- URLs containing drive thru
"""
import re
import hashlib


# Watermark patterns to strip
WATERMARK_PATTERNS = [
    re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b', re.IGNORECASE),  # email addresses
    re.compile(r'(purchased by|prepared for|downloaded by|bought by)\s*:?\s*[^\n]+', re.IGNORECASE),
    re.compile(r'(order|transaction)\s*#?\s*:?\s*\w+', re.IGNORECASE),
    re.compile(r'https?://\S*drivethru\S*', re.IGNORECASE),
    re.compile(r'https?://\S*dtrpg\S*', re.IGNORECASE),
    re.compile(r'watermark\s*:?\s*[^\n]+', re.IGNORECASE),
    # DriveThruRPG specific: buyer name on each page header/footer
    re.compile(r'\bprepared\s+for\s+[\w\s]+\b', re.IGNORECASE),
    re.compile(r'\bpurchased\s+by\s+[\w\s]+\b', re.IGNORECASE),
    # Per-page unique IDs (often alphanumeric codes)
    re.compile(r'\b[A-F0-9]{8,}\b', re.IGNORECASE),  # hex IDs
    # Date stamps in watermarks
    re.compile(r'\d{4}-\d{2}-\d{2}', re.IGNORECASE),
]

# Lines that are purely watermark noise
WATERMARK_LINE_PATTERNS = [
    re.compile(r'^\s*purchased by\b', re.IGNORECASE),
    re.compile(r'^\s*prepared for\b', re.IGNORECASE),
    re.compile(r'^\s*downloaded by\b', re.IGNORECASE),
    re.compile(r'^\s*watermark\b', re.IGNORECASE),
    re.compile(r'^\s*order\s*#', re.IGNORECASE),
    # Buyer name lines (often "Prepared for First Last")
    re.compile(r'^\s*prepared\s+for\s+\w+\s+\w+\s*$', re.IGNORECASE),
    re.compile(r'^\s*purchased\s+by\s+\w+\s+\w+\s*$', re.IGNORECASE),
    # Page-specific watermark lines
    re.compile(r'^\s*[A-F0-9]{8,}\s*$', re.IGNORECASE),
]


def strip_watermarks(text: str) -> str:
    """Remove watermark text from extracted PDF text."""
    # Remove inline watermark patterns
    for pattern in WATERMARK_PATTERNS:
        text = pattern.sub('', text)
    # Remove lines that are purely watermark
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        stripped = line.strip()
        if any(p.match(stripped) for p in WATERMARK_LINE_PATTERNS):
            continue
        cleaned_lines.append(line)
    # Normalize whitespace
    text = '\n'.join(cleaned_lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def _normalize_for_hash(text: str) -> str:
    """Normalize text for consistent hashing across PDF extractions.

    Splits into words, lowercases, and joins with single space.
    This preserves semantic content while being robust to line breaks,
    spacing differences, and page boundary variations in PDF extraction.
    """
    words = re.findall(r'\w+', text.lower())
    return ' '.join(words)


def content_hash(text: str) -> str:
    """SHA-256 hash of watermark-stripped, normalized text."""
    stripped = strip_watermarks(text)
    normalized = _normalize_for_hash(stripped)
    return hashlib.sha256(normalized.encode()).hexdigest()