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
                    collection_id=collection_id,
                    queries=queries,
                )
                docs = r.get("documents", [])

            result = []
            for doc in docs:
                stored_title = doc.get("title", "")
                if stored_title:
                    result.append((
                        stored_title,
                        normalize_title_tokens(stored_title),
                    ))
            return result

        except AppwriteException as e:
            print(f"[WARN] load_recent_titles: {e.message}")
            return []
        except Exception as e:
            print(f"[WARN] load_recent_titles: {e}")
            return []


def is_fuzzy_duplicate(title, recent_titles):
    """
    Compare incoming article title against recent DB titles.
    Uses Jaccard token similarity — 100% offline.

    Returns (True, matched_title, score) if duplicate found.
    Returns (False, None, 0.0) if unique.

    Threshold: FUZZY_SIMILARITY_THRESHOLD (default 0.65)
    """
    if not recent_titles:
        return False, None, 0.0

    incoming_tokens = normalize_title_tokens(title)
    if not incoming_tokens:
        return False, None, 0.0

    best_score    = 0.0
    best_match    = None

    for stored_title, stored_tokens in recent_titles:
        score = jaccard_similarity(incoming_tokens, stored_tokens)
        if score > best_score:
            best_score = score
            best_match = stored_title

    if best_score >= FUZZY_SIMILARITY_THRESHOLD:
        return True, best_match, best_score

    return False, None, best_score


def get_domain(url):
    """Extract root domain from URL for per-domain deduplication."""
    try:
        parsed = urlparse(url)
        # e.g. "www.vogue.com" → "vogue.com"
        parts  = parsed.netloc.replace("www.", "").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else parsed.netloc
    except Exception:
        return url[:30]


# ─────────────────────────────────────────────
# PHASE 1 — CANDIDATE SELECTION
# ─────────────────────────────────────────────

async def find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, now, recent_titles,
):
    """
    Full duplicate-aware candidate selection.

    For each candidate (sorted by score, highest first):
      L1: exact link check      → Appwrite DB query
      L2: content_hash check    → Appwrite DB query (cross-site)
      L3: fuzzy title check     → in-memory Jaccard (cross-site, paraphrased)
      L4: domain dedup          → in-memory set (one article per domain)

    Returns first candidate that passes all checks.
    """
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

    # Score all candidates
    for c in all_candidates:
        c["score"] = score_article(c, now)

    # Sort highest score first
    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    print("[INFO] Top candidates by score:")
    for c in all_candidates[:5]:
        print(f"       [{c['score']:>3}] {c['title'][:60]}")

    # Track domains already seen in this run
    seen_domains = set()

    for c in all_candidates:
        link         = c["link"]
        title        = c["title"]
        feed_url     = c["feed_url"]
        domain       = get_domain(link)
        content_hash = make_content_hash(title)
        title_hash   = make_title_hash(title, feed_url)

        # ── L4: Domain dedup (in-memory, no DB call) ──
        if domain in seen_domains:
            print(f"[INFO] Domain dedup ({domain}): {title[:50]}")
            continue
        seen_domains.add(domain)

        # ── L1: Exact link match ──
        if is_duplicate(
            databases, database_id, collection_id, link, sdk_mode
        ):
            print(f"[INFO] Dup L1 (link): {title[:55]}")
            continue

        # ── L2: Content hash (cross-site exact title) ──
        if is_duplicate_by_content_hash(
            databases, database_id, collection_id, content_hash, sdk_mode
        ):
            print(f"[INFO] Dup L2 (content_hash): {title[:55]}")
            continue

        # ── L2b: Legacy title_hash (backward compat) ──
        if is_duplicate_by_field(
            databases, database_id, collection_id,
            "title_hash", title_hash, sdk_mode
        ):
            print(f"[INFO] Dup L2b (title_hash): {title[:55]}")
            continue

        # ── L3: Fuzzy title similarity (in-memory, no DB call) ──
        is_fuzz, matched, fuzz_score = is_fuzzy_duplicate(
            title, recent_titles
        )
        if is_fuzz:
            print(
                f"[INFO] Dup L3 (fuzzy {fuzz_score:.2f}): {title[:45]} "
                f"≈ {(matched or '')[:45]}"
            )
            continue

        # Passed all checks — this is our candidate
        print(
            f"[INFO] Selected (fuzz_score={fuzz_score:.2f} < threshold): "
            f"{title[:55]}"
        )
        return c

    print("[INFO] All candidates exhausted — all duplicates.")
    return None


def score_article(candidate, now):
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
        })
    return candidates


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
        print(f"[WARN] Text scrape timeout: {url[:60]}")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"[WARN] Text scrape HTTP {e.response.status_code}: {url[:60]}")
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
        if not img_url.startswith("http"):
            return
        if img_url in seen:
            return
        url_lower = img_url.lower()
        if any(blocked in url_lower for blocked in IMAGE_BLOCKLIST):
            return
        has_ext = any(
            img_url.lower().split("?")[0].endswith(ext)
            for ext in IMAGE_EXTENSIONS
        )
        has_image_word = any(
            word in url_lower
            for word in ["image", "photo", "img", "picture", "media", "cdn"]
        )
        if not has_ext and not has_image_word:
            return
        seen.add(img_url)
        images.append(img_url)

    try:
        resp = requests.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            },
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
                img.get("data-src")
                or img.get("data-original")
                or img.get("data-lazy-src")
                or img.get("src")
                or ""
            )
            add_image(src)
            if len(images) >= MAX_IMAGES:
                break

        if len(images) < MAX_IMAGES:
            for source in area.find_all("source"):
                srcset = source.get("srcset", "")
                if srcset:
                    first = srcset.split(",")[0].strip().split(" ")[0]
                    add_image(first)
                if len(images) >= MAX_IMAGES:
                    break

    except Exception as e:
        print(f"[WARN] Image HTML scrape: {e}")

    if len(images) < MAX_IMAGES:
        rss_img = extract_rss_image(rss_entry)
        if rss_img:
            add_image(rss_img)

    print(f"[INFO] Images: {len(images)} collected")
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
                print("[WARN] MyMemory: daily quota reached.")
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
                print(f"[WARN] MyMemory chunk {i+1}: {trans[:60]}")
                translated.append(chunk)

            if i < len(chunks) - 1:
                time.sleep(MYMEMORY_CHUNK_DELAY)

        except requests.exceptions.Timeout:
            print(f"[WARN] MyMemory timeout chunk {i+1}")
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
    body_fa = translate_mymemory(body)
    return title_fa or title, body_fa or body


# ─────────────────────────────────────────────
# PHASE 4 — CAPTION BUILDER
# ─────────────────────────────────────────────

def build_caption(title_fa, body_fa):
    def esc(t):
        return (
            t.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    safe_title = esc(title_fa.strip())
    safe_body  = esc(body_fa.strip())

    parts = [
        f"<b>{safe_title}</b>",
        "@irfashionnews",
        safe_body,
        "🌐 <i>کانال مد و فشن ایرانی</i>",
    ]

    caption = "\n\n".join(parts)

    if len(caption) > CAPTION_MAX:
        overflow  = len(caption) - CAPTION_MAX
        safe_body = safe_body[:max(0, len(safe_body) - overflow - 5)] + "…"
        parts[2]  = safe_body
        caption   = "\n\n".join(parts)

    return caption


# ─────────────────────────────────────────────
# TELEGRAM SENDER — TWO-STEP (v7.2+)
# ─────────────────────────────────────────────

async def send_to_telegram(bot, chat_id, caption, image_urls):
    """
    Step 1: send_media_group (all images, NO caption)
    Step 2: send_photo (first image + full caption)
    Step 1 failure is non-fatal.
    """
    # Step 1: Album
    if len(image_urls) >= 2:
        try:
            media_group = [
                InputMediaPhoto(media=url)
                for url in image_urls[:MAX_IMAGES]
            ]
            await bot.send_media_group(
                chat_id=chat_id,
                media=media_group,
                disable_notification=True,
            )
            print(f"[INFO] Album sent: {len(media_group)} photos.")
        except Exception as e:
            print(f"[WARN] Album failed (continuing): {str(e)[:100]}")

    # Step 2: Caption post
    caption_image = image_urls[0] if image_urls else None

    try:
        if caption_image:
            await bot.send_photo(
                chat_id=chat_id,
                photo=caption_image,
                caption=caption,
                parse_mode="HTML",
                disable_notification=True,
            )
            print(f"[INFO] Caption photo sent.")
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                disable_notification=True,
            )
            print("[INFO] Text-only caption sent.")
        return True

    except Exception as e:
        print(f"[WARN] Caption photo failed: {str(e)[:100]}")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                disable_notification=True,
            )
            print("[INFO] Text-only fallback sent.")
            return True
        except Exception as e2:
            print(f"[ERROR] All Telegram methods failed: {str(e2)[:100]}")
            return False


# ─────────────────────────────────────────────
# APPWRITE DATABASE HELPERS
# ─────────────────────────────────────────────

def _build_db_data(link, title, feed_url, pub_date,
                   source_type, title_hash, content_hash):
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
    }


def is_duplicate(databases, database_id, collection_id, link, sdk_mode):
    """L1: exact link match."""
    return _query_field(
        databases, database_id, collection_id,
        "link", link[:DB_LINK_MAX], sdk_mode,
    )


def is_duplicate_by_content_hash(databases, database_id, collection_id,
                                  content_hash, sdk_mode):
    """L2: normalized title hash — cross-site exact title."""
    return _query_field(
        databases, database_id, collection_id,
        "content_hash", content_hash, sdk_mode,
    )


def is_duplicate_by_field(databases, database_id, collection_id,
                           field, value, sdk_mode):
    """Generic field equality check."""
    return _query_field(
        databases, database_id, collection_id, field, value, sdk_mode,
    )


def _query_field(databases, database_id, collection_id,
                 field, value, sdk_mode):
    """
    Shared Appwrite query helper.
    Returns True if any document matches field=value.
    Returns False on any error (fail-open = don't block posting).
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


def save_to_db(databases, database_id, collection_id,
               link, title, feed_url, pub_date,
               source_type, sdk_mode, title_hash, content_hash):
    data = _build_db_data(
        link, title, feed_url, pub_date,
        source_type, title_hash, content_hash,
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
