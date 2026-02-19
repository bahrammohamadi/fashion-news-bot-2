# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    7.3
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# Changes vs 7.2:
#   [1] Cross-source duplicate detection
#       Level 1: exact link match (existing)
#       Level 2: content_hash — SHA256 of normalized title
#                catches same article on different sites
#       Level 3: fuzzy title match — token Jaccard similarity
#                catches paraphrased/reworded titles
#                runs against last N titles in DB (recent window)
#       Level 4: one article per domain per run
#                prevents same-source flooding
#   [2] Appwrite schema: content_hash field added
#       String, size 64, not required
#       Add to collection before deploying
#
# Duplicate protection summary:
#   is_duplicate()          → exact link
#   is_duplicate_by_hash()  → content_hash (normalized title)
#   is_fuzzy_duplicate()    → token overlap vs recent DB titles
#   domain dedup            → in-memory during candidate selection
# ============================================================

import os
import re
import time
import hashlib
import asyncio
import warnings
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from telegram import Bot, InputMediaPhoto, LinkPreviewOptions
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lsa import LsaSummarizer
from sumy.nlp.stemmers import Stemmer
from sumy.utils import get_stop_words

warnings.filterwarnings("ignore", category=DeprecationWarning, module="appwrite")


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

COLLECTION_ID     = "history"
SOURCE_TYPE       = "en"
ARTICLE_AGE_HOURS = 36
CAPTION_MAX       = 1020
MAX_SCRAPED_CHARS = 3000
MAX_RSS_CHARS     = 1000
MIN_CONTENT_CHARS = 150

# Appwrite schema field size limits
DB_LINK_MAX         = 999
DB_TITLE_MAX        = 499
DB_FEED_URL_MAX     = 499
DB_SOURCE_TYPE_MAX  = 19
DB_HASH_MAX         = 64    # SHA256 hex = exactly 64 chars

# Timeout budgets (seconds)
FEED_FETCH_TIMEOUT  = 7
FEEDS_SCAN_TIMEOUT  = 20
SCRAPE_TIMEOUT      = 12
TRANSLATION_TIMEOUT = 40
TELEGRAM_TIMEOUT    = 25

# Summarizer
SUMMARY_SENTENCES = 8

# MyMemory translation
MYMEMORY_CHUNK_SIZE  = 450
MYMEMORY_CHUNK_DELAY = 1.0
MYMEMORY_EMAIL       = ""   # set for 50000 chars/day free

# Image scraping
MAX_IMAGES       = 10
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_BLOCKLIST  = [
    "doubleclick", "googletagmanager", "googlesyndication",
    "facebook.com/tr", "analytics", "pixel", "beacon",
    "tracking", "counter", "stat.", "stats.",
]

# ── Cross-source duplicate detection ──
# Fuzzy title similarity threshold (0.0–1.0)
# 0.65 = 65% token overlap required to flag as duplicate
# Lower = more aggressive blocking
# Higher = more permissive (may allow near-duplicates through)
FUZZY_SIMILARITY_THRESHOLD = 0.65

# How many recent DB records to load for fuzzy comparison
# More = better detection, slower DB query
FUZZY_LOOKBACK_COUNT = 50

# Stop words removed before title comparison
# (common words that add noise to similarity scoring)
TITLE_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "its", "it", "this", "that", "these", "those", "and", "or",
    "but", "as", "up", "out", "if", "about", "into", "over",
    "after", "new", "first", "last", "says", "said",
}

# Trend scoring
SCORE_RECENCY_MAX   = 40
SCORE_TITLE_KEYWORD = 15
SCORE_HAS_IMAGE     = 10
SCORE_DESC_LENGTH   = 10

TREND_KEYWORDS = [
    "launches", "unveils", "debuts", "announces", "names",
    "acquires", "appoints", "partners", "expands", "opens",
    "trend", "collection", "season", "runway", "fashion week",
    "capsule", "collab", "collaboration", "limited edition",
    "viral", "popular", "iconic", "exclusive", "first look",
    "top", "best", "most", "new", "latest",
    "chanel", "dior", "gucci", "prada", "louis vuitton",
    "zara", "h&m", "nike", "adidas", "balenciaga",
    "versace", "fendi", "burberry", "valentino", "armani",
]

RSS_FEEDS = [
    "https://www.vogue.com/feed/rss",
    "https://wwd.com/feed/",
    "https://fashionista.com/feed",
    "https://www.harpersbazaar.com/rss/fashion.xml",
    "https://www.elle.com/rss/fashion.xml",
    "https://www.businessoffashion.com/feed/",
    "https://www.thecut.com/feed",
    "https://www.refinery29.com/rss.xml",
    "https://www.whowhatwear.com/rss",
    "https://feeds.feedburner.com/fibre2fashion/fashion-news",
    "https://www.gq.com/feed/style/rss",
    "https://www.cosmopolitan.com/rss/fashion.xml",
    "https://www.instyle.com/rss/fashion.xml",
    "https://www.marieclaire.com/rss/fashion.xml",
    "https://www.vanityfair.com/feed/style/rss",
    "https://www.allure.com/feed/fashion/rss",
    "https://www.teenvogue.com/feed/rss",
    "https://www.glossy.co/feed/",
    "https://www.highsnobiety.com/feed/",
    "https://fashionmagazine.com/feed/",
]


# ─────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────

async def main(event=None, context=None):
    print("[INFO] ═══ Function 1 v7.3 started ═══")
    loop       = asyncio.get_event_loop()
    start_time = loop.time()

    def elapsed():
        return round(loop.time() - start_time, 1)

    # ── Environment variables ──
    token             = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id           = os.environ.get("TELEGRAM_CHANNEL_ID")
    appwrite_endpoint = os.environ.get(
        "APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1"
    )
    appwrite_project  = os.environ.get("APPWRITE_PROJECT_ID")
    appwrite_key      = os.environ.get("APPWRITE_API_KEY")
    database_id       = os.environ.get("APPWRITE_DATABASE_ID")

    missing = [
        k for k, v in {
            "TELEGRAM_BOT_TOKEN":   token,
            "TELEGRAM_CHANNEL_ID":  chat_id,
            "APPWRITE_PROJECT_ID":  appwrite_project,
            "APPWRITE_API_KEY":     appwrite_key,
            "APPWRITE_DATABASE_ID": database_id,
        }.items() if not v
    ]
    if missing:
        print(f"[ERROR] Missing env vars: {missing}")
        return {"status": "error", "missing_vars": missing}

    bot = Bot(token=token)

    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)
    sdk_mode  = "new" if hasattr(databases, "list_rows") else "legacy"

    print(f"[INFO] SDK mode: {sdk_mode}")

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)

    # ════════════════════════════════════════════════
    # PRE-LOAD: Fetch recent titles from DB for fuzzy matching
    # Done once at startup — shared across all candidate checks
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Loading recent titles for fuzzy check...")
    recent_titles = load_recent_titles(
        databases, database_id, COLLECTION_ID,
        sdk_mode, FUZZY_LOOKBACK_COUNT,
    )
    print(f"[INFO] [{elapsed()}s] {len(recent_titles)} recent titles loaded.")

    # ════════════════════════════════════════════════
    # PHASE 1 — PARALLEL RSS SCAN + SCORING
    # ════════════════════════════════════════════════
    print(
        f"[INFO] [{elapsed()}s] Phase 1: "
        f"Scanning {len(RSS_FEEDS)} feeds..."
    )

    try:
        candidate = await asyncio.wait_for(
            find_best_candidate(
                feeds=RSS_FEEDS,
                databases=databases,
                database_id=database_id,
                collection_id=COLLECTION_ID,
                time_threshold=time_threshold,
                sdk_mode=sdk_mode,
                now=now,
                recent_titles=recent_titles,
            ),
            timeout=FEEDS_SCAN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Feed scan timed out.")
        candidate = None

    print(f"[INFO] [{elapsed()}s] Phase 1 complete.")

    if not candidate:
        print("[INFO] No suitable unposted articles found.")
        return {"status": "success", "posted": False}

    title        = candidate["title"]
    link         = candidate["link"]
    desc         = candidate["description"]
    feed_url     = candidate["feed_url"]
    pub_date     = candidate["pub_date"]
    entry        = candidate["entry"]
    score        = candidate["score"]
    content_hash = make_content_hash(title)
    title_hash   = make_title_hash(title, feed_url)

    print(f"[INFO] [{elapsed()}s] Selected (score={score}): {title[:65]}")

    # ════════════════════════════════════════════════
    # PHASE 2 — PARALLEL SCRAPE: TEXT + IMAGES
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 2: Scraping text + images...")

    try:
        text_result, image_result = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(None, scrape_article_text, link),
                loop.run_in_executor(None, scrape_article_images, link, entry),
                return_exceptions=True,
            ),
            timeout=SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Scrape timed out.")
        text_result  = None
        image_result = []

    full_text  = text_result  if not isinstance(text_result, Exception)  else None
    image_urls = image_result if not isinstance(image_result, Exception) else []

    if isinstance(text_result, Exception):
        print(f"[WARN] Text scrape error: {text_result}")
    if isinstance(image_result, Exception):
        print(f"[WARN] Image scrape error: {image_result}")

    content = (
        full_text
        if full_text and len(full_text) > len(desc)
        else desc[:MAX_RSS_CHARS]
    )

    print(
        f"[INFO] [{elapsed()}s] "
        f"Text: {'scraped' if full_text else 'rss-summary'} "
        f"({len(content)} chars) | Images: {len(image_urls)}"
    )

    if len(content) < MIN_CONTENT_CHARS:
        print(f"[WARN] [{elapsed()}s] Content too thin. Skipping.")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date,
            SOURCE_TYPE, sdk_mode, title_hash, content_hash,
        )
        return {"status": "skipped", "reason": "thin_content", "posted": False}

    # ════════════════════════════════════════════════
    # PHASE 3 — SUMMARIZE + TRANSLATE
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 3: Summarize + Translate...")

    english_summary = await loop.run_in_executor(
        None, extractive_summarize, content, SUMMARY_SENTENCES,
    )
    print(f"[INFO] [{elapsed()}s] Summary: {len(english_summary)} chars")

    try:
        title_fa, body_fa = await asyncio.wait_for(
            loop.run_in_executor(
                None, translate_article, title, english_summary,
            ),
            timeout=TRANSLATION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Translation timed out.")
        title_fa = title
        body_fa  = english_summary

    if not title_fa or not body_fa:
        print(f"[WARN] [{elapsed()}s] Translation empty.")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date,
            SOURCE_TYPE, sdk_mode, title_hash, content_hash,
        )
        return {"status": "error", "reason": "translation_failed", "posted": False}

    print(
        f"[INFO] [{elapsed()}s] "
        f"title_fa={len(title_fa)} | body_fa={len(body_fa)}"
    )

    # ════════════════════════════════════════════════
    # PHASE 4 — POST TO TELEGRAM
    # Step 1: image album (no caption)
    # Step 2: first image + Persian caption
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 4: Posting...")

    caption = build_caption(title_fa, body_fa)
    print(
        f"[INFO] [{elapsed()}s] "
        f"Caption: {len(caption)} chars | Images: {len(image_urls)}"
    )

    try:
        posted = await asyncio.wait_for(
            send_to_telegram(bot, chat_id, caption, image_urls),
            timeout=TELEGRAM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Telegram timed out.")
        posted = False
    except Exception as e:
        print(f"[ERROR] [{elapsed()}s] Telegram: {e}")
        posted = False

    if posted:
        print(f"[SUCCESS] [{elapsed()}s] Posted: {title[:60]}")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date,
            SOURCE_TYPE, sdk_mode, title_hash, content_hash,
        )
    else:
        print(f"[ERROR] [{elapsed()}s] Telegram failed.")

    print(
        f"[INFO] ═══ v7.3 done in {elapsed()}s | posted={posted} ═══"
    )
    return {"status": "success", "posted": posted}


# ─────────────────────────────────────────────
# CROSS-SOURCE DUPLICATE DETECTION
# ─────────────────────────────────────────────

def make_content_hash(title):
    """
    SHA256 of normalized title — ignores source site.
    Normalization: lowercase, strip punctuation, sort tokens,
    remove stop words.

    "H&M Launches New Spring Collection" on wwd.com
    "H&M Launches New Spring Collection" on fashionista.com
    → identical hash → duplicate caught ✓

    "H&M Unveils Spring Collection" (reworded)
    → different hash → caught by fuzzy matching instead
    """
    tokens = normalize_title_tokens(title)
    normalized = " ".join(sorted(tokens))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def make_title_hash(title, feed_url):
    """
    Legacy hash: SHA256(title + feed_url).
    Kept for backward compatibility with v7.2 DB records.
    """
    normalized = (title.lower().strip() + feed_url[:50]).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def normalize_title_tokens(title):
    """
    Tokenize and normalize a title for similarity comparison.
    Returns frozenset of meaningful tokens.

    Steps:
      1. Lowercase
      2. Remove punctuation (keep alphanumeric + spaces)
      3. Split on whitespace
      4. Remove stop words
      5. Remove tokens shorter than 2 chars
    """
    title   = title.lower()
    title   = re.sub(r"[^a-z0-9\s]", " ", title)
    tokens  = title.split()
    tokens  = [
        t for t in tokens
        if t not in TITLE_STOP_WORDS and len(t) >= 2
    ]
    return frozenset(tokens)


def jaccard_similarity(tokens_a, tokens_b):
    """
    Jaccard similarity = |intersection| / |union|
    Range: 0.0 (no overlap) to 1.0 (identical)
    Fully offline — pure Python set operations.
    """
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = len(tokens_a & tokens_b)
    union        = len(tokens_a | tokens_b)
    return intersection / union if union > 0 else 0.0


def load_recent_titles(databases, database_id, collection_id,
                       sdk_mode, limit):
    """
    Fetch the most recent N article titles from Appwrite DB.
    Used for fuzzy duplicate checking against incoming articles.

    Returns list of (title_string, normalized_token_frozenset) tuples.
    Returns empty list on any error — fuzzy check degrades gracefully.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            queries = [
                Query.limit(limit),
                Query.order_desc("$createdAt"),
            ]
            if sdk_mode == "new":
                r = databases.list_rows(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=queries,
                )
                docs = r.get("rows", [])
            else:
                r = databases.list_documents(
                    database_id=database_id,
                
