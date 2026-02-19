# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    8.1 — NLTK + ALBUM TIMEOUT FIXED
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# Fixes vs 8.0:
#   [1] NLTK punkt downloaded at startup
#       sumy now works correctly — real extractive summary
#       punkt saved to /tmp/nltk_data (writable in Appwrite)
#   [2] Album capped at MAX_ALBUM_IMAGES = 4
#       Prevents media_group timeout with 10 images
#       Caption post always uses best single image
#   [3] Separate timeout for album vs caption post
#       ALBUM_TIMEOUT  = 15s (non-fatal if exceeded)
#       CAPTION_TIMEOUT = 12s (fatal if exceeded)
#   [4] Domain dedup limit raised
#       Allow up to MAX_PER_DOMAIN articles per domain
#       Prevents over-filtering when one domain has many
#       new articles (e.g. whowhatwear.com had 6 blocked)
# ============================================================

import os
import re
import time
import hashlib
import asyncio
import warnings
import feedparser
import requests
import nltk
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
# NLTK STARTUP — Download punkt if missing
# Must run before any sumy call
# /tmp is writable in Appwrite Cloud Functions
# ─────────────────────────────────────────────

def ensure_nltk_data():
    """
    Download NLTK punkt tokenizer to /tmp/nltk_data.
    Called once at function startup.
    /tmp is the only writable directory in Appwrite serverless.
    Skips download if already present (cached between warm runs).
    """
    nltk_dir = "/tmp/nltk_data"
    nltk.data.path.insert(0, nltk_dir)

    # Test if punkt is already available
    try:
        nltk.data.find("tokenizers/punkt")
        print("[INFO] NLTK punkt: already available.")
        return True
    except LookupError:
        pass

    # Try punkt_tab (newer NLTK versions use this name)
    try:
        nltk.data.find("tokenizers/punkt_tab")
        print("[INFO] NLTK punkt_tab: already available.")
        return True
    except LookupError:
        pass

    # Download
    print("[INFO] Downloading NLTK punkt tokenizer...")
    try:
        success = nltk.download(
            "punkt",
            download_dir=nltk_dir,
            quiet=True,
        )
        if success:
            print("[INFO] NLTK punkt downloaded successfully.")
            return True

        # Try punkt_tab as fallback (NLTK >= 3.8.1)
        success = nltk.download(
            "punkt_tab",
            download_dir=nltk_dir,
            quiet=True,
        )
        if success:
            print("[INFO] NLTK punkt_tab downloaded successfully.")
            return True

        print("[WARN] NLTK download returned False — sumy will use fallback.")
        return False

    except Exception as e:
        print(f"[WARN] NLTK download failed: {e} — sumy will use fallback.")
        return False


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
DB_LINK_MAX        = 999
DB_TITLE_MAX       = 499
DB_FEED_URL_MAX    = 499
DB_SOURCE_TYPE_MAX = 19
DB_HASH_MAX        = 64
DB_CATEGORY_MAX    = 49

# Timeout budgets (seconds)
FEED_FETCH_TIMEOUT  = 7
FEEDS_SCAN_TIMEOUT  = 20
SCRAPE_TIMEOUT      = 12
TRANSLATION_TIMEOUT = 45
ALBUM_TIMEOUT       = 15    # non-fatal if exceeded
CAPTION_TIMEOUT     = 12    # fatal if exceeded — retried as text

# Image settings
MAX_IMAGES       = 10   # scraped from article
MAX_ALBUM_IMAGES = 4    # sent in media_group (avoids timeout with 10)
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_BLOCKLIST  = [
    "doubleclick", "googletagmanager", "googlesyndication",
    "facebook.com/tr", "analytics", "pixel", "beacon",
    "tracking", "counter", "stat.", "stats.",
]

# Summarizer
SUMMARY_SENTENCES = 8

# MyMemory translation
MYMEMORY_CHUNK_SIZE  = 450
MYMEMORY_CHUNK_DELAY = 1.0
MYMEMORY_EMAIL       = ""

# Duplicate detection
FUZZY_SIMILARITY_THRESHOLD = 0.65
FUZZY_LOOKBACK_COUNT       = 50

# Domain dedup — allow up to N articles per domain per run
# Set to 1 to keep strict one-per-domain behaviour
# Set to 2 to allow two articles from the same domain
MAX_PER_DOMAIN = 1

TITLE_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "its", "it", "this", "that", "these", "those", "and", "or",
    "but", "as", "up", "out", "if", "about", "into", "over",
    "after", "new", "first", "last", "says", "said",
}

# Smart scheduling — peak hour bonus (Tehran UTC+3:30)
PEAK_HOURS_UTC  = {4, 5, 6, 9, 10, 11, 16, 17, 18, 19}
PEAK_HOUR_BONUS = 15

# Content categories
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

# Hashtag map
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
    "sustainability":"#Sustainability #مد_پایدار",
    "beauty":        "#Beauty #زیبایی",
    "trend":         "#Trend #ترند",
    "style":         "#Style #استایل",
    "celebrity":     "#Celebrity #سلبریتی",
    "streetwear":    "#Streetwear #استریت_ویر",
    "luxury":        "#Luxury #لاکچری",
    "vintage":       "#Vintage #وینتیج",
}

MAX_HASHTAGS = 4

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
    print("[INFO] ═══ Function 1 v8.1 started ═══")

    # ── Fix 1: Download NLTK punkt before anything else ──
    ensure_nltk_data()

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
    current_hour   = now.hour
    is_peak        = current_hour in PEAK_HOURS_UTC

    print(
        f"[INFO] UTC hour: {current_hour} | "
        f"Peak: {'YES +' + str(PEAK_HOUR_BONUS) + 'pts' if is_peak else 'no'}"
    )

    # ── Pre-load recent titles for fuzzy matching ──
    print(f"[INFO] [{elapsed()}s] Loading recent titles...")
    recent_titles = load_recent_titles(
        databases, database_id, COLLECTION_ID,
        sdk_mode, FUZZY_LOOKBACK_COUNT,
    )
    print(f"[INFO] [{elapsed()}s] {len(recent_titles)} titles loaded.")

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
                is_peak=is_peak,
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
    category     = candidate["category"]
    content_hash = make_content_hash(title)
    title_hash   = make_title_hash(title, feed_url)

    print(
        f"[INFO] [{elapsed()}s] "
        f"Selected (score={score}, cat={category}): {title[:60]}"
    )

    # ════════════════════════════════════════════════
    # PHASE 2 — PARALLEL SCRAPE: TEXT + IMAGES
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 2: Scraping...")

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

    full_text  = text_result  if not isinstance(text_result, Exception) else None
    image_urls = image_result if not isinstance(image_result, Exception) else []

    if isinstance(text_result, Exception):
        print(f"[WARN] Text scrape exception: {text_result}")
    if isinstance(image_result, Exception):
        print(f"[WARN] Image scrape exception: {image_result}")

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
        print(f"[WARN] [{elapsed()}s] Content too thin — skipping.")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE,
            sdk_mode, title_hash, content_hash,
            category, score, now.hour,
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
            link, title, feed_url, pub_date, SOURCE_TYPE,
            sdk_mode, title_hash, content_hash,
            category, score, now.hour,
        )
        return {"status": "error", "reason": "translation_failed", "posted": False}

    print(
        f"[INFO] [{elapsed()}s] "
        f"title_fa={len(title_fa)} | body_fa={len(body_fa)}"
    )

    # ════════════════════════════════════════════════
    # PHASE 4 — CAPTION + TELEGRAM POST
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 4: Posting...")

    hashtags = extract_hashtags(title, desc)
    caption  = build_caption(title_fa, body_fa, hashtags, category)

    print(
        f"[INFO] [{elapsed()}s] "
        f"Caption: {len(caption)} chars | "
        f"Images: {len(image_urls)} (album: {min(len(image_urls), MAX_ALBUM_IMAGES)}) | "
        f"Tags: {len(hashtags)}"
    )

    try:
        posted = await send_to_telegram(
            bot, chat_id, caption, image_urls,
        )
    except Exception as e:
        print(f"[ERROR] [{elapsed()}s] Telegram outer: {e}")
        posted = False

    if posted:
        print(f"[SUCCESS] [{elapsed()}s] Posted: {title[:60]}")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE,
            sdk_mode, title_hash, content_hash,
            category, score, now.hour,
        )
    else:
        print(f"[ERROR] [{elapsed()}s] All Telegram attempts failed.")

    print(
        f"[INFO] ═══ v8.1 done in {elapsed()}s | posted={posted} ═══"
    )
    return {"status": "success", "posted": posted}


# ─────────────────────────────────────────────
# CONTENT PROFILING
# ─────────────────────────────────────────────

def detect_category(title, description):
    combined = (title + " " + description).lower()
    for category, keywords in CONTENT_CATEGORIES.items():
        for kw in keywords:
            if kw in combined:
                return category
    return "general"


def extract_hashtags(title, description):
    combined = (title + " " + description).lower()
    hashtags = []
    seen     = set()
    for keyword, tags in HASHTAG_MAP.items():
        if keyword in combined and keyword not in seen:
            hashtags.append(tags)
            seen.add(keyword)
            if len(hashtags) >= MAX_HASHTAGS:
                break
    return hashtags


# ─────────────────────────────────────────────
# PHASE 1 — CANDIDATE SELECTION
# ─────────────────────────────────────────────

async def find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, now,
    recent_titles, is_peak,
):
    loop  = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, fetch_feed_entries, url, time_threshold)
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

    print(f"[INFO] {len(all_candidates)} recent articles collected.")
    if not all_candidates:
        return None

    for c in all_candidates:
        c["score"]    = score_article(c, now, is_peak)
        c["category"] = detect_category(c["title"], c["description"])

    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    print("[INFO] Top candidates:")
    for c in all_candidates[:5]:
        print(
            f"       [{c['score']:>3}] [{c['category']:<14}] "
            f"{c['title'][:55]}"
        )

    # domain → count of articles already selected from this domain
    domain_counts = {}

    for c in all_candidates:
        link         = c["link"]
        title        = c["title"]
        feed_url     = c["feed_url"]
        domain       = get_domain(link)
        content_hash = make_content_hash(title)
        title_hash   = make_title_hash(title, feed_url)

        # L4: Domain dedup (allow up to MAX_PER_DOMAIN per domain)
        count = domain_counts.get(domain, 0)
        if count >= MAX_PER_DOMAIN:
            print(f"[INFO] Dup L4 (domain {domain}): {title[:50]}")
            continue
        domain_counts[domain] = count + 1

        # L1: Exact link
        if _query_field(
            databases, database_id, collection_id,
            "link", link[:DB_LINK_MAX], sdk_mode,
        ):
            print(f"[INFO] Dup L1 (link): {title[:55]}")
            domain_counts[domain] -= 1   # undo domain count increment
            continue

        # L2: Content hash (cross-site)
        if _query_field(
            databases, database_id, collection_id,
            "content_hash", content_hash, sdk_mode,
        ):
            print(f"[INFO] Dup L2 (content_hash): {title[:55]}")
            domain_counts[domain] -= 1
            continue

        # L2b: Legacy title_hash
        if _query_field(
            databases, database_id, collection_id,
            "title_hash", title_hash, sdk_mode,
        ):
            print(f"[INFO] Dup L2b (title_hash): {title[:55]}")
            domain_counts[domain] -= 1
            continue

        # L3: Fuzzy match
        is_fuzz, matched, fuzz_score = is_fuzzy_duplicate(
            title, recent_titles
        )
        if is_fuzz:
            print(
                f"[INFO] Dup L3 (fuzzy {fuzz_score:.2f}): {title[:45]} "
                f"≈ {(matched or '')[:40]}"
            )
            domain_counts[domain] -= 1
            continue

        print(
            f"[INFO] Passed all checks "
            f"(best_fuzz={fuzz_score:.2f}): {title[:55]}"
        )
        return c

    print("[INFO] All candidates exhausted.")
    return None


def score_article(candidate, now, is_peak=False):
    score     = 0
    age_hours = (now - candidate["pub_date"]).total_seconds() / 3600

    if age_hours <= 3:
        score += SCORE_RECENCY_MAX
    elif age_hours <= ARTICLE_AGE_HOURS:
        score += int(
            SCORE_RECENCY_MAX
            * (1 - (age_hours - 3) / (ARTICLE_AGE_HOURS - 3))
        )

    title_lower = candidate["title"].lower()
    desc_lower  = candidate["description"].lower()
    matched = 0
    for kw in TREND_KEYWORDS:
        if matched >= 3:
            break
        if kw in title_lower:
            score   += SCORE_TITLE_KEYWORD
            matched += 1
        elif kw in desc_lower:
            score   += 5
            matched += 1

    if extract_rss_image(candidate["entry"]):
        score += SCORE_HAS_IMAGE

    if len(candidate["description"]) > 200:
        score += SCORE_DESC_LENGTH

    if is_peak:
        score += PEAK_HOUR_BONUS

    return min(score, 100)


def fetch_feed_entries(feed_url, time_threshold):
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
        link  = (entry.get("link") or "").strip()
        if not title or not link:
            continue
        raw  = entry.get("summary") or entry.get("description") or ""
        desc = re.sub(r"<[^>]+>", " ", raw)
        desc = re.sub(r"\s+", " ", desc).strip()
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


# ─────────────────────────────────────────────
# DUPLICATE DETECTION
# ─────────────────────────────────────────────

def make_content_hash(title):
    tokens     = normalize_title_tokens(title)
    normalized = " ".join(sorted(tokens))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def make_title_hash(title, feed_url):
    normalized = (title.lower().strip() + feed_url[:50]).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def normalize_title_tokens(title):
    title  = title.lower()
    title  = re.sub(r"[^a-z0-9\s]", " ", title)
    tokens = title.split()
    return frozenset(
        t for t in tokens
        if t not in TITLE_STOP_WORDS and len(t) >= 2
    )


def jaccard_similarity(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_recent_titles(databases, database_id, collection_id,
                       sdk_mode, limit):
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
                (d.get("title", ""), normalize_title_tokens(d.get("title", "")))
                for d in docs if d.get("title")
            ]
        except Exception as e:
            print(f"[WARN] load_recent_titles: {e}")
            return []


def is_fuzzy_duplicate(title, recent_titles):
    if not recent_titles:
        return False, None, 0.0
    incoming   = normalize_title_tokens(title)
    best_score = 0.0
    best_match = None
    for stored_title, stored_tokens in recent_titles:
        score = jaccard_similarity(incoming, stored_tokens)
        if score > best_score:
            best_score = score
            best_match = stored_title
    if best_score >= FUZZY_SIMILARITY_THRESHOLD:
        return True, best_match, best_score
    return False, None, best_score


def get_domain(url):
    try:
        parts = urlparse(url).netloc.replace("www.", "").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else url[:30]
    except Exception:
        return url[:30]


def _query_field(databases, database_id, collection_id,
                 field, value, sdk_mode):
    """
    Generic Appwrite field equality check.
    Returns True if match found, False on error or no match.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            if sdk_mode == "new":
                r = databases.list_rows(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=[Query.equal(field, value)],
                )
            else:
                r = databases.list_documents(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=[Query.equal(field, value)],
                )
            return r["total"] > 0
        except AppwriteException as e:
            print(f"[WARN] _query_field ({field}): {e.message}")
            return False
        except Exception as e:
            print(f"[WARN] _query_field ({field}): {e}")
            return False


# ─────────────────────────────────────────────
# PHASE 2A — TEXT SCRAPER
# ─────────────────────────────────────────────

def scrape_article_text(url):
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
            "script", "style", "nav", "footer", "header",
            "aside", "form", "iframe", "noscript",
            "figcaption", "button", "input", "select", "svg",
        ]):
            tag.decompose()
        body = (
            soup.find("article")
            or soup.find("div", {"class": re.compile(r"article[-_]?body", re.I)})
            or soup.find("div", {"class": re.compile(r"post[-_]?content", re.I)})
            or soup.find("div", {"class": re.compile(r"entry[-_]?content", re.I)})
            or soup.find("div", {"class": re.compile(r"story[-_]?body", re.I)})
            or soup.find("main")
        )
        paragraphs = (body or soup).find_all("p")
        text = " ".join(
            p.get_text(" ").strip()
            for p in paragraphs
            if len(p.get_text().strip()) > 40
        )
        text = re.sub(r"\s+", " ", text).strip()
        return text[:MAX_SCRAPED_CHARS] if len(text) >= 100 else None
    except requests.exceptions.Timeout:
        print(f"[WARN] Text timeout: {url[:60]}")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"[WARN] Text HTTP {e.response.status_code}: {url[:60]}")
        return None
    except Exception as e:
        print(f"[WARN] Text scrape: {e}")
        return None


# ─────────────────────────────────────────────
# PHASE 2B — IMAGE SCRAPER
# ─────────────────────────────────────────────

def scrape_article_images(url, rss_entry):
    images = []
    seen   = set()

    def add_image(img_url):
        if not img_url:
            return
        img_url = img_url.strip()
        if not img_url.startswith("http") or img_url in seen:
            return
        url_lower = img_url.lower()
        if any(b in url_lower for b in IMAGE_BLOCKLIST):
            return
        has_ext = any(
            img_url.lower().split("?")[0].endswith(e)
            for e in IMAGE_EXTENSIONS
        )
        has_word = any(
            w in url_lower
            for w in ["image", "photo", "img", "picture", "media", "cdn"]
        )
        if not has_ext and not has_word:
            return
        seen.add(img_url)
        images.append(img_url)

    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible)"},
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
            or soup.find("div", {"class": re.compile(r"post[-_]?content", re.I)})
            or soup.find("div", {"class": re.compile(r"entry[-_]?content", re.I)})
            or soup.find("main")
        )
        area = body if body else soup
        for img in area.find_all("img"):
            src = (
                img.get("data-src") or img.get("data-original")
                or img.get("data-lazy-src") or img.get("src") or ""
            )
            add_image(src)
            if len(images) >= MAX_IMAGES:
                break
        if len(images) < MAX_IMAGES:
            for source in area.find_all("source"):
                srcset = source.get("srcset", "")
                if srcset:
                    add_image(srcset.split(",")[0].strip().split(" ")[0])
                if len(images) >= MAX_IMAGES:
                    break
    except Exception as e:
        print(f"[WARN] Image scrape: {e}")

    if len(images) < MAX_IMAGES:
        rss_img = extract_rss_image(rss_entry)
        if rss_img:
            add_image(rss_img)

    print(f"[INFO] Images: {len(images)} scraped")
    return images[:MAX_IMAGES]


def extract_rss_image(entry):
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
    return None


# ─────────────────────────────────────────────
# PHASE 3 — SUMMARIZE + TRANSLATE
# ─────────────────────────────────────────────

def extractive_summarize(text, sentence_count=8):
    """
    LSA extractive summarization.
    Requires NLTK punkt — downloaded at startup.
    Falls back to first 1200 chars if sumy fails.
    """
    try:
        parser     = PlaintextParser.from_string(text, Tokenizer("english"))
        stemmer    = Stemmer("english")
        summarizer = LsaSummarizer(stemmer)
        summarizer.stop_words = get_stop_words("english")
        sentences  = summarizer(parser.document, sentence_count)
        result     = " ".join(str(s) for s in sentences).strip()
        if result:
            print(f"[INFO] sumy OK: {len(result)} chars from {len(text)} chars")
            return result
        print("[WARN] sumy returned empty — using fallback.")
        return text[:1200]
    except Exception as e:
        print(f"[WARN] sumy failed: {e}")
        return text[:1200]


def translate_mymemory(text, source="en", target="fa"):
    if not text or not text.strip():
        return ""
    chunks     = split_into_chunks(text, MYMEMORY_CHUNK_SIZE)
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
                print("[WARN] MyMemory quota reached.")
                translated.append(chunk)
                continue
            if (
                trans
                and "MYMEMORY WARNING" not in trans
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
            print(f"[WARN] MyMemory chunk {i+1}: {e}")
            translated.append(chunk)
    return " ".join(translated).strip() if translated else None


def split_into_chunks(text, max_chars):
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks    = []
    current   = ""
    for sentence in sentences:
        if len(sentence) > max_chars:
            parts = sentence.split(", ")
            for part in parts:
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


def translate_article(title, body):
    print(f"[INFO] Translating title ({len(title)} chars)...")
    title_fa = translate_mymemory(title)
    time.sleep(1)
    print(f"[INFO] Translating body ({len(body)} chars)...")
    body_fa  = translate_mymemory(body)
    return title_fa or title, body_fa or body


# ─────────────────────────────────────────────
# PHASE 4 — CAPTION BUILDER
# ─────────────────────────────────────────────

def build_caption(title_fa, body_fa, hashtags, category):
    category_emoji = {
        "runway":        "👗",
        "brand":         "🏷️",
        "business":      "📊",
        "beauty":        "💄",
        "sustainability":"♻️",
        "celebrity":     "⭐",
        "trend":         "🔥",
        "general":       "🌐",
    }
    emoji = category_emoji.get(category, "🌐")

    def esc(t):
        return (
            t.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    safe_title = esc(title_fa.strip())
    safe_body  = esc(body_fa.strip())
    hashtag_line = " ".join(hashtags) if hashtags else ""

    parts = [f"<b>{safe_title}</b>", "@irfashionnews", safe_body]
    if hashtag_line:
        parts.append(hashtag_line)
    parts.append(f"{emoji} <i>کانال مد و فشن ایرانی</i>")

    caption = "\n\n".join(parts)

    if len(caption) > CAPTION_MAX:
        overflow  = len(caption) - CAPTION_MAX
        safe_body = safe_body[:max(0, len(safe_body) - overflow - 5)] + "…"
        parts[2]  = safe_body
        caption   = "\n\n".join(parts)

    return caption


# ─────────────────────────────────────────────
# TELEGRAM SENDER — TWO-STEP WITH FIXED TIMEOUTS
# ─────────────────────────────────────────────

async def send_to_telegram(bot, chat_id, caption, image_urls):
    """
    Step 1: send_media_group (capped at MAX_ALBUM_IMAGES, no caption)
            Timeout: ALBUM_TIMEOUT (non-fatal)
    Step 2: send_photo (first image + caption)
            Timeout: CAPTION_TIMEOUT (falls back to text on failure)

    Fix vs v8.0:
      Album capped at 4 images (was 10 → caused timeout)
      Album and caption have separate timeout budgets
    """

    # ── Step 1: Image album (non-fatal) ──
    album_images = image_urls[:MAX_ALBUM_IMAGES]

    if len(album_images) >= 2:
        try:
            media_group = [
                InputMediaPhoto(media=url) for url in album_images
            ]
            await asyncio.wait_for(
                bot.send_media_group(
                    chat_id=chat_id,
                    media=media_group,
                    disable_notification=True,
                ),
                timeout=ALBUM_TIMEOUT,
            )
            print(f"[INFO] Album sent: {len(media_group)} photos.")
        except asyncio.TimeoutError:
            print(
                f"[WARN] Album timed out after {ALBUM_TIMEOUT}s "
                "(non-fatal — continuing to caption post)."
            )
        except Exception as e:
            print(f"[WARN] Album failed (non-fatal): {str(e)[:100]}")

    # ── Step 2: Caption post (fatal if completely fails) ──
    caption_image = image_urls[0] if image_urls else None

    # Try with image
    if caption_image:
        try:
            await asyncio.wait_for(
                bot.send_photo(
                    chat_id=chat_id,
                    photo=caption_image,
                    caption=caption,
                    parse_mode="HTML",
                    disable_notification=True,
                ),
                timeout=CAPTION_TIMEOUT,
            )
            print("[INFO] Caption photo sent.")
            return True
        except asyncio.TimeoutError:
            print(f"[WARN] Caption photo timed out — trying text-only.")
        except Exception as e:
            print(f"[WARN] Caption photo failed: {str(e)[:100]}")

    # Fallback: text-only
    try:
        await asyncio.wait_for(
            bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                disable_notification=True,
            ),
            timeout=CAPTION_TIMEOUT,
        )
        print("[INFO] Text-only caption sent.")
        return True
    except Exception as e:
        print(f"[ERROR] Text-only also failed: {str(e)[:100]}")
        return False


# ─────────────────────────────────────────────
# APPWRITE DATABASE HELPERS
# ─────────────────────────────────────────────

def _build_db_data(link, title, feed_url, pub_date, source_type,
                   title_hash, content_hash, category,
                   trend_score, post_hour):
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
    }


def save_to_db(databases, database_id, collection_id,
               link, title, feed_url, pub_date, source_type,
               sdk_mode, title_hash, content_hash,
               category, trend_score, post_hour):
    data = _build_db_data(
        link, title, feed_url, pub_date, source_type,
        title_hash, content_hash, category, trend_score, post_hour,
    )
    print(f"[INFO] DB save: {data['link'][:65]}")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            if sdk_mode == "new":
                databases.create_row(
                    database_id=database_id,
                    collection_id=collection_id,
                    row_id="unique()",
                    data=data,
                )
            else:
                databases.create_document(
                    database_id=database_id,
                    collection_id=collection_id,
                    document_id="unique()",
                    data=data,
                )
            print("[SUCCESS] Saved to Appwrite.")
        except AppwriteException as e:
            print(f"[ERROR] save_to_db: {e.message}")
        except Exception as e:
            print(f"[ERROR] save_to_db: {e}")


# ─────────────────────────────────────────────
# LOCAL TEST
# ─────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())
