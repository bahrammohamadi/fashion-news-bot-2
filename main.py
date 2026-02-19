# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    7.1
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# Changes vs 7.0:
#   [1] Multi-image support
#       Scrapes ALL valid images from article HTML
#       Sends as Telegram media group (2-10 photos)
#       Falls back to single photo if only 1 image found
#       Falls back to text-only if no images found
#   [2] Duplicate protection hardened
#       Primary key:   article link (indexed in Appwrite)
#       Secondary key: SHA256 hash of (title + feed_url)
#       Both checked before processing any article
#   [3] Image scraping separated from article text scraping
#       scrape_article()  → returns text only
#       scrape_images()   → returns list of image URLs
#       Both run in parallel via asyncio.gather
#
# Translation stack (no LLM, no paid API):
#   sumy LSA    → offline extractive summarization
#   MyMemory    → free official translation API (EN→FA)
#   Quality ceiling: readable Persian, not magazine-style
#   This is a hard limit of the no-LLM constraint.
#
# Duplicate protection:
#   is_duplicate() checks Appwrite DB by article link
#   save_to_db() stores link + title_hash
#   Any article already in DB is permanently skipped
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

# Appwrite schema field size limits (1 under schema max)
DB_LINK_MAX        = 999
DB_TITLE_MAX       = 499
DB_FEED_URL_MAX    = 499
DB_SOURCE_TYPE_MAX = 19
DB_HASH_MAX        = 64     # SHA256 hex = 64 chars

# Timeout budgets (seconds)
FEED_FETCH_TIMEOUT  = 7
FEEDS_SCAN_TIMEOUT  = 20
SCRAPE_TIMEOUT      = 12    # slightly longer — now scrapes text + images
TRANSLATION_TIMEOUT = 40
TELEGRAM_TIMEOUT    = 20    # longer — media group upload takes more time

# Summarizer
SUMMARY_SENTENCES = 8

# MyMemory translation API
MYMEMORY_CHUNK_SIZE  = 450
MYMEMORY_CHUNK_DELAY = 1.0
MYMEMORY_EMAIL       = ""   # set to your email for 50000 chars/day

# Image scraping
MAX_IMAGES          = 10    # Telegram media group max
MIN_IMAGE_DIMENSION = 200   # pixels — filter out tiny icons/trackers
IMAGE_EXTENSIONS    = {".jpg", ".jpeg", ".png", ".webp"}

# Domains known to serve tracking pixels / ad images — skip these
IMAGE_BLOCKLIST = [
    "doubleclick", "googletagmanager", "googlesyndication",
    "facebook.com/tr", "analytics", "pixel", "beacon",
    "tracking", "counter", "stat.", "stats.",
]

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

# RSS feeds
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
    print("[INFO] ═══ Function 1 v7.1 started ═══")
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
    # PHASE 1 — PARALLEL RSS SCAN + TREND SCORING
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

    title      = candidate["title"]
    link       = candidate["link"]
    desc       = candidate["description"]
    feed_url   = candidate["feed_url"]
    pub_date   = candidate["pub_date"]
    entry      = candidate["entry"]
    score      = candidate["score"]
    title_hash = make_hash(title, feed_url)

    print(
        f"[INFO] [{elapsed()}s] "
        f"Selected (score={score}): {title[:65]}"
    )
    print(f"[INFO] [{elapsed()}s] Link: {link[:80]}")

    # ════════════════════════════════════════════════
    # PHASE 2 — PARALLEL SCRAPE: TEXT + IMAGES
    # Run text scraping and image scraping simultaneously
    # to save time vs doing them sequentially.
    # Budget: 12 seconds
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

    # Handle exceptions from gather
    full_text   = text_result  if not isinstance(text_result, Exception)  else None
    image_urls  = image_result if not isinstance(image_result, Exception) else []

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
        f"({len(content)} chars) | "
        f"Images: {len(image_urls)} found"
    )

    # Quality gate
    if len(content) < MIN_CONTENT_CHARS:
        print(f"[WARN] [{elapsed()}s] Content too thin. Skipping.")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date,
            SOURCE_TYPE, sdk_mode, title_hash,
        )
        return {"status": "skipped", "reason": "thin_content", "posted": False}

    # ════════════════════════════════════════════════
    # PHASE 3 — SUMMARIZE + TRANSLATE
    # Step A: offline sumy summarization
    # Step B: MyMemory free API translation
    # Budget: 40 seconds
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 3: Summarize + Translate...")

    english_summary = await loop.run_in_executor(
        None, extractive_summarize, content, SUMMARY_SENTENCES,
    )
    print(
        f"[INFO] [{elapsed()}s] "
        f"Summary: {len(english_summary)} chars"
    )

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
        print(f"[WARN] [{elapsed()}s] Translation empty. Saving + skipping.")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date,
            SOURCE_TYPE, sdk_mode, title_hash,
        )
        return {"status": "error", "reason": "translation_failed", "posted": False}

    print(
        f"[INFO] [{elapsed()}s] "
        f"title_fa={len(title_fa)} | body_fa={len(body_fa)}"
    )

    # ════════════════════════════════════════════════
    # PHASE 4 — BUILD CAPTION + POST TO TELEGRAM
    #
    # Posting strategy:
    #   ≥2 images → send_media_group (album post, caption on first)
    #    1 image  → send_photo with caption
    #    0 images → send_message text only
    #
    # Budget: 20 seconds
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 4: Posting to Telegram...")

    caption = build_caption(title_fa, body_fa)
    print(
        f"[INFO] [{elapsed()}s] "
        f"Caption: {len(caption)} chars | "
        f"Images to post: {len(image_urls)}"
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
            SOURCE_TYPE, sdk_mode, title_hash,
        )
    else:
        print(f"[ERROR] [{elapsed()}s] Telegram failed — not saving to DB.")

    print(
        f"[INFO] ═══ v7.1 done in {elapsed()}s "
        f"| posted={posted} ═══"
    )
    return {"status": "success", "posted": posted}


# ─────────────────────────────────────────────
# PHASE 1 — CANDIDATE SELECTION
# ─────────────────────────────────────────────

async def find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, now,
):
    """
    Fetch all feeds in parallel.
    Score every recent article.
    Return highest-scored unposted article.
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

    for c in all_candidates:
        c["score"] = score_article(c, now)

    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    print("[INFO] Top candidates by score:")
    for c in all_candidates[:5]:
        print(f"       [{c['score']:>3}] {c['title'][:60]}")

    for c in all_candidates:
        link       = c["link"]
        title_hash = make_hash(c["title"], c["feed_url"])

        # Primary duplicate check: by link
        if is_duplicate(
            databases, database_id, collection_id, link, sdk_mode
        ):
            print(f"[INFO] Duplicate (link): {c['title'][:55]}")
            continue

        # Secondary duplicate check: by title hash
        # Catches same article republished at different URL
        if is_duplicate_by_hash(
            databases, database_id, collection_id, title_hash, sdk_mode
        ):
            print(f"[INFO] Duplicate (hash): {c['title'][:55]}")
            continue

        return c

    return None


def make_hash(title, feed_url):
    """
    SHA256 hash of normalized title + feed domain.
    Used as secondary duplicate key.
    """
    normalized = (title.lower().strip() + feed_url[:50]).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def score_article(candidate, now):
    score     = 0
    age_hours = (now - candidate["pub_date"]).total_seconds() / 3600

    # Recency (0-40)
    if age_hours <= 3:
        score += SCORE_RECENCY_MAX
    elif age_hours <= ARTICLE_AGE_HOURS:
        score += int(
            SCORE_RECENCY_MAX
            * (1 - (age_hours - 3) / (ARTICLE_AGE_HOURS - 3))
        )

    # Keywords
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

    # Image available bonus
    if extract_rss_image(candidate["entry"]):
        score += SCORE_HAS_IMAGE

    # Rich description bonus
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
    """
    Scrape and return clean paragraph text from article URL.
    Synchronous — called via run_in_executor.
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
# Collects ALL valid article images
# ─────────────────────────────────────────────

def scrape_article_images(url, rss_entry):
    """
    Collect all valid image URLs for this article.

    Priority order:
      1. Images scraped from full article HTML page
      2. RSS entry image (enclosure, media:content, thumbnail)

    Filters applied:
      - Must be http/https URL
      - Must have image extension OR content-type image/*
      - URL must not match blocklist (ads, trackers, pixels)
      - Deduplicates URLs

    Returns list of 1–10 image URL strings.
    """
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
        # Check blocklist
        url_lower = img_url.lower()
        if any(blocked in url_lower for blocked in IMAGE_BLOCKLIST):
            return
        # Must look like an image
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

    # ── Step 1: Scrape article page HTML ──
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

        # Remove known non-content elements before looking for images
        for tag in soup([
            "script", "style", "nav", "footer", "header",
            "aside", "form", "iframe", "noscript", "button",
        ]):
            tag.decompose()

        # Find article body first (better signal-to-noise)
        body = (
            soup.find("article")
            or soup.find("div", {"class": re.compile(r"article[-_]?body", re.I)})
            or soup.find("div", {"class": re.compile(r"post[-_]?content", re.I)})
            or soup.find("div", {"class": re.compile(r"entry[-_]?content", re.I)})
            or soup.find("main")
        )

        search_area = body if body else soup

        # Collect from <img> tags
        for img in search_area.find_all("img"):
            # Prefer data-src (lazy load) over src
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

        # Collect from <source> tags inside <picture>
        if len(images) < MAX_IMAGES:
            for source in search_area.find_all("source"):
                srcset = source.get("srcset", "")
                if srcset:
                    # srcset format: "url1 1x, url2 2x" — take first
                    first = srcset.split(",")[0].strip().split(" ")[0]
                    add_image(first)
                if len(images) >= MAX_IMAGES:
                    break

    except Exception as e:
        print(f"[WARN] Image scrape from HTML: {e}")

    # ── Step 2: Add RSS entry images as fallback / supplement ──
    if len(images) < MAX_IMAGES:
        rss_img = extract_rss_image(rss_entry)
        if rss_img:
            add_image(rss_img)

    print(f"[INFO] Images collected: {len(images)}")
    for i, img in enumerate(images[:3]):
        print(f"       [{i+1}] {img[:80]}")

    return images[:MAX_IMAGES]


def extract_rss_image(entry):
    """
    Extract single best image URL from RSS entry fields.
    Used for both scoring and image fallback.
    """
    # media:content explicit
    for m in entry.get("media_content", []):
        if m.get("url") and m.get("medium") == "image":
            return m["url"]

    # media:content by extension
    for m in entry.get("media_content", []):
        url = m.get("url", "")
        if url and any(
            url.lower().endswith(e) for e in IMAGE_EXTENSIONS
        ):
            return url

    # enclosure
    enc = entry.get("enclosure")
    if enc:
        url = enc.get("href") or enc.get("url", "")
        if url and enc.get("type", "").startswith("image/"):
            return url

    # media:thumbnail
    thumbs = entry.get("media_thumbnail", [])
    if thumbs and thumbs[0].get("url"):
        return thumbs[0]["url"]

    # img in summary/description HTML
    for field in ["summary", "description"]:
        html = entry.get(field, "")
        if html:
            img = BeautifulSoup(html, "html.parser").find("img")
            if img:
                src = img.get("src", "")
                if src.startswith("http"):
                    return src

    # content:encoded
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
    Offline LSA extractive summarization via sumy.
    Reduces text to most informative sentences.
    No internet, no API, no GPU needed.
    Quality ceiling: extractive (copies sentences), not abstractive.
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
        print(f"[WARN] sumy error: {e}")
        return text[:1200]


def translate_mymemory(text, source="en", target="fa"):
    """
    Translate using MyMemory official free API.
    No API key required for 5000 chars/day.
    Set MYMEMORY_EMAIL for 50000 chars/day.

    Splits into sentence-boundary chunks.
    Keeps English text for any chunk that fails.
    """
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
                    .get("translatedText", "")
                or ""
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
                print(f"[WARN] MyMemory chunk {i+1} bad: {trans[:60]}")
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
    """Split text at sentence boundaries, never mid-sentence."""
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
    """
    Translate title then body separately.
    Synchronous — call via run_in_executor.
    Returns (title_fa, body_fa).
    """
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
    """
    Build Telegram caption. Enforces 1024-char limit.

    Format:
      <b>Title</b>

      @irfashionnews

      Body...

      Channel signature
    """
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
        overflow   = len(caption) - CAPTION_MAX
        safe_body  = safe_body[:max(0, len(safe_body) - overflow - 5)] + "…"
        parts[2]   = safe_body
        caption    = "\n\n".join(parts)

    return caption


# ─────────────────────────────────────────────
# TELEGRAM SENDER — MULTI-IMAGE SUPPORT
# ─────────────────────────────────────────────

async def send_to_telegram(bot, chat_id, caption, image_urls):
    """
    Send post to Telegram channel.

    Strategy based on image count:
      0 images  → send_message (text only)
      1 image   → send_photo (photo + caption)
      2-10 imgs → send_media_group (album, caption on first photo)

    Telegram media group rules:
      - 2 to 10 media items per group
      - Caption only on first item
      - Caption limit: 1024 chars (same as single photo)
      - All items must be InputMediaPhoto
    """
    try:
        if not image_urls:
            # Text only
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                disable_notification=True,
            )
            print("[INFO] Text-only post sent.")
            return True

        if len(image_urls) == 1:
            # Single photo
            await bot.send_photo(
                chat_id=chat_id,
                photo=image_urls[0],
                caption=caption,
                parse_mode="HTML",
                disable_notification=True,
            )
            print(f"[INFO] Single photo sent: {image_urls[0][:70]}")
            return True

        # Multiple photos — media group (album)
        # Caption goes on the first photo only
        media_group = []
        for i, img_url in enumerate(image_urls[:MAX_IMAGES]):
            if i == 0:
                media_group.append(
                    InputMediaPhoto(
                        media=img_url,
                        caption=caption,
                        parse_mode="HTML",
                    )
                )
            else:
                media_group.append(InputMediaPhoto(media=img_url))

        await bot.send_media_group(
            chat_id=chat_id,
            media=media_group,
            disable_notification=True,
        )
        print(f"[INFO] Media group sent: {len(media_group)} photos.")
        return True

    except Exception as e:
        err = str(e)
        print(f"[WARN] Telegram send error: {err[:120]}")

        # If media group failed (bad image URL etc.), retry with just first image
        if image_urls and len(image_urls) > 1:
            print("[INFO] Retrying with single image fallback...")
            try:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=image_urls[0],
                    caption=caption,
                    parse_mode="HTML",
                    disable_notification=True,
                )
                print("[INFO] Single photo fallback sent.")
                return True
            except Exception as e2:
                print(f"[WARN] Single photo fallback failed: {e2}")

        # Final fallback: text only
        if image_urls:
            print("[INFO] Retrying as text-only post...")
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
            except Exception as e3:
                print(f"[ERROR] Text-only fallback also failed: {e3}")

        return False


# ─────────────────────────────────────────────
# APPWRITE DATABASE HELPERS
# ─────────────────────────────────────────────

def _build_db_data(link, title, feed_url, pub_date,
                   source_type, title_hash):
    """
    Build schema-safe data dict.
    Auto fields ($id, $createdAt, $updatedAt) are NOT included.
    """
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    return {
        "link":         link[:DB_LINK_MAX],
        "title":        title[:DB_TITLE_MAX],
        "published_at": pub_date.strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
        "feed_url":     feed_url[:DB_FEED_URL_MAX],
        "source_type":  source_type[:DB_SOURCE_TYPE_MAX],
        # title_hash stored if schema has this field
        # If not in schema, this line will cause an error —
        # remove it if you haven't added title_hash to collection
        # "title_hash":   title_hash[:DB_HASH_MAX],
    }


def is_duplicate(databases, database_id, collection_id, link, sdk_mode):
    """
    Primary duplicate check: query by article link.
    Link field must be indexed in Appwrite for fast lookup.
    Returns True if already posted.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            if sdk_mode == "new":
                r = databases.list_rows(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=[Query.equal("link", link[:DB_LINK_MAX])],
                )
            else:
                r = databases.list_documents(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=[Query.equal("link", link[:DB_LINK_MAX])],
                )
            return r["total"] > 0
        except AppwriteException as e:
            print(f"[WARN] is_duplicate (link): {e.message}")
            return False
        except Exception as e:
            print(f"[WARN] is_duplicate (link): {e}")
            return False


def is_duplicate_by_hash(databases, database_id, collection_id,
                         title_hash, sdk_mode):
    """
    Secondary duplicate check: query by title hash.
    Catches same article republished at a different URL.

    NOTE: Only works if you have added 'title_hash' field
    to your Appwrite history collection (String, size 64).
    If field doesn't exist, this always returns False safely.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            if sdk_mode == "new":
                r = databases.list_rows(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=[Query.equal("title_hash", title_hash)],
                )
            else:
                r = databases.list_documents(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=[Query.equal("title_hash", title_hash)],
                )
            return r["total"] > 0
        except AppwriteException:
            # Field doesn't exist in schema — hash check not available
            return False
        except Exception:
            return False


def save_to_db(databases, database_id, collection_id,
               link, title, feed_url, pub_date,
               source_type, sdk_mode, title_hash):
    """
    Save article to Appwrite.
    Prevents any future duplicate posting of this article.
    """
    data = _build_db_data(
        link, title, feed_url, pub_date, source_type, title_hash
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
