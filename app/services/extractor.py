"""HTML content extraction, noise stripping, normalization, and SHA-256 hashing."""

from dataclasses import dataclass
import hashlib
import re
from bs4 import BeautifulSoup, Comment, Tag


# Tags that never represent human-readable meaningful content
ALWAYS_STRIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "object",
    "embed",
    "audio",
    "video",
    "source",
    "track",
    "map",
    "area",
    "meta",
    "link",
}

# Structural noise selectors stripped when monitoring a full page (not a specific CSS selector)
DEFAULT_NOISE_SELECTORS = (
    "nav",
    "footer",
    "aside",
    "[role='navigation']",
    "[role='banner']",
    "[role='contentinfo']",
    "[role='complementary']",
    "[aria-hidden='true']",
    ".cookie-banner",
    "#cookie-banner",
    ".cookie-consent",
    "#cookie-consent",
    ".ad",
    ".ads",
    ".advertisement",
    ".sponsored",
    "[data-ad]",
    "[id*='google_ads']",
    ".timestamp",
    ".live-counter",
    ".view-count",
    ".visitor-count",
    ".session-id",
    ".csrf-token",
)

# Regex patterns for volatile inline text noise (timestamps, counters, runtime stats)
VOLATILE_TEXT_PATTERNS = [
    # Relative timestamps: "updated 5 mins ago", "posted 2 seconds ago", "3 hours ago"
    re.compile(
        r"\b(?:updated|posted|fetched|refreshed|active)?\s*\d+\s+"
        r"(?:second|sec|minute|min|hour|hr)s?\s+ago\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bjust\s+now\b", re.IGNORECASE),
    # ISO / RFC timestamps with seconds or milliseconds (e.g., 2026-10-17T00:25:12.345Z)
    re.compile(
        r"\b\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?\b"
    ),
    # Clock times with seconds: "14:23:09 UTC", "09:15:42 GMT"
    re.compile(r"\b\d{1,2}:\d{2}:\d{2}\s*(?:AM|PM|UTC|GMT|EST|PST|IST)?\b", re.IGNORECASE),
    # Dynamic view/visitor/online counters: "1,234 views", "42 users online", "visitors: 9812"
    re.compile(
        r"\b(?:\d[\d,]*\s+(?:views|visitors|users\s+online|active\s+users|page\s+views)"
        r"|(?:views|visitors|online\s+users)\s*:\s*\d[\d,]*)\b",
        re.IGNORECASE,
    ),
    # Page generation / render timing noise: "Generated in 0.042s", "Render time: 14ms"
    re.compile(
        r"\b(?:generated|rendered|page\s+load|execution\s+time)\s*(?:in|:)?\s*\d+(?:\.\d+)?\s*(?:ms|s|seconds|milliseconds)\b",
        re.IGNORECASE,
    ),
]

BLOCK_TAGS = {
    "p",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "li",
    "tr",
    "blockquote",
    "pre",
    "article",
    "section",
    "div",
    "header",
    "main",
    "td",
    "th",
    "dt",
    "dd",
    "figcaption",
}


@dataclass
class ExtractedContent:
    """Result of parsing and normalizing an HTML document."""

    title: str
    normalized_text: str
    content_hash: str
    char_count: int
    word_count: int


class ExtractionError(ValueError):
    """Raised when HTML extraction fails (e.g., target CSS selector matches nothing)."""


def normalize_text(raw_text: str) -> str:
    """Normalize whitespace and strip volatile inline noise from extracted text."""
    if not raw_text:
        return ""

    cleaned = raw_text
    for pattern in VOLATILE_TEXT_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        # Collapse horizontal whitespace into a single space
        line = re.sub(r"[ \t\r\f\v]+", " ", raw_line).strip()
        # Skip lines that are purely punctuation/separators after stripping timestamps
        if not line or re.fullmatch(r"[\-|•:|,/\\]+", line):
            continue
        lines.append(line)

    return "\n".join(lines)


def compute_content_hash(normalized_text: str) -> str:
    """Calculate a deterministic SHA-256 hex digest of the normalized text."""
    return hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()


def extract_meaningful_content(
    html: str,
    *,
    css_selector: str | None = None,
    ignore_selector: str | None = None,
) -> ExtractedContent:
    """Extract readable, noise-free content from HTML and compute its SHA-256 hash.

    1. Parses HTML with BeautifulSoup.
    2. Removes comments, scripts, styles, ads, and volatile elements.
    3. Removes any user-configured `ignore_selector` elements.
    4. If `css_selector` is provided, extracts only matching regions; otherwise
       strips global navigation/footer noise and extracts main readable content.
    5. Normalizes whitespace and volatile strings, then hashes the result.
    """
    soup = BeautifulSoup(html or "", "lxml")

    title_text = ""
    if soup.title and soup.title.string:
        title_text = re.sub(r"\s+", " ", soup.title.string).strip()[:500]

    # 1. Strip HTML comments
    for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
        comment.extract()

    # 2. Strip non-content tags globally
    for tag in soup.find_all(ALWAYS_STRIP_TAGS):
        tag.decompose()

    # 3. Strip user-configured ignore regions first
    if ignore_selector and ignore_selector.strip():
        try:
            for ignored in soup.select(ignore_selector.strip()):
                ignored.decompose()
        except Exception as exc:
            raise ExtractionError(f"Invalid ignore CSS selector '{ignore_selector}': {exc}") from exc

    # 4. Determine target scope
    target_roots: list[Tag] = []
    if css_selector and css_selector.strip():
        try:
            target_roots = list(soup.select(css_selector.strip()))
        except Exception as exc:
            raise ExtractionError(f"Invalid target CSS selector '{css_selector}': {exc}") from exc

        if not target_roots:
            raise ExtractionError(
                f"CSS selector '{css_selector}' did not match any elements on the page."
            )

        # Even inside a target selector, remove known ad/timestamp/counter noise classes
        for root in target_roots:
            for noisy_sel in (
                ".ad",
                ".ads",
                ".advertisement",
                ".timestamp",
                ".live-counter",
                ".view-count",
                ".visitor-count",
                "[aria-hidden='true']",
            ):
                for node in root.select(noisy_sel):
                    if node is not root:
                        node.decompose()
    else:
        # Full-page extraction: strip global navigation, footer, ads, cookie banners, timestamps
        for selector in DEFAULT_NOISE_SELECTORS:
            for node in soup.select(selector):
                node.decompose()

        body = soup.body or soup
        target_roots = [body]

    # 5. Extract structured text with newline separation between block elements
    extracted_chunks: list[str] = []
    for root in target_roots:
        for br in root.find_all("br"):
            br.replace_with("\n")
        for block in root.find_all(BLOCK_TAGS):
            block.insert_before("\n")
            block.insert_after("\n")
        text_piece = root.get_text(separator=" ", strip=False)
        if text_piece:
            extracted_chunks.append(text_piece)

    combined_raw = "\n".join(extracted_chunks)
    normalized = normalize_text(combined_raw)

    # Fallback: if page had only a title or minimal inline body and no block text remained
    if not normalized and title_text and not css_selector:
        normalized = normalize_text(title_text)

    content_hash = compute_content_hash(normalized)
    words = normalized.split() if normalized else []

    return ExtractedContent(
        title=title_text,
        normalized_text=normalized,
        content_hash=content_hash,
        char_count=len(normalized),
        word_count=len(words),
    )


def match_keywords_in_change(
    keywords_csv: str | None,
    new_text: str,
    added_text_only: str | None = None,
) -> list[str]:
    """Return list of configured keywords found in the updated content (or added lines)."""
    if not keywords_csv:
        return []

    raw_keywords = [kw.strip() for kw in keywords_csv.split(",") if kw.strip()]
    if not raw_keywords:
        return []

    # Check both newly added lines and full updated text (case-insensitive)
    search_corpus = f"{added_text_only or ''}\n{new_text}".lower()
    matched: list[str] = []
    for kw in raw_keywords:
        if kw.lower() in search_corpus and kw not in matched:
            matched.append(kw)
    return matched
