# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    8.3 — OVER-FILTERING FIX
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# Fixed in v8.3 vs v8.2:
#
#   [FIX-1] L4a domain dedup logic corrected.
#           OLD (WRONG): domain added to seen_domains on ANY
#                        candidate from that domain, including
#                        ones already rejected by L1/L2/L3.
#                        Result: domain "used up" by old posts,
#                        blocking all fresh articles from same domain.
#           NEW (CORRECT): domain only consumed when candidate
#                          PASSES L1+L2+L3 and is selected.
#                          Fresh articles from prolific domains
#                          are no longer silently suppressed.
#
#   [FIX-2] L4a scope clarified.
#           Purpose: prevent posting TWO articles from the
#           same domain in a single execution run.
#           NOT: prevent any article from a domain that had
#           a previously-posted article anywhere in history.
#
#   [FIX-3] L4b (cross-run domain hash) made optional/relaxed.
#           Cross-run domain blocking was set to 6 hours,
#           which when combined with broken L4a was causing
#           total silence. Raised to informational warning only
#           — does not block candidates.
#           Rationale: L1 (exact link) + L2 (content_hash)
#           are sufficient cross-run duplicate protection.
#           Domain-level cross-run blocking is too aggressive
#           for prolific sources (vogue, whowhatwear, wwd).
#
#   [FIX-4] SDK deprecation warnings suppressed globally.
#           DeprecationWarning for list_documents is cosmetic
#           and clutters logs. Suppressed at module level.
#
# Duplicate protection after fixes:
#   L1:  exact link (blocks repost of same URL)         ← hard block
#   L2:  content_hash (blocks same story, diff URL)     ← hard block
#   L2b: title_hash legacy (backward compat)            ← hard block
#   L3:  fuzzy Jaccard ≥ 0.65 (paraphrased titles)     ← hard block
#   L4a: one article per domain PER RUN                 ← soft block
#        (only applied after L1–L3 pass)
#   L4b: domain cross-run (informational log only)      ← warning only
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

# Suppress Appwrite SDK deprecation warnings globally
warnings.filterwarnings("ignore", category=DeprecationWarning, module="appwrite")


# ═══════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS & CONFIGURATION
# ═══════════════════════════════════════════════════════════

COLLECTION_ID = "history"
SOURCE_TYPE   = "en"

ARTICLE_AGE_HOURS = 36
MIN_CONTENT_CHARS = 150
MAX_SCRAPED_CHARS = 3000
MAX_RSS_CHARS     = 1000

CAPTION_MAX = 1020
MAX_IMAGES  = 10

# Appwrite field size limits
DB_LINK_MAX        = 999
DB_TITLE_MAX       = 499
DB_FEED_URL_MAX    = 499
DB_SOURCE_TYPE_MAX = 19
DB_HASH_MAX        = 64
DB_CATEGORY_MAX    = 49
DB_DOMAIN_HASH_MAX = 64

# Timeouts
FEED_FETCH_TIMEOUT  = 7
FEEDS_SCAN_TIMEOUT  = 22
SCRAPE_TIMEOUT      = 12
TRANSLATION_TIMEOUT = 45
TELEGRAM_TIMEOUT    = 35

SUMMARY_SENTENCES = 8

# MyMemory
MYMEMORY_CHUNK_SIZE  = 450
MYMEMORY_CHUNK_DELAY = 1.0
MYMEMORY_EMAIL       = "lasvaram@gmail.com"

# Image scraping
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_BLOCKLIST  = [
    "doubleclick", "googletagmanager", "googlesyndication",
    "facebook.com/tr", "analytics", "pixel", "beacon",
    "tracking", "counter", "stat.", "stats.",
]

# Duplicate detection
FUZZY_SIMILARITY_THRESHOLD = 0.65
FUZZY_LOOKBACK_COUNT       = 150

# [FIX-3] Domain cross-run dedup is now WARNING ONLY.
# L1 + L2 handle true duplicate prevention.
# L4b is purely informational.
DOMAIN_DEDUP_HOURS = 6

TITLE_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "its", "it", "this", "that", "these", "those", "and", "or",
    "but", "as", "up", "out", "if", "about", "into", "over",
    "after", "new", "first", "last", "says", "said",
}

# Peak hours (UTC, mapped from Tehran UTC+3:30)
PEAK_HOURS_UTC  = {4, 5, 6, 9, 10, 11, 16, 17, 18, 19}
PEAK_HOUR_BONUS = 15

SCORE_RECENCY_MAX   = 40
SCORE_TITLE_KEYWORD = 15
SCORE_DESC_KEYWORD  = 5
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
}
MAX_HASHTAGS = 4

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


# ═══════════════════════════════════════════════════════════
# SECTION 2 — MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

async def main(event=None, context=None):
    print("[INFO] ═══ Function 1 v8.3 started ═══")
    loop       = asyncio.get_event_loop()
    start_time = loop.time()

    def elapsed():
        return round(loop.time() - start_time, 1)

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
    print(f"[INFO] Appwrite SDK mode: {sdk_mode}")

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)
    current_hour   = now.hour
    is_peak        = current_hour in PEAK_HOURS_UTC

    print(
        f"[INFO] UTC hour={current_hour} | "
        f"Peak={'YES +' + str(PEAK_HOUR_BONUS) + 'pts' if is_peak else 'no'}"
    )

    print(f"[INFO] [{elapsed()}s] Loading recent titles for fuzzy check...")
    recent_titles = _load_recent_titles(
        databases, database_id, COLLECTION_ID, sdk_mode, FUZZY_LOOKBACK_COUNT
    )
    print(f"[INFO] [{elapsed()}s] {len(recent_titles)} titles loaded.")

    # Phase 1: RSS scan
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
        print(f"[INFO] [{elapsed()}s] No suitable article found. Exiting.")
        return {"status": "success", "posted": False}

    title        = candidate["title"]
    link         = candidate["link"]
    desc         = candidate["description"]
    feed_url     = candidate["feed_url"]
    pub_date     = candidate["pub_date"]
    entry        = candidate["entry"]
    score        = candidate["score"]
    category     = candidate["category"]
    content_hash = _make_content_hash(title)
    title_hash   = _make_title_hash(title, feed_url)
    domain       = _get_domain(link)
    domain_hash  = _make_domain_hash(domain)

    print(
        f"[INFO] [{elapsed()}s] Candidate: "
        f"score={score} cat={category} | {title[:65]}"
    )

    # Pre-flight strict re-check (race condition guard)
    print(f"[INFO] [{elapsed()}s] Pre-flight strict duplicate re-check...")
    is_dup, dup_reason = _strict_duplicate_check(
        databases, database_id, COLLECTION_ID,
        link, content_hash, title_hash, sdk_mode,
    )
    if is_dup:
        print(
            f"[WARN] [{elapsed()}s] "
            f"Pre-flight caught duplicate ({dup_reason}). Abort."
        )
        return {
            "status": "success",
            "posted": False,
            "reason": f"preflight_{dup_reason}",
        }

    # Phase 5: Save to DB BEFORE posting (distributed lock)
    print(f"[INFO] [{elapsed()}s] Phase 5: Saving to DB (pre-post lock)...")
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
        print(
            f"[WARN] [{elapsed()}s] "
            f"DB save failed — aborting to prevent duplicate post."
        )
        return {"status": "error", "reason": "db_save_failed", "posted": False}

    print(f"[INFO] [{elapsed()}s] DB lock acquired.")

    # Phase 2: Scrape
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

    full_text  = text_result  if not isinstance(text_result,  Exception) else None
    image_urls = image_result if not isinstance(image_result, Exception) else []

    content = (
        full_text
        if full_text and len(full_text) > len(desc)
        else desc[:MAX_RSS_CHARS]
    )

    print(
        f"[INFO] [{elapsed()}s] "
        f"Text={'scraped' if full_text else 'rss'} ({len(content)}ch) | "
        f"Images={len(image_urls)}"
    )

    if len(content) < MIN_CONTENT_CHARS:
        print(f"[WARN] [{elapsed()}s] Content too thin. DB record kept, no post.")
        return {"status": "skipped", "reason": "thin_content", "posted": False}

    # Phase 3: Summarize + Translate
    print(f"[INFO] [{elapsed()}s] Phase 3: Summarize + Translate...")
    english_summary = await loop.run_in_executor(
        None, _extractive_summarize, content, SUMMARY_SENTENCES
    )
    print(f"[INFO] [{elapsed()}s] Summary: {len(english_summary)} chars")

    try:
        title_fa, body_fa = await asyncio.wait_for(
            loop.run_in_executor(
                None, _translate_article, title, english_summary
            ),
            timeout=TRANSLATION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Translation timed out. Using originals.")
        title_fa = title
        body_fa  = english_summary

    if not title_fa or not body_fa:
        print(f"[WARN] [{elapsed()}s] Translation empty. DB record kept.")
        return {"status": "error", "reason": "translation_failed", "posted": False}

    print(
        f"[INFO] [{elapsed()}s] "
        f"title_fa={len(title_fa)}ch | body_fa={len(body_fa)}ch"
    )

    # Phase 4: Build caption
    hashtags = _extract_hashtags(title, desc)
    caption  = _build_caption(title_fa, body_fa, hashtags, category)

    print(
        f"[INFO] [{elapsed()}s] "
        f"Caption={len(caption)}ch | Images={len(image_urls)} | "
        f"Hashtags={len(hashtags)}"
    )

    # Phase 6: Post to Telegram
    print(f"[INFO] [{elapsed()}s] Phase 6: Posting to Telegram...")
    try:
        posted = await asyncio.wait_for(
            _post_to_telegram(bot, chat_id, caption, image_urls),
            timeout=TELEGRAM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Telegram post timed out.")
        posted = False
    except Exception as e:
        print(f"[ERROR] [{elapsed()}s] Telegram unexpected: {e}")
        posted = False

    if posted:
        print(f"[SUCCESS] [{elapsed()}s] Posted: {title[:65]}")
    else:
        print(
            f"[WARN] [{elapsed()}s] Telegram failed. "
            f"DB record retained — article will not be retried."
        )

    print(f"[INFO] ═══ v8.3 done in {elapsed()}s | posted={posted} ═══")
    return {"status": "success", "posted": posted}


# ═══════════════════════════════════════════════════════════
# SECTION 3 — FEED SCANNING & CANDIDATE SELECTION
# ═══════════════════════════════════════════════════════════

async def _find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, now, recent_titles, is_peak,
):
    """
    Fetch all feeds in parallel, score all articles,
    apply duplicate checks L1–L3, then L4a only on passing candidates.

    KEY BEHAVIOR (v8.3 fix):
      L4a (domain/run) is checked AFTER L1+L2+L3 pass.
      A domain is only "consumed" when a candidate from that
      domain has passed all content checks and is eligible.
      Domains are NOT consumed by articles that were already
      rejected by L1/L2/L3 — those are irrelevant old posts.
    """
    loop  = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, _fetch_feed, url, time_threshold)
        for url in feeds
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_candidates = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"[WARN] Feed error ({feeds[i][:50]}): {result}")
            continue
        if result:
            all_candidates.extend(result)

    print(f"[INFO] {len(all_candidates)} articles collected from feeds.")
    if not all_candidates:
        return None

    # Score + categorize all
    for c in all_candidates:
        c["score"]    = _score_article(c, now, is_peak)
        c["category"] = _detect_category(c["title"], c["description"])

    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    print("[INFO] Top 5 candidates before duplicate check:")
    for c in all_candidates[:5]:
        print(
            f"       [{c['score']:>3}] [{c['category']:<14}] "
            f"{c['title'][:60]}"
        )

    # [FIX-3] Load recent domain hashes — informational only
    recent_domain_hashes = _load_recent_domain_hashes(
        databases, database_id, collection_id, sdk_mode
    )
    print(
        f"[INFO] Recent domain hashes loaded: {len(recent_domain_hashes)} "
        f"(informational — does not block)"
    )

    # [FIX-1] Domain tracking — only populated when a candidate
    # PASSES L1+L2+L3. Does not inherit rejected candidates.
    seen_domains_this_run = set()

    for c in all_candidates:
        link         = c["link"]
        title        = c["title"]
        feed_url     = c["feed_url"]
        domain       = _get_domain(link)
        content_hash = _make_content_hash(title)
        title_hash   = _make_title_hash(title, feed_url)
        domain_hash  = _make_domain_hash(domain)

        # ── L1: Exact link ──
        l1 = _query_field(
            databases, database_id, collection_id,
            "link", link[:DB_LINK_MAX], sdk_mode,
        )
        if l1 is True:
            print(f"[SKIP] L1 link: {title[:60]}")
            continue
        if l1 is None:
            print(f"[SKIP] L1 DB error (safe): {title[:60]}")
            continue

        # ── L2: Content hash ──
        l2 = _query_field(
            databases, database_id, collection_id,
            "content_hash", content_hash, sdk_mode,
        )
        if l2 is True:
            print(f"[SKIP] L2 content_hash: {title[:60]}")
            continue
        if l2 is None:
            print(f"[SKIP] L2 DB error (safe): {title[:60]}")
            continue

        # ── L2b: Legacy title_hash ──
        l2b = _query_field(
            databases, database_id, collection_id,
            "title_hash", title_hash, sdk_mode,
        )
        if l2b is True:
            print(f"[SKIP] L2b title_hash: {title[:60]}")
            continue
        if l2b is None:
            print(f"[SKIP] L2b DB error (safe): {title[:60]}")
            continue

        # ── L3: Fuzzy title similarity ──
        is_fuzz, matched, fuzz_score = _fuzzy_duplicate(title, recent_titles)
        if is_fuzz:
            print(
                f"[SKIP] L3 fuzzy {fuzz_score:.2f}: {title[:45]} "
                f"≈ {(matched or '')[:40]}"
            )
            continue

        # ── L4b: Cross-run domain (informational only — does not block) ──
        if domain_hash in recent_domain_hashes:
            print(
                f"[INFO] L4b domain seen recently ({domain}) "
                f"— not blocking, continuing: {title[:50]}"
            )

        # ── L4a: Domain dedup within this run ──
        # [FIX-1] Only reached here if L1+L2+L3 all passed.
        # This domain slot is consumed by a genuinely new article.
        if domain in seen_domains_this_run:
            print(f"[SKIP] L4a domain/run ({domain}): {title[:60]}")
            continue

        # ── PASSED ALL CHECKS ──
        seen_domains_this_run.add(domain)
        print(
            f"[INFO] Candidate passed all checks "
            f"(fuzz={fuzz_score:.2f}): {title[:60]}"
        )
        return c

    print("[INFO] All candidates exhausted after duplicate checks.")
    return None


def _fetch_feed(feed_url, time_threshold):
    import socket
    try:
        old = socket.getdefaulttimeout()
        socket.setdefaulttimeout(FEED_FETCH_TIMEOUT)
        feed = feedparser.parse(feed_url)
        socket.setdefaulttimeout(old)
    except Exception as e:
        print(f"[WARN] feedparser ({feed_url[:50]}): {e}")
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


def _score_article(candidate, now, is_peak=False):
    score     = 0
    age_hours = (now - candidate["pub_date"]).total_seconds() / 3600

    if age_hours <= 3:
        score += SCORE_RECENCY_MAX
    elif age_hours <= ARTICLE_AGE_HOURS:
        ratio  = 1 - (age_hours - 3) / (ARTICLE_AGE_HOURS - 3)
        score += int(SCORE_RECENCY_MAX * ratio)

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
            score   += SCORE_DESC_KEYWORD
            matched += 1

    if _extract_rss_image(candidate["entry"]):
        score += SCORE_HAS_IMAGE

    if len(candidate["description"]) > 200:
        score += SCORE_DESC_LENGTH

    if is_peak:
        score += PEAK_HOUR_BONUS

    return min(score, 100)


def _detect_category(title, description):
    combined = (title + " " + description).lower()
    for category, keywords in CONTENT_CATEGORIES.items():
        for kw in keywords:
            if kw in combined:
                return category
    return "general"


def _extract_hashtags(title, description):
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


# ═══════════════════════════════════════════════════════════
# SECTION 4 — DUPLICATE DETECTION
# ═══════════════════════════════════════════════════════════

def _make_content_hash(title):
    tokens     = _normalize_tokens(title)
    normalized = " ".join(sorted(tokens))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _make_title_hash(title, feed_url):
    raw = (title.lower().strip() + feed_url[:50]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _make_domain_hash(domain):
    return hashlib.sha256(
        domain.encode("utf-8")
    ).hexdigest()[:DB_DOMAIN_HASH_MAX]


def _normalize_tokens(title):
    title  = title.lower()
    title  = re.sub(r"[^a-z0-9\s]", " ", title)
    tokens = title.split()
    return frozenset(
        t for t in tokens
        if t not in TITLE_STOP_WORDS and len(t) >= 2
    )


def _jaccard(tokens_a, tokens_b):
    if not tokens_a or not tokens_b:
        return 0.0
    inter = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return inter / union if union else 0.0


def _fuzzy_duplicate(title, recent_titles):
    if not recent_titles:
        return False, None, 0.0
    incoming = _normalize_tokens(title)
    if not incoming:
        return False, None, 0.0
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
):
    """
    Final check immediately before DB write.
    Conservative: DB error → treat as duplicate → abort.
    Returns (is_duplicate: bool, reason: str).
    """
    for field, value in [
        ("link",         link[:DB_LINK_MAX]),
        ("content_hash", content_hash),
        ("title_hash",   title_hash),
    ]:
        result = _query_field(
            databases, database_id, collection_id,
            field, value, sdk_mode,
        )
        if result is True:
            return True, f"found_{field}"
        if result is None:
            return True, f"db_error_{field}"
    return False, ""


def _query_field(databases, database_id, collection_id,
                 field, value, sdk_mode):
    """
    Returns:
      True  → record found
      False → not found
      None  → DB error
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


def _load_recent_titles(databases, database_id, collection_id,
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
                (d.get("title", ""), _normalize_tokens(d.get("title", "")))
                for d in docs if d.get("title")
            ]
        except Exception as e:
            print(f"[WARN] _load_recent_titles: {e}")
            return []


def _load_recent_domain_hashes(databases, database_id, collection_id, sdk_mode):
    """
    Load domain hashes posted within DOMAIN_DEDUP_HOURS.
    Used for L4b informational logging only — does not block candidates.
    """
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


def _get_domain(url):
    try:
        parts = urlparse(url).netloc.replace("www.", "").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else url[:30]
    except Exception:
        return url[:30]


# ═══════════════════════════════════════════════════════════
# SECTION 5 — SCRAPING
# ═══════════════════════════════════════════════════════════

def _scrape_text(url):
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
            or soup.find("div", {"class": re.compile(r"article[-_]?body",  re.I)})
            or soup.find("div", {"class": re.compile(r"post[-_]?content",   re.I)})
            or soup.find("div", {"class": re.compile(r"entry[-_]?content",  re.I)})
            or soup.find("div", {"class": re.compile(r"story[-_]?body",     re.I)})
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


def _scrape_images(url, rss_entry):
    images = []
    seen   = set()

    def _add(img_url):
        if not img_url:
            return
        img_url = img_url.strip()
        if not img_url.startswith("http"):
            return
        if img_url in seen:
            return
        lower = img_url.lower()
        if any(b in lower for b in IMAGE_BLOCKLIST):
            return
        base     = lower.split("?")[0]
        has_ext  = any(base.endswith(ext) for ext in IMAGE_EXTENSIONS)
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
                    first = srcset.split(",")[0].strip().split(" ")[0]
                    _add(first)
                if len(images) >= MAX_IMAGES:
                    break

    except Exception as e:
        print(f"[WARN] Image scrape: {e}")

    if len(images) < MAX_IMAGES:
        rss_img = _extract_rss_image(rss_entry)
        if rss_img:
            _add(rss_img)

    print(f"[INFO] Images collected: {len(images)}")
    return images[:MAX_IMAGES]


def _extract_rss_image(entry):
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


# ═══════════════════════════════════════════════════════════
# SECTION 6 — SUMMARIZATION & TRANSLATION
# ═══════════════════════════════════════════════════════════

def _extractive_summarize(text, sentence_count=8):
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


def _translate_mymemory(text, source="en", target="fa"):
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
                print("[WARN] MyMemory quota reached.")
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
            print(f"[WARN] MyMemory chunk {i+1}: {e}")
            translated.append(chunk)
    return " ".join(translated).strip() if translated else None


def _split_chunks(text, max_chars):
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


def _translate_article(title, body):
    print(f"[INFO] Translating title ({len(title)} chars)...")
    title_fa = _translate_mymemory(title)
    time.sleep(1)
    print(f"[INFO] Translating body ({len(body)} chars)...")
    body_fa = _translate_mymemory(body)
    return title_fa or title, body_fa or body


# ═══════════════════════════════════════════════════════════
# SECTION 7 — CAPTION BUILDER
# ═══════════════════════════════════════════════════════════

def _build_caption(title_fa, body_fa, hashtags, category):
    def _esc(t):
        return (
            t.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    category_emoji = {
        "runway":         "👗",
        "brand":          "🏷️",
        "business":       "📊",
        "beauty":         "💄",
        "sustainability": "♻️",
        "celebrity":      "⭐",
        "trend":          "🔥",
        "general":        "🌐",
    }
    emoji      = category_emoji.get(category, "🌐")
    safe_title = _esc(title_fa.strip())
    safe_body  = _esc(body_fa.strip())
    hash_line  = " ".join(hashtags) if hashtags else ""

    parts = [
        f"<b>{safe_title}</b>",
        "@irfashionnews",
        safe_body,
    ]
    if hash_line:
        parts.append(hash_line)
    parts.append(f"{emoji} <i>کانال مد و فشن ایرانی</i>")

    caption = "\n\n".join(parts)

    if len(caption) > CAPTION_MAX:
        overflow  = len(caption) - CAPTION_MAX
        safe_body = safe_body[:max(0, len(safe_body) - overflow - 5)] + "…"
        parts[2]  = safe_body
        caption   = "\n\n".join(parts)

    return caption


# ═══════════════════════════════════════════════════════════
# SECTION 8 — TELEGRAM POSTING
#
# MANDATORY ORDER (project spec):
#   Step 1: send_media_group(ALL images, NO caption)
#   Step 2: sleep(0.8s)
#   Step 3: send_photo(images[0] + caption)
#
# Caption appears EXACTLY ONCE — on send_photo only.
# Album step is non-fatal if it fails.
# ═══════════════════════════════════════════════════════════

async def _post_to_telegram(bot, chat_id, caption, image_urls):
    caption_image = image_urls[0] if image_urls else None

    # Step 1: Album (no caption)
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
            print(
                f"[INFO] Step 1: Album sent "
                f"({len(media_group)} images, no caption)."
            )
        except Exception as e:
            print(f"[WARN] Step 1 album failed (non-fatal): {str(e)[:120]}")
    else:
        print(
            f"[INFO] Step 1: Skipped "
            f"({'1 image' if image_urls else 'no images'} — album needs ≥2)."
        )

    # Delay between album and caption post
    if len(image_urls) >= 2:
        await asyncio.sleep(0.8)

    # Step 2: Captioned photo (primary post)
    try:
        if caption_image:
            await bot.send_photo(
                chat_id=chat_id,
                photo=caption_image,
                caption=caption,
                parse_mode="HTML",
                disable_notification=True,
            )
            print("[INFO] Step 2: Caption photo sent.")
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                disable_notification=True,
            )
            print("[INFO] Step 2: Text-only caption sent.")
        return True

    except Exception as e:
        print(f"[WARN] Step 2 photo failed: {str(e)[:120]}")
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                disable_notification=True,
            )
            print("[INFO] Step 2 fallback: text-only sent.")
            return True
        except Exception as e2:
            print(f"[ERROR] Step 2 all methods failed: {str(e2)[:120]}")
            return False


# ═══════════════════════════════════════════════════════════
# SECTION 9 — DATABASE WRITE
# ═══════════════════════════════════════════════════════════

def _build_db_payload(
    link, title, feed_url, pub_date, source_type,
    title_hash, content_hash, category,
    trend_score, post_hour, domain_hash,
):
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
):
    """
    Returns True on success, False on failure.
    Caller aborts posting on False.
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
            print(f"[ERROR] DB write: {e.message}")
            return False
        except Exception as e:
            print(f"[ERROR] DB write: {e}")
            return False


# ═══════════════════════════════════════════════════════════
# LOCAL TEST
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    asyncio.run(main())
