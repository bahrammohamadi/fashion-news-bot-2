# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    9.0 — FINAL
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# ARCHITECTURE OVERVIEW:
#
#   PIPELINE ORDER (corrected from previous versions):
#   ┌─────────────────────────────────────────────────┐
#   │  Phase 1: RSS scan → score → dedup (L1-L4)     │
#   │  Phase 2: Parallel scrape (text + images)       │
#   │  Phase 3: Summarize (offline LSA)               │
#   │           + Parallel translate (title + body)   │
#   │  Phase 4: Build caption + hashtags              │
#   │  Phase 5: DB write (distributed lock)           │
#   │  Phase 6: Post to Telegram                      │
#   └─────────────────────────────────────────────────┘
#
#   POSTING SEQUENCE (guaranteed order):
#   ┌─────────────────────────────────────────────────┐
#   │  Step 1: send_media_group(all images)           │
#   │          → capture last message_id (anchor)     │
#   │  Step 2: sleep(ALBUM_CAPTION_DELAY seconds)     │
#   │  Step 3: send_message(caption,                  │
#   │            reply_to_message_id=anchor)          │
#   │  Step 4: sleep(STICKER_DELAY seconds)           │
#   │  Step 5: send_sticker(random fashion sticker)   │
#   └─────────────────────────────────────────────────┘
#
#   DUPLICATE PROTECTION (L1-L4, all pre-post):
#   L1  → exact URL match in DB
#   L2  → content_hash (SHA256 of normalized title tokens)
#   L2b → title_hash (legacy, feed-url scoped)
#   L3  → Jaccard fuzzy title similarity >= 0.65
#   L4a → one article per domain per execution
#   L4b → domain_hash in DB within last 6 hours (log only)
#
#   DB WRITE BEFORE POST (distributed lock):
#   Article saved to Appwrite AFTER translation but BEFORE
#   any Telegram call. Concurrent executions abort on find.
#
#   NO LLMs. NO paid APIs. NO OpenAI. NO OpenRouter.
#   Translation: MyMemory free API
#   Summarization: sumy LsaSummarizer (offline)
#   Email: read from MYMEMORY_EMAIL env var
# ============================================================


# ═══════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════

import os
import re
import time
import random
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

warnings.filterwarnings("ignore", category=DeprecationWarning)


# ═══════════════════════════════════════════════════════════
# SECTION 1 — CONFIGURATION
# All secrets read from environment variables only.
# No hardcoded credentials anywhere in this file.
# ═══════════════════════════════════════════════════════════

# ── Appwrite ──
COLLECTION_ID = "history"
SOURCE_TYPE   = "en"

# ── Article filtering ──
ARTICLE_AGE_HOURS = 36
MIN_CONTENT_CHARS = 150
MAX_SCRAPED_CHARS = 3000
MAX_RSS_CHARS     = 1000

# ── Telegram ──
CAPTION_MAX         = 1020
MAX_IMAGES          = 10
ALBUM_CAPTION_DELAY = 2.0
STICKER_DELAY       = 1.5

# ── Appwrite DB field size limits ──
DB_LINK_MAX        = 999
DB_TITLE_MAX       = 499
DB_FEED_URL_MAX    = 499
DB_SOURCE_TYPE_MAX = 19
DB_HASH_MAX        = 64
DB_CATEGORY_MAX    = 49
DB_DOMAIN_HASH_MAX = 64

# ── Operation timeouts (seconds) ──
FEED_FETCH_TIMEOUT  = 7
FEEDS_SCAN_TIMEOUT  = 22
SCRAPE_TIMEOUT      = 12
TRANSLATION_TIMEOUT = 45
TELEGRAM_TIMEOUT    = 50

# ── Summarizer ──
SUMMARY_SENTENCES = 8

# ── MyMemory translation ──
# Email loaded from env for 50k chars/day quota.
# Falls back to anonymous (10k/day) if not set.
MYMEMORY_CHUNK_SIZE  = 450
MYMEMORY_CHUNK_DELAY = 1.0
MYMEMORY_EMAIL       = os.environ.get("MYMEMORY_EMAIL", "")

# ── Image scraping ──
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_BLOCKLIST  = [
    "doubleclick", "googletagmanager", "googlesyndication",
    "facebook.com/tr", "analytics", "pixel", "beacon",
    "tracking", "counter", "stat.", "stats.",
]

# ── Duplicate detection ──
FUZZY_SIMILARITY_THRESHOLD = 0.65
FUZZY_LOOKBACK_COUNT       = 150
DOMAIN_DEDUP_HOURS         = 6

# ── Stop words for title normalization ──
TITLE_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "its", "it", "this", "that", "these", "those", "and", "or",
    "but", "as", "up", "out", "if", "about", "into", "over",
    "after", "new", "first", "last", "says", "said",
}

# ── Peak hours UTC (Tehran = UTC+3:30) ──
# Tehran 08-10 → UTC 04-06
# Tehran 13-15 → UTC 09-11
# Tehran 20-23 → UTC 16-19
PEAK_HOURS_UTC  = {4, 5, 6, 9, 10, 11, 16, 17, 18, 19}
PEAK_HOUR_BONUS = 15

# ── Article scoring weights ──
SCORE_RECENCY_MAX       = 40
SCORE_TITLE_KEYWORD     = 15
SCORE_DESC_KEYWORD      = 5
SCORE_HAS_IMAGE         = 10
SCORE_DESC_LENGTH       = 10
SCORE_FASHION_RELEVANCE = 20

# ── Fashion relevance keywords ──
FASHION_RELEVANCE_KEYWORDS = {
    "chanel", "dior", "gucci", "prada", "louis vuitton", "lv",
    "balenciaga", "versace", "fendi", "burberry", "valentino",
    "armani", "hermes", "celine", "givenchy", "saint laurent",
    "bottega veneta", "miu miu", "loewe", "jacquemus", "off-white",
    "alexander mcqueen", "vivienne westwood", "stella mccartney",
    "zara", "h&m", "hm", "uniqlo", "massimo dutti", "cos",
    "mango", "asos", "shein", "& other stories",
    "nike", "adidas", "puma", "reebok", "new balance", "converse",
    "vans", "supreme", "palace", "stussy", "kith", "jordan",
    "fashion week", "runway", "catwalk", "collection", "couture",
    "resort", "pre-fall", "ss26", "fw26", "ss25", "fw25",
    "pfw", "mfw", "lfw", "nyfw", "met gala", "red carpet",
    "fashion show", "lookbook", "editorial",
    "trend", "style", "outfit", "wardrobe", "streetwear", "luxury",
    "vintage", "sustainable fashion", "fast fashion", "capsule",
    "collaboration", "collab", "model", "designer",
    "creative director", "fashion",
}

# ── Trend scoring keywords ──
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

# ── Content categories ──
CONTENT_CATEGORIES = {
    "runway": [
        "runway", "fashion week", "collection", "show", "catwalk",
        "ss26", "fw26", "ss25", "fw25", "resort", "couture",
        "paris", "milan", "london", "new york", "pfw", "mfw",
    ],
    "brand": [
        "chanel", "dior", "gucci", "prada", "louis vuitton", "lv",
        "balenciaga", "versace", "fendi", "burberry", "valentino",
        "armani", "hermes", "celine", "givenchy", "saint laurent",
        "bottega", "miu miu", "loewe", "jacquemus", "off-white",
    ],
    "business": [
        "acquires", "acquisition", "merger", "revenue", "profit",
        "ipo", "stock", "sales", "growth", "market", "investment",
        "funding", "ceo", "appoints", "names", "executive",
        "partnership", "deal", "collaboration", "brand deal",
    ],
    "beauty": [
        "beauty", "makeup", "cosmetics", "skincare", "fragrance",
        "perfume", "lipstick", "foundation", "serum", "moisturizer",
        "hair", "nail", "spa", "wellness", "grooming",
    ],
    "sustainability": [
        "sustainable", "sustainability", "eco", "green", "recycled",
        "organic", "ethical", "conscious", "upcycled", "carbon",
        "environment", "circular", "biodegradable", "vegan",
    ],
    "celebrity": [
        "celebrity", "actor", "actress", "singer", "kardashian",
        "beyonce", "rihanna", "zendaya", "hailey", "kendall",
        "gigi", "bella", "met gala", "red carpet", "wore", "spotted",
    ],
    "trend": [
        "trend", "trending", "viral", "popular", "style", "look",
        "aesthetic", "core", "outfit", "wear", "season", "must-have",
        "fashion", "wardrobe", "staple", "classic",
    ],
}

# ── Hashtag map ──
HASHTAG_MAP = {
    "chanel":        "#Chanel #شنل",
    "dior":          "#Dior #دیور",
    "gucci":         "#Gucci #گوچی",
    "prada":         "#Prada #پرادا",
    "louis vuitton": "#LouisVuitton #لویی_ویتون",
    "balenciaga":    "#Balenciaga #بالنسیاگا",
    "versace":       "#Versace #ورساچه",
    "zara":          "#Zara #زارا",
    "hm":            "#HM #اچ_اند_ام",
    "nike":          "#Nike #نایکی",
    "adidas":        "#Adidas #آدیداس",
    "runway":        "#Runway #رانوی",
    "fashion week":  "#FashionWeek #هفته_مد",
    "collection":    "#Collection #کالکشن",
    "sustainability": "#Sustainability #مد_پایدار",
    "beauty":        "#Beauty #زیبایی",
    "trend":         "#Trend #ترند",
    "style":         "#Style #استایل",
    "celebrity":     "#Celebrity #سلبریتی",
    "streetwear":    "#Streetwear #استریت_ویر",
    "luxury":        "#Luxury #لاکچری",
    "vintage":       "#Vintage #وینتیج",
    "met gala":      "#MetGala #مت_گالا",
    "red carpet":    "#RedCarpet #فرش_قرمز",
    "couture":       "#Couture #کوتور",
    "collab":        "#Collab #همکاری",
}
MAX_HASHTAGS = 5

# ── Fashion stickers ──
FASHION_STICKERS = [
    "CAACAgIAAxkBAAIBmGRx1yRFMVhVqVXLv_dAAXJMOdFNAAIUAAOVgnkAAVGGBbBjxbg4LwQ",
    "CAACAgIAAxkBAAIBmWRx1yRqy9JkN2DmV_Z2sRsKdaTjAAIVAAOVgnkAAc8R3q5p5-AELAQ",
    "CAACAgIAAxkBAAIBmmRx1yS2T2gfLqJQX9oK6LZqp1HIAAIWAAO0yXAAAV0MzCRF3ZRILAQ",
    "CAACAgIAAxkBAAIBm2Rx1ySiJV4dVeTuCTc-RfFDnfQpAAIXAAO0yXAAAA3Vm7IiJdisLAQ",
    "CAACAgIAAxkBAAIBnGRx1yT_jVlWt5xPJ7BO9aQ4JvFaAAIYAAO0yXAAAA0k9GZDQpLcLAQ",
]

# ── RSS feeds ──
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

# ── Boilerplate patterns ──
BOILERPLATE_PATTERNS = [
    "subscribe", "newsletter", "sign up", "cookie",
    "privacy policy", "all rights reserved", "terms of service",
    "advertisement", "sponsored content", "follow us",
    "share this", "read more", "click here", "tap here",
    "download the app", "get the app",
]


# ═══════════════════════════════════════════════════════════
# SECTION 2 — MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

async def main(event=None, context=None):
    print("[INFO] ═══ FashionBot v9.0 FINAL started ═══")

    # ── Python 3.12 safe event loop reference ──
    loop       = asyncio.get_running_loop()
    start_time = loop.time()

    def elapsed():
        return round(loop.time() - start_time, 1)

    # ── Load ALL secrets from environment only ──
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
            "TELEGRAM_BOT_TOKEN":  token,
            "TELEGRAM_CHANNEL_ID": chat_id,
            "APPWRITE_PROJECT_ID": appwrite_project,
            "APPWRITE_API_KEY":    appwrite_key,
            "APPWRITE_DATABASE_ID": database_id,
        }.items() if not v
    ]
    if missing:
        print(f"[ERROR] Missing env vars: {missing}")
        return {"status": "error", "missing_vars": missing}

    # ── Initialize clients ──
    bot       = Bot(token=token)
    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)
    sdk_mode  = "new" if hasattr(databases, "list_rows") else "legacy"
    print(f"[INFO] SDK mode: {sdk_mode}")

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)
    current_hour   = now.hour
    is_peak        = current_hour in PEAK_HOURS_UTC
    print(
        f"[INFO] UTC={current_hour}h | "
        f"Peak={'YES +' + str(PEAK_HOUR_BONUS) if is_peak else 'no'}"
    )

    # ── Load recent titles for fuzzy dedup ──
    recent_titles = _load_recent_titles(
        databases, database_id, COLLECTION_ID, sdk_mode,
        FUZZY_LOOKBACK_COUNT,
    )
    print(f"[INFO] [{elapsed()}s] {len(recent_titles)} recent titles loaded.")

    # ════════════════════════════════
    # PHASE 1 — RSS SCAN
    # ════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 1: Scanning {len(RSS_FEEDS)} feeds...")
    try:
        candidate = await asyncio.wait_for(
            _find_best_candidate(
                feeds=RSS_FEEDS,
                databases=databases,
                database_id=database_id,
                collection_id=COLLECTION_ID,
                time_threshold=time_threshold,
                sdk_mode=sdk_mode,
                now=now,
                recent_titles=recent_titles,
                is_peak=is_peak,
            ),
            timeout=FEEDS_SCAN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Feed scan timed out.")
        candidate = None

    if not candidate:
        print(f"[INFO] [{elapsed()}s] No new article found.")
        return {"status": "success", "posted": False}

    # ── Unpack candidate ──
    title    = candidate["title"]
    link     = candidate["link"]
    desc     = candidate["description"]
    feed_url = candidate["feed_url"]
    pub_date = candidate["pub_date"]
    entry    = candidate["entry"]
    score    = candidate["score"]
    category = candidate["category"]

    content_hash = _make_content_hash(title)
    title_hash   = _make_title_hash(title, feed_url)
    domain_hash  = _make_domain_hash(_get_domain(link))

    print(
        f"[INFO] [{elapsed()}s] Selected: "
        f"score={score} cat={category} | {title[:65]}"
    )

    # ── Pre-flight strict re-check (race condition guard) ──
    is_dup, dup_reason = _strict_duplicate_check(
        databases, database_id, COLLECTION_ID,
        link, content_hash, title_hash, sdk_mode,
    )
    if is_dup:
        print(f"[WARN] [{elapsed()}s] Pre-flight dup ({dup_reason}). Abort.")
        return {"status": "success", "posted": False, "reason": dup_reason}

    # ════════════════════════════════
    # PHASE 2 — PARALLEL SCRAPE
    # ════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 2: Scraping text + images...")
    try:
        text_result, image_result = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(None, _scrape_text, link),
                loop.run_in_executor(None, _scrape_images, link, entry),
                return_exceptions=True,
            ),
            timeout=SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Scrape timed out.")
        text_result  = None
        image_result = []

    full_text  = text_result  if isinstance(text_result,  str)  else None
    image_urls = image_result if isinstance(image_result, list) else []
    content    = _select_content(full_text, desc, title)

    print(
        f"[INFO] [{elapsed()}s] "
        f"Text={'scraped' if full_text else 'fallback'} "
        f"({len(content)}ch) | Images={len(image_urls)}"
    )

    if len(content) < MIN_CONTENT_CHARS:
        print(f"[WARN] [{elapsed()}s] Thin content — aborting.")
        return {
            "status": "skipped",
            "reason": f"thin_content ({len(content)}ch)",
            "posted": False,
        }

    # ════════════════════════════════
    # PHASE 3 — SUMMARIZE + TRANSLATE (parallel)
    # ════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 3: Summarize + Translate...")

    # Step A: offline summarization (blocking — run in executor)
    english_summary = await loop.run_in_executor(
        None, _extractive_summarize, content, SUMMARY_SENTENCES
    )
    print(f"[INFO] [{elapsed()}s] Summary: {len(english_summary)}ch")

    # Step B: translate title and body IN PARALLEL
    try:
        title_fa, body_fa = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(None, _translate_mymemory, title),
                loop.run_in_executor(None, _translate_mymemory, english_summary),
            ),
            timeout=TRANSLATION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Translation timed out — using English.")
        title_fa = title
        body_fa  = english_summary

    title_fa = (title_fa or "").strip() or title
    body_fa  = (body_fa  or "").strip() or english_summary

    if not title_fa or not body_fa:
        print(f"[WARN] [{elapsed()}s] Translation empty — aborting.")
        return {
            "status": "error",
            "reason": "translation_failed",
            "posted": False,
        }

    print(
        f"[INFO] [{elapsed()}s] "
        f"title_fa={len(title_fa)}ch | body_fa={len(body_fa)}ch"
    )

    # ════════════════════════════════
    # PHASE 4 — BUILD CAPTION
    # ════════════════════════════════
    combined_for_tags = f"{title} {desc} {content[:500]}"
    hashtags = _extract_hashtags_from_text(combined_for_tags)
    caption  = _build_caption(title_fa, body_fa, hashtags, category)

    print(
        f"[INFO] [{elapsed()}s] "
        f"Caption={len(caption)}ch | Hashtags={len(hashtags)}"
    )

    # ════════════════════════════════
    # PHASE 5 — DB WRITE (distributed lock)
    # Written AFTER translation, BEFORE Telegram.
    # If two instances race, second finds record and aborts.
    # ════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 5: DB write (lock)...")
    saved = _save_to_db(
        databases=databases,
        database_id=database_id,
        collection_id=COLLECTION_ID,
        link=link,
        title=title,
        feed_url=feed_url,
        pub_date=pub_date,
        source_type=SOURCE_TYPE,
        sdk_mode=sdk_mode,
        title_hash=title_hash,
        content_hash=content_hash,
        category=category,
        trend_score=score,
        post_hour=current_hour,
        domain_hash=domain_hash,
    )
    if not saved:
        print(f"[WARN] [{elapsed()}s] DB write failed — aborting.")
        return {
            "status": "error",
            "reason": "db_save_failed",
            "posted": False,
        }
    print(f"[INFO] [{elapsed()}s] DB lock acquired.")

    # ════════════════════════════════
    # PHASE 6 — POST TO TELEGRAM
    # ════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 6: Posting to Telegram...")
    try:
        posted = await asyncio.wait_for(
            _post_to_telegram(bot, chat_id, caption, image_urls),
            timeout=TELEGRAM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Telegram timed out.")
        posted = False
    except Exception as e:
        print(f"[ERROR] [{elapsed()}s] Telegram: {e}")
        posted = False

    print(
        f"[INFO] ═══ v9.0 FINAL done in {elapsed()}s | "
        f"{'POSTED' if posted else 'FAILED'} ═══"
    )
    return {"status": "success", "posted": posted}


# ═══════════════════════════════════════════════════════════
# SECTION 2b — STANDALONE ARTICLE PROCESSOR
#
# Use this when you have a specific URL to process
# without going through RSS scanning.
# No posting logic — returns structured data only.
#
# Parallelism:
#   ┌────────────────────────────────────────────┐
#   │  asyncio.gather()                          │
#   │    ├─ _scrape_text(url)    [executor]      │
#   │    └─ _scrape_images(url)  [executor]      │
#   │  then: _extractive_summarize()  [executor] │
#   │  then: asyncio.gather()                    │
#   │    ├─ translate(title)     [executor]      │
#   │    └─ translate(body)      [executor]      │
#   └────────────────────────────────────────────┘
# ═══════════════════════════════════════════════════════════

async def process_article(
    url: str,
    title: str = "",
    description: str = "",
    category: str = "",
    sentence_count: int = SUMMARY_SENTENCES,
) -> dict:
    """
    Standalone article processor.

    Args:
        url:            Article URL to scrape
        title:          Optional known title (from RSS or caller)
        description:    Optional known description
        category:       Optional pre-detected category
        sentence_count: Max sentences in summary

    Returns:
        {
            "status":     "success" | "error" | "skipped",
            "reason":     str (present on non-success),
            "title_fa":   str,
            "summary_fa": str,
            "hashtags":   list[str],
            "caption":    str,
            "image_urls": list[str],
        }
    """
    loop = asyncio.get_running_loop()

    _EMPTY = {
        "title_fa":   "",
        "summary_fa": "",
        "hashtags":   [],
        "caption":    "",
        "image_urls": [],
    }

    # ── Validate URL ──
    if not url or not url.startswith("http"):
        return {"status": "error", "reason": "invalid_url", **_EMPTY}

    print(f"[process_article] START url={url[:80]}")

    # ── Minimal RSS entry stub for image scraper ──
    class _StubEntry:
        def get(self, key, default=None):
            return default
        content = []

    stub_entry = _StubEntry()

    # ── Step 1: Parallel scrape ──
    try:
        text_result, image_result = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(None, _scrape_text, url),
                loop.run_in_executor(
                    None, _scrape_images, url, stub_entry
                ),
                return_exceptions=True,
            ),
            timeout=SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print("[process_article] Scrape timed out.")
        text_result  = None
        image_result = []

    scraped_text = text_result  if isinstance(text_result,  str)  else None
    image_urls   = image_result if isinstance(image_result, list) else []
    content      = _select_content(scraped_text, description, title)

    print(
        f"[process_article] "
        f"text={'scraped' if scraped_text else 'fallback'} "
        f"({len(content)}ch) | images={len(image_urls)}"
    )

    # ── Step 1b: Minimum length check ──
    if len(content) < MIN_CONTENT_CHARS:
        return {
            "status": "skipped",
            "reason": f"thin_content ({len(content)}ch < {MIN_CONTENT_CHARS})",
            **_EMPTY,
            "image_urls": image_urls,
        }

    # ── Step 2: Offline summarization ──
    english_summary = await loop.run_in_executor(
        None, _extractive_summarize, content, sentence_count,
    )
    print(f"[process_article] Summary {len(english_summary)}ch")

    # ── Step 3: Parallel translation ──
    effective_title = title or _extract_first_sentence(content)

    try:
        title_fa, body_fa = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(
                    None, _translate_mymemory, effective_title
                ),
                loop.run_in_executor(
                    None, _translate_mymemory, english_summary
                ),
            ),
            timeout=TRANSLATION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print("[process_article] Translation timed out — using English.")
        title_fa = effective_title
        body_fa  = english_summary

    title_fa = (title_fa or "").strip() or effective_title
    body_fa  = (body_fa  or "").strip() or english_summary

    print(
        f"[process_article] "
        f"title_fa={len(title_fa)}ch | body_fa={len(body_fa)}ch"
    )

    # ── Step 4: Hashtags + category ──
    combined_for_tags = f"{title} {description} {content[:500]}"
    hashtags          = _extract_hashtags_from_text(combined_for_tags)
    detected_category = (
        category or _detect_category(title, description or content[:300])
    )

    # ── Step 5: Build caption ──
    caption = _build_caption(title_fa, body_fa, hashtags, detected_category)

    print(
        f"[process_article] "
        f"caption={len(caption)}ch | "
        f"hashtags={len(hashtags)} | "
        f"category={detected_category}"
    )

    return {
        "status":     "success",
        "title_fa":   title_fa,
        "summary_fa": body_fa,
        "hashtags":   hashtags,
        "caption":    caption,
        "image_urls": image_urls,
    }


# ═══════════════════════════════════════════════════════════
# SECTION 3 — FEED SCANNING & CANDIDATE SELECTION
# ═══════════════════════════════════════════════════════════

async def _find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, now, recent_titles, is_peak,
):
    """
    Fetch all feeds in parallel, score all articles,
    apply duplicate checks L1-L4 in order.
    L4a only fires AFTER L1-L3 pass — avoids blocking
    domains that have old posted articles.
    """
    loop  = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, _fetch_feed, url, time_threshold)
        for url in feeds
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_candidates = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"[WARN] Feed ({feeds[i][:45]}): {result}")
            continue
        if result:
            all_candidates.extend(result)

    print(f"[INFO] {len(all_candidates)} articles collected across all feeds.")
    if not all_candidates:
        return None

    # Score and categorize
    for c in all_candidates:
        c["score"]    = _score_article(c, now, is_peak)
        c["category"] = _detect_category(c["title"], c["description"])

    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    print("[INFO] Top 5 candidates by score:")
    for c in all_candidates[:5]:
        print(
            f"       [{c['score']:>3}] [{c['category']:<14}] "
            f"{c['title'][:58]}"
        )

    # Load recent domain hashes — informational only
    recent_domain_hashes = _load_recent_domain_hashes(
        databases, database_id, collection_id, sdk_mode
    )
    print(f"[INFO] Recent domain hashes loaded: {len(recent_domain_hashes)}")

    seen_domains_this_run = set()

    for c in all_candidates:
        link         = c["link"]
        title        = c["title"]
        feed_url     = c["feed_url"]
        domain       = _get_domain(link)
        content_hash = _make_content_hash(title)
        title_hash   = _make_title_hash(title, feed_url)
        domain_hash  = _make_domain_hash(domain)

        # L1: Exact link match
        r = _query_field(
            databases, database_id, collection_id,
            "link", link[:DB_LINK_MAX], sdk_mode,
        )
        if r is not False:
            print(f"[SKIP] L1({'err' if r is None else 'dup'}): {title[:58]}")
            continue

        # L2: Content hash
        r = _query_field(
            databases, database_id, collection_id,
            "content_hash", content_hash, sdk_mode,
        )
        if r is not False:
            print(f"[SKIP] L2({'err' if r is None else 'dup'}): {title[:58]}")
            continue

        # L2b: Legacy title hash
        r = _query_field(
            databases, database_id, collection_id,
            "title_hash", title_hash, sdk_mode,
        )
        if r is not False:
            print(f"[SKIP] L2b({'err' if r is None else 'dup'}): {title[:58]}")
            continue

        # L3: Fuzzy title similarity
        is_fuzz, matched, fuzz_score = _fuzzy_duplicate(title, recent_titles)
        if is_fuzz:
            print(
                f"[SKIP] L3 fuzzy={fuzz_score:.2f}: "
                f"{title[:45]} ≈ {(matched or '')[:35]}"
            )
            continue

        # L4b: Cross-run domain — log only, does not block
        if domain_hash in recent_domain_hashes:
            print(
                f"[INFO] L4b: domain {domain} seen in last "
                f"{DOMAIN_DEDUP_HOURS}h — not blocking."
            )

        # L4a: One domain per run (only reached after L1-L3 pass)
        if domain in seen_domains_this_run:
            print(f"[SKIP] L4a domain/run ({domain}): {title[:58]}")
            continue

        seen_domains_this_run.add(domain)
        print(f"[INFO] PASS fuzz={fuzz_score:.2f}: {title[:58]}")
        return c

    print("[INFO] All candidates exhausted after duplicate checks.")
    return None


def _fetch_feed(feed_url: str, time_threshold: datetime) -> list:
    """Parse one RSS feed synchronously. Returns list of candidate dicts."""
    import socket
    try:
        old = socket.getdefaulttimeout()
        socket.setdefaulttimeout(FEED_FETCH_TIMEOUT)
        feed = feedparser.parse(feed_url)
        socket.setdefaulttimeout(old)
    except Exception as e:
        print(f"[WARN] feedparser ({feed_url[:45]}): {e}")
        return []

    candidates = []
    for entry in feed.entries:
        published = (
            entry.get("published_parsed") or entry.get("updated_parsed")
        )
        if not published:
            continue

        pub_date = datetime(*published[:6], tzinfo=timezone.utc)
        if pub_date < time_threshold:
            continue

        title = (entry.get("title") or "").strip()
        link  = (entry.get("link")  or "").strip()
        if not title or not link:
            continue

        raw  = entry.get("summary") or entry.get("description") or ""
        desc = re.sub(r"<[^>]+>", " ", raw)
        desc = re.sub(r"\s+",     " ", desc).strip()

        candidates.append({
            "title":       title,
            "link":        link,
            "description": desc,
            "feed_url":    feed_url,
            "pub_date":    pub_date,
            "entry":       entry,
            "score":       0,
            "category":    "general",
        })
    return candidates


def _score_article(candidate: dict, now: datetime, is_peak: bool = False) -> int:
    """
    Score 0-100. Components:
      Recency         0-40
      Trend keywords  0-45   (capped at 3 keyword matches)
      Image bonus     0-10
      Desc richness   0-10
      Peak hour       0-15
      Fashion signal  -30 / +10 / +20
    """
    score     = 0
    age_hours = (now - candidate["pub_date"]).total_seconds() / 3600
    combined  = (candidate["title"] + " " + candidate["description"]).lower()

    # Recency (linear decay after 3h)
    if age_hours <= 3:
        score += SCORE_RECENCY_MAX
    elif age_hours <= ARTICLE_AGE_HOURS:
        ratio  = 1 - (age_hours - 3) / (ARTICLE_AGE_HOURS - 3)
        score += int(SCORE_RECENCY_MAX * ratio)

    # Trend keywords (max 3 matches)
    title_lower = candidate["title"].lower()
    desc_lower  = candidate["description"].lower()
    matched     = 0
    for kw in TREND_KEYWORDS:
        if matched >= 3:
            break
        if kw in title_lower:
            score   += SCORE_TITLE_KEYWORD
            matched += 1
        elif kw in desc_lower:
            score   += SCORE_DESC_KEYWORD
            matched += 1

    # Image bonus
    if _extract_rss_image(candidate["entry"]):
        score += SCORE_HAS_IMAGE

    # Description richness
    if len(candidate["description"]) > 200:
        score += SCORE_DESC_LENGTH

    # Peak hour
    if is_peak:
        score += PEAK_HOUR_BONUS

    # Fashion relevance
    fashion_hits = sum(
        1 for kw in FASHION_RELEVANCE_KEYWORDS if kw in combined
    )
    if fashion_hits >= 2:
        score += SCORE_FASHION_RELEVANCE
    elif fashion_hits == 1:
        score += SCORE_FASHION_RELEVANCE // 2
    else:
        score = max(0, score - 30)

    return min(score, 100)


def _detect_category(title: str, description: str) -> str:
    combined = (title + " " + description).lower()
    for cat, keywords in CONTENT_CATEGORIES.items():
        for kw in keywords:
            if kw in combined:
                return cat
    return "general"


def _extract_hashtags_from_text(text: str) -> list:
    """Extract up to MAX_HASHTAGS bilingual hashtags from combined text."""
    lower    = text.lower()
    hashtags = []
    seen     = set()
    for keyword, tags in HASHTAG_MAP.items():
        if keyword in lower and keyword not in seen:
            hashtags.append(tags)
            seen.add(keyword)
            if len(hashtags) >= MAX_HASHTAGS:
                break
    return hashtags


# ── Legacy wrapper (keeps existing call-sites working) ──
def _extract_hashtags(title: str, description: str) -> list:
    return _extract_hashtags_from_text(f"{title} {description}")


# ═══════════════════════════════════════════════════════════
# SECTION 4 — DUPLICATE DETECTION
# ═══════════════════════════════════════════════════════════

def _make_content_hash(title: str) -> str:
    """SHA256 of sorted normalized title tokens. Cross-source safe."""
    tokens     = _normalize_tokens(title)
    normalized = " ".join(sorted(tokens))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _make_title_hash(title: str, feed_url: str) -> str:
    """Legacy hash scoped to feed URL for backward compatibility."""
    raw = (title.lower().strip() + feed_url[:50]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _make_domain_hash(domain: str) -> str:
    return hashlib.sha256(
        domain.encode("utf-8")
    ).hexdigest()[:DB_DOMAIN_HASH_MAX]


def _normalize_tokens(title: str) -> frozenset:
    title  = title.lower()
    title  = re.sub(r"[^a-z0-9\s]", " ", title)
    tokens = title.split()
    return frozenset(
        t for t in tokens
        if t not in TITLE_STOP_WORDS and len(t) >= 2
    )


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _fuzzy_duplicate(
    title: str, recent_titles: list
) -> tuple[bool, str | None, float]:
    if not recent_titles:
        return False, None, 0.0
    incoming   = _normalize_tokens(title)
    best_score = 0.0
    best_match = None
    for stored_title, stored_tokens in recent_titles:
        s = _jaccard(incoming, stored_tokens)
        if s > best_score:
            best_score = s
            best_match = stored_title
    if best_score >= FUZZY_SIMILARITY_THRESHOLD:
        return True, best_match, best_score
    return False, None, best_score


def _strict_duplicate_check(
    databases, database_id, collection_id,
    link, content_hash, title_hash, sdk_mode,
) -> tuple[bool, str]:
    """
    Final check before DB write.
    DB error → treat as duplicate → abort.
    Returns (is_duplicate, reason).
    """
    for field, value in [
        ("link",         link[:DB_LINK_MAX]),
        ("content_hash", content_hash),
        ("title_hash",   title_hash),
    ]:
        r = _query_field(
            databases, database_id, collection_id,
            field, value, sdk_mode,
        )
        if r is True:
            return True, f"found_{field}"
        if r is None:
            return True, f"db_error_{field}"
    return False, ""


def _query_field(
    databases, database_id, collection_id,
    field, value, sdk_mode,
):
    """
    Returns:
      True  → record found (duplicate)
      False → not found (safe to proceed)
      None  → DB error (caller treats as duplicate)
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            queries = [Query.equal(field, value), Query.limit(1)]
            if sdk_mode == "new":
                r = databases.list_rows(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=queries,
                )
            else:
                r = databases.list_documents(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=queries,
                )
            return r["total"] > 0
        except AppwriteException as e:
            print(f"[ERROR] _query_field ({field}): {e.message}")
            return None
        except Exception as e:
            print(f"[ERROR] _query_field ({field}): {e}")
            return None


def _load_recent_titles(
    databases, database_id, collection_id, sdk_mode, limit
) -> list:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            queries = [Query.limit(limit), Query.order_desc("$createdAt")]
            if sdk_mode == "new":
                r    = databases.list_rows(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=queries,
                )
                docs = r.get("rows", [])
            else:
                r    = databases.list_documents(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=queries,
                )
                docs = r.get("documents", [])
            return [
                (d.get("title", ""), _normalize_tokens(d.get("title", "")))
                for d in docs if d.get("title")
            ]
        except Exception as e:
            print(f"[WARN] _load_recent_titles: {e}")
            return []


def _load_recent_domain_hashes(
    databases, database_id, collection_id, sdk_mode
) -> set:
    cutoff     = datetime.now(timezone.utc) - timedelta(hours=DOMAIN_DEDUP_HOURS)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            queries = [
                Query.greater_than("$createdAt", cutoff_str),
                Query.limit(200),
            ]
            if sdk_mode == "new":
                r    = databases.list_rows(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=queries,
                )
                docs = r.get("rows", [])
            else:
                r    = databases.list_documents(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=queries,
                )
                docs = r.get("documents", [])
            return {d["domain_hash"] for d in docs if d.get("domain_hash")}
        except Exception as e:
            print(f"[WARN] _load_recent_domain_hashes: {e}")
            return set()


def _get_domain(url: str) -> str:
    try:
        parts = urlparse(url).netloc.replace("www.", "").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else url[:30]
    except Exception:
        return url[:30]


# ═══════════════════════════════════════════════════════════
# SECTION 5 — SCRAPING
# ═══════════════════════════════════════════════════════════

def _select_content(
    scraped_text: str | None,
    description: str,
    title: str,
) -> str:
    """
    Content priority: scraped > description > title.
    Never returns empty string.
    """
    if scraped_text and len(scraped_text) >= MIN_CONTENT_CHARS:
        return scraped_text[:MAX_SCRAPED_CHARS]
    if description and len(description) >= MIN_CONTENT_CHARS:
        return description[:MAX_RSS_CHARS]
    return title


def _extract_first_sentence(text: str) -> str:
    """Pull first sentence as fallback title."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return parts[0][:DB_TITLE_MAX] if parts else text[:DB_TITLE_MAX]


def _scrape_text(url: str) -> str | None:
    """
    Scrape article body. Extracts p/h2/h3/h4/li in doc order.
    Marks: ▌ Section   • List item
    Strips boilerplate. Deduplicates similar lines.
    """
    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=SCRAPE_TIMEOUT - 3,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        for tag in soup([
            "script", "style", "nav", "footer", "header", "aside",
            "form", "iframe", "noscript", "figcaption",
            "button", "input", "select", "svg",
        ]):
            tag.decompose()

        body = (
            soup.find("article")
            or soup.find("div", {"class": re.compile(r"article[-_]?body",  re.I)})
            or soup.find("div", {"class": re.compile(r"post[-_]?content",   re.I)})
            or soup.find("div", {"class": re.compile(r"entry[-_]?content",  re.I)})
            or soup.find("div", {"class": re.compile(r"story[-_]?body",     re.I)})
            or soup.find("main")
        )
        area = body or soup

        TARGET_TAGS = {"p", "h2", "h3", "h4", "li"}
        lines       = []
        seen_keys   = set()

        for el in area.find_all(TARGET_TAGS):
            raw = re.sub(r"\s+", " ", el.get_text(" ").strip())
            if len(raw) < 25:
                continue
            key = raw.lower()[:80]
            if key in seen_keys:
                continue
            seen_keys.add(key)

            tag   = el.name
            lower = raw.lower()

            if tag in ("h2", "h3", "h4"):
                lines.append(f"▌ {raw}")
            elif tag == "li":
                if len(raw) < 30:
                    continue
                if any(p in lower for p in BOILERPLATE_PATTERNS):
                    continue
                lines.append(f"• {raw}")
            else:
                if any(p in lower for p in BOILERPLATE_PATTERNS):
                    continue
                lines.append(raw)

        text = "\n".join(lines).strip()
        return text[:MAX_SCRAPED_CHARS] if len(text) >= 100 else None

    except requests.exceptions.Timeout:
        print(f"[WARN] Text scrape timeout: {url[:60]}")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"[WARN] Text scrape HTTP {e.response.status_code}: {url[:60]}")
        return None
    except Exception as e:
        print(f"[WARN] Text scrape: {e}")
        return None


def _scrape_images(url: str, rss_entry) -> list:
    """
    Collect article images up to MAX_IMAGES.
    Falls back to RSS entry image if page yields nothing.
    """
    images = []
    seen   = set()

    def _add(img_url: str):
        if not img_url:
            return
        img_url = img_url.strip()
        if not img_url.startswith("http") or img_url in seen:
            return
        lower = img_url.lower()
        if any(b in lower for b in IMAGE_BLOCKLIST):
            return
        base     = lower.split("?")[0]
        has_ext  = any(base.endswith(e) for e in IMAGE_EXTENSIONS)
        has_word = any(
            w in lower
            for w in ["image", "photo", "img", "picture", "media", "cdn"]
        )
        if not has_ext and not has_word:
            return
        seen.add(img_url)
        images.append(img_url)

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )},
            timeout=8,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        for tag in soup([
            "script", "style", "nav", "footer", "header",
            "aside", "form", "iframe", "noscript", "button",
        ]):
            tag.decompose()

        body = (
            soup.find("article")
            or soup.find("div", {"class": re.compile(r"article[-_]?body", re.I)})
            or soup.find("div", {"class": re.compile(r"post[-_]?content",  re.I)})
            or soup.find("div", {"class": re.compile(r"entry[-_]?content", re.I)})
            or soup.find("main")
        )
        area = body or soup

        for img in area.find_all("img"):
            src = (
                img.get("data-src")
                or img.get("data-original")
                or img.get("data-lazy-src")
                or img.get("src")
                or ""
            )
            _add(src)
            if len(images) >= MAX_IMAGES:
                break

        if len(images) < MAX_IMAGES:
            for source in area.find_all("source"):
                srcset = source.get("srcset", "")
                if srcset:
                    _add(srcset.split(",")[0].strip().split(" ")[0])
                if len(images) >= MAX_IMAGES:
                    break

    except Exception as e:
        print(f"[WARN] Image scrape: {e}")

    # RSS fallback
    if len(images) < MAX_IMAGES:
        rss_img = _extract_rss_image(rss_entry)
        if rss_img:
            _add(rss_img)

    print(f"[INFO] Images collected: {len(images)}")
    return images[:MAX_IMAGES]


def _extract_rss_image(entry) -> str | None:
    """Extract best image URL from RSS entry metadata."""
    if entry is None:
        return None
    try:
        for m in entry.get("media_content", []):
            if m.get("url") and m.get("medium") == "image":
                return m["url"]
        for m in entry.get("media_content", []):
            url = m.get("url", "")
            if url and any(url.lower().endswith(e) for e in IMAGE_EXTENSIONS):
                return url
        enc = entry.get("enclosure")
        if enc:
            url = enc.get("href") or enc.get("url", "")
            if url and enc.get("type", "").startswith("image/"):
                return url
        thumbs = entry.get("media_thumbnail", [])
        if thumbs and thumbs[0].get("url"):
            return thumbs[0]["url"]
        for field in ["summary", "description"]:
            html = entry.get(field, "")
            if html:
                img = BeautifulSoup(html, "html.parser").find("img")
                if img:
                    src = img.get("src", "")
                    if src.startswith("http"):
                        return src
        if hasattr(entry, "content") and entry.content:
            html = entry.content[0].get("value", "")
            if html:
                img = BeautifulSoup(html, "html.parser").find("img")
                if img:
                    src = img.get("src", "")
                    if src.startswith("http"):
                        return src
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════
# SECTION 6 — SUMMARIZATION & TRANSLATION
# ═══════════════════════════════════════════════════════════

def _extractive_summarize(text: str, sentence_count: int = 8) -> str:
    """
    Offline LSA summarization via sumy.
    Structured markers (▌, •) are valid sentences
    and may be selected, preserving article structure.
    """
    try:
        parser     = PlaintextParser.from_string(text, Tokenizer("english"))
        stemmer    = Stemmer("english")
        summarizer = LsaSummarizer(stemmer)
        summarizer.stop_words = get_stop_words("english")
        sentences  = summarizer(parser.document, sentence_count)
        result     = " ".join(str(s) for s in sentences).strip()
        return result if result else text[:1200]
    except Exception as e:
        print(f"[WARN] sumy: {e}")
        return text[:1200]


def _translate_mymemory(text: str, source: str = "en", target: str = "fa") -> str:
    """
    Translate via MyMemory free API.
    Chunked at MYMEMORY_CHUNK_SIZE chars to respect API limits.
    Uses MYMEMORY_EMAIL env var for 50k chars/day quota.
    Falls back to returning original text on any failure.
    """
    if not text or not text.strip():
        return ""

    chunks     = _split_chunks(text, MYMEMORY_CHUNK_SIZE)
    translated = []

    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        try:
            params = {"q": chunk, "langpair": f"{source}|{target}"}
            if MYMEMORY_EMAIL:
                params["de"] = MYMEMORY_EMAIL

            resp = requests.get(
                "https://api.mymemory.translated.net/get",
                params=params,
                timeout=12,
            )
            resp.raise_for_status()
            data  = resp.json()
            trans = (
                data.get("responseData", {})
                    .get("translatedText", "") or ""
            ).strip()

            if data.get("quotaFinished"):
                print("[WARN] MyMemory daily quota reached.")
                translated.append(chunk)
                continue

            if (
                trans
                and "MYMEMORY WARNING"       not in trans
                and "YOU USED ALL AVAILABLE" not in trans
                and len(trans) > 2
            ):
                translated.append(trans)
            else:
                translated.append(chunk)

            if i < len(chunks) - 1:
                time.sleep(MYMEMORY_CHUNK_DELAY)

        except requests.exceptions.Timeout:
            translated.append(chunk)
        except Exception as e:
            print(f"[WARN] MyMemory chunk {i + 1}/{len(chunks)}: {e}")
            translated.append(chunk)

    return " ".join(translated).strip() if translated else ""


def _split_chunks(text: str, max_chars: int) -> list:
    """Split text on sentence boundaries, respecting max_chars per chunk."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks, current = [], ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            # Long sentence: split on comma boundaries
            for part in sentence.split(", "):
                if len(current) + len(part) + 2 <= max_chars:
                    current += ("" if not current else ", ") + part
                else:
                    if current:
                        chunks.append(current.strip())
                    current = part
        elif len(current) + len(sentence) + 1 <= max_chars:
            current += ("" if not current else " ") + sentence
        else:
            if current:
                chunks.append(current.strip())
            current = sentence

    if current:
        chunks.append(current.strip())

    return [c for c in chunks if c.strip()]


def _translate_article(title: str, body: str) -> tuple[str, str]:
    """
    Sequential fallback translation (used only outside parallel gather).
    Prefer the parallel path in main() and process_article().
    """
    print(f"[INFO] Translating title ({len(title)}ch)...")
    title_fa = _translate_mymemory(title)
    time.sleep(1)
    print(f"[INFO] Translating body ({len(body)}ch)...")
    body_fa = _translate_mymemory(body)
    return title_fa or title, body_fa or body


# ═══════════════════════════════════════════════════════════
# SECTION 7 — CAPTION BUILDER
#
# Magazine editorial format:
#
#   <b>عنوان فارسی</b>
#   ─────────────
#   @irfashionnews
#
#   متن خبر...
#   ▌ عنوان بخش
#   • نکته مهم
#
#   EMOJI  کانال مد و فشن ایرانی
#
#   #hashtag1 #hashtag2 ...    ← ALWAYS LAST LINE
#
# Budget-based body trim guarantees output ≤ CAPTION_MAX.
# ═══════════════════════════════════════════════════════════

def _build_caption(
    title_fa: str,
    body_fa: str,
    hashtags: list,
    category: str,
) -> str:
    """
    Build Telegram-ready HTML caption.
    Guarantees len(output) <= CAPTION_MAX.
    Hashtags are always the last element.
    """

    def _esc(t: str) -> str:
        return (
            t.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    CATEGORY_EMOJI = {
        "runway":         "👗",
        "brand":          "🏷️",
        "business":       "📊",
        "beauty":         "💄",
        "sustainability": "♻️",
        "celebrity":      "⭐",
        "trend":          "🔥",
        "general":        "🌐",
    }
    emoji     = CATEGORY_EMOJI.get(category, "🌐")
    hash_line = " ".join(hashtags)

    # These parts are never trimmed
    header = f"<b>{_esc(title_fa.strip())}</b>"
    sep    = "─────────────\n@irfashionnews"
    footer = f"{emoji}  <i>کانال مد و فشن ایرانی</i>"

    # Calculate body budget
    # Structure: header + sep + [body] + footer + [hashes]
    # Each joined by \n\n (2 chars), so 4 separators = 8 chars overhead
    fixed_parts = [header, sep, footer]
    if hash_line:
        fixed_parts.append(hash_line)
    fixed_len   = len("\n\n".join(fixed_parts))
    body_budget = CAPTION_MAX - fixed_len - 4  # 4 = two \n\n for body slot

    safe_body = _esc(body_fa.strip())

    if body_budget <= 10:
        # Extreme edge case: headers alone too long — truncate title
        safe_body    = ""
        short_title  = _esc(title_fa.strip())[:CAPTION_MAX - 80]
        header       = f"<b>{short_title}</b>"
    elif len(safe_body) > body_budget:
        safe_body = safe_body[:body_budget - 1] + "…"

    # Assemble final caption
    parts = [header, sep]
    if safe_body:
        parts.append(safe_body)
    parts.append(footer)
    if hash_line:
        parts.append(hash_line)  # always last

    caption = "\n\n".join(parts)

    # Hard guard — should never trigger after budget calculation
    if len(caption) > CAPTION_MAX:
        caption = caption[:CAPTION_MAX - 1] + "…"

    return caption


# ═══════════════════════════════════════════════════════════
# SECTION 8 — TELEGRAM POSTING
#
# ORDER GUARANTEE via reply_to_message_id:
#
#   ① send_media_group → anchor = last message_id
#   ② sleep(ALBUM_CAPTION_DELAY)    ← belt-and-suspenders
#   ③ send_message(reply_to=anchor) ← protocol-level order
#   ④ sleep(STICKER_DELAY)
#   ⑤ send_sticker (non-fatal)
#
# FALLBACK CHAIN:
#   Album fails  → try single photo
#   Photo fails  → caption standalone (no anchor)
#   Caption fails → return False
#   Sticker fail → ignored, posted=True unchanged
# ═══════════════════════════════════════════════════════════

async def _post_to_telegram(
    bot: Bot,
    chat_id: str,
    caption: str,
    image_urls: list,
) -> bool:
    anchor_msg_id = None
    posted        = False

    # ── Step 1: Send images ──────────────────────────────
    if len(image_urls) >= 2:
        try:
            media_group = [
                InputMediaPhoto(media=url)
                for url in image_urls[:MAX_IMAGES]
            ]
            sent_msgs = await bot.send_media_group(
                chat_id=chat_id,
                media=media_group,
                disable_notification=True,
            )
            anchor_msg_id = sent_msgs[-1].message_id
            print(
                f"[INFO] ① Album: {len(sent_msgs)} images. "
                f"anchor={anchor_msg_id}"
            )
        except Exception as e:
            print(f"[WARN] ① Album failed: {str(e)[:120]}")
            # Fallback: single image
            if image_urls:
                try:
                    sent = await bot.send_photo(
                        chat_id=chat_id,
                        photo=image_urls[0],
                        disable_notification=True,
                    )
                    anchor_msg_id = sent.message_id
                    print(
                        f"[INFO] ① Album→single fallback. "
                        f"anchor={anchor_msg_id}"
                    )
                except Exception as e2:
                    print(
                        f"[WARN] ① Single photo fallback failed: "
                        f"{str(e2)[:80]}"
                    )

    elif len(image_urls) == 1:
        try:
            sent = await bot.send_photo(
                chat_id=chat_id,
                photo=image_urls[0],
                disable_notification=True,
            )
            anchor_msg_id = sent.message_id
            print(f"[INFO] ① Single photo. anchor={anchor_msg_id}")
        except Exception as e:
            print(f"[WARN] ① Single photo failed: {str(e)[:120]}")
    else:
        print("[INFO] ① No images — caption will be standalone.")

    # ── Step 2: Hard delay (belt-and-suspenders) ─────────
    if anchor_msg_id is not None:
        print(f"[INFO] ② Waiting {ALBUM_CAPTION_DELAY}s...")
        await asyncio.sleep(ALBUM_CAPTION_DELAY)

    # ── Step 3: Send caption ─────────────────────────────
    try:
        kwargs: dict = {
            "chat_id":              chat_id,
            "text":                 caption,
            "parse_mode":           "HTML",
            "link_preview_options": LinkPreviewOptions(is_disabled=True),
            "disable_notification": True,
        }
        if anchor_msg_id is not None:
            kwargs["reply_to_message_id"] = anchor_msg_id

        await bot.send_message(**kwargs)
        anchor_info = (
            f"reply_to={anchor_msg_id}" if anchor_msg_id else "standalone"
        )
        print(f"[INFO] ③ Caption sent ({anchor_info}).")
        posted = True

    except Exception as e:
        print(f"[ERROR] ③ Caption failed: {str(e)[:120]}")
        return False

    # ── Step 4: Sticker (engagement, non-fatal) ──────────
    if posted and FASHION_STICKERS:
        await asyncio.sleep(STICKER_DELAY)
        try:
            await bot.send_sticker(
                chat_id=chat_id,
                sticker=random.choice(FASHION_STICKERS),
                disable_notification=True,
            )
            print("[INFO] ④ Sticker sent.")
        except Exception as e:
            print(f"[WARN] ④ Sticker failed (non-fatal): {str(e)[:80]}")

    return posted


# ═══════════════════════════════════════════════════════════
# SECTION 9 — DATABASE WRITE
# ═══════════════════════════════════════════════════════════

def _build_db_payload(
    link, title, feed_url, pub_date, source_type,
    title_hash, content_hash, category,
    trend_score, post_hour, domain_hash,
) -> dict:
    """
    Build validated payload for Appwrite history collection.
    All string fields truncated to their DB column limits.
    All 11 fields:
      link, title, published_at, feed_url, source_type
      title_hash, content_hash, domain_hash
      category, trend_score, post_hour
    """
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    return {
        "link":         link[:DB_LINK_MAX],
        "title":        title[:DB_TITLE_MAX],
        "published_at": pub_date.strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
        "feed_url":     feed_url[:DB_FEED_URL_MAX],
        "source_type":  source_type[:DB_SOURCE_TYPE_MAX],
        "title_hash":   title_hash[:DB_HASH_MAX],
        "content_hash": content_hash[:DB_HASH_MAX],
        "category":     category[:DB_CATEGORY_MAX],
        "trend_score":  int(trend_score),
        "post_hour":    int(post_hour),
        "domain_hash":  domain_hash[:DB_DOMAIN_HASH_MAX],
    }


def _save_to_db(
    databases, database_id, collection_id,
    link, title, feed_url, pub_date, source_type,
    sdk_mode, title_hash, content_hash,
    category, trend_score, post_hour, domain_hash,
) -> bool:
    """
    Write article record to Appwrite BEFORE Telegram post.
    Acts as distributed lock: concurrent runs find the record
    on pre-flight check and abort cleanly.
    Returns True on success, False on any failure.
    """
    payload = _build_db_payload(
        link, title, feed_url, pub_date, source_type,
        title_hash, content_hash, category,
        trend_score, post_hour, domain_hash,
    )
    print(f"[INFO] DB write: {payload['link'][:70]}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            if sdk_mode == "new":
                databases.create_row(
                    database_id=database_id,
                    collection_id=collection_id,
                    row_id="unique()",
                    data=payload,
                )
            else:
                databases.create_document(
                    database_id=database_id,
                    collection_id=collection_id,
                    document_id="unique()",
                    data=payload,
                )
            print("[SUCCESS] DB write complete.")
            return True
        except AppwriteException as e:
            print(f"[ERROR] DB write AppwriteException: {e.message}")
            return False
        except Exception as e:
            print(f"[ERROR] DB write: {e}")
            return False


# ═══════════════════════════════════════════════════════════
# LOCAL TEST ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        # Direct URL mode: python main.py <url>
        test_url = sys.argv[1]
        print(f"[LOCAL] Processing URL: {test_url}")

        async def _test_process():
            result = await process_article(
                url=test_url,
                title="",
                description="",
                category="",
            )
            print("\n[RESULT]")
            for k, v in result.items():
                if k == "caption":
                    print(f"  {k} ({len(v)}ch):\n{v}")
                elif k == "image_urls":
                    print(f"  {k} ({len(v)}): {v[:3]}")
                else:
                    print(f"  {k}: {v[:120] if isinstance(v, str) else v}")

        asyncio.run(_test_process())
    else:
        # Full RSS pipeline mode
        asyncio.run(main())
