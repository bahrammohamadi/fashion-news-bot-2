# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    7.0 — NO EXTERNAL LLM
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# Translation stack (fully free, no LLM API):
#
#   Step 1: Scrape full article (BeautifulSoup)
#   Step 2: Extract key sentences (sumy LSA — 100% offline)
#   Step 3: Translate via MyMemory free API
#              - Official API, no key required
#              - Free tier: 5000 chars/day
#              - With email param: 50000 chars/day
#              - Endpoint: api.mymemory.translated.net
#   Step 4: Format as clean Persian caption
#   Step 5: Post to Telegram
#
# What was REMOVED:
#   - openai library
#   - OpenRouter calls
#   - deepseek / mistral / qwen models
#   - All external LLM dependencies
#
# What was ADDED:
#   - sumy (offline extractive summarizer)
#   - MyMemory translation (free official API)
#   - nltk punkt tokenizer (for sumy)
#
# Quality note:
#   Output will be clean, readable Persian translation.
#   It will NOT be magazine-style rewriting.
#   That requires an LLM — no free local alternative exists.
#
# Schedule: Every 45 minutes
# ============================================================

import os
import re
import time
import asyncio
import warnings
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from telegram import Bot, LinkPreviewOptions
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query

# sumy — offline extractive summarizer
# Selects most important sentences from English text
# before sending to translation API
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

# Appwrite schema field limits
DB_LINK_MAX        = 999
DB_TITLE_MAX       = 499
DB_FEED_URL_MAX    = 499
DB_SOURCE_TYPE_MAX = 19

# Timeout budgets (seconds)
FEED_FETCH_TIMEOUT    = 7
FEEDS_SCAN_TIMEOUT    = 20
SCRAPE_TIMEOUT        = 10
TRANSLATION_TIMEOUT   = 40   # MyMemory can be slow for multiple chunks
TELEGRAM_TIMEOUT      = 10

# Summarizer settings
SUMMARY_SENTENCES = 8        # extract 8 key sentences before translating

# MyMemory settings
MYMEMORY_CHUNK_SIZE  = 450   # chars per API request (API limit ~500)
MYMEMORY_CHUNK_DELAY = 1.0   # seconds between chunk requests
# Optional: add your email for 50000 chars/day instead of 5000
# Leave empty string for anonymous 5000 chars/day
MYMEMORY_EMAIL = "lasvaram@gmail.com"

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


# ─────────────────────────────────────────────
# RSS FEEDS
# ─────────────────────────────────────────────

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
    print("[INFO] ═══ Function 1 v7.0 (No-LLM) started ═══")
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

    # ── Clients ──
    bot = Bot(token=token)

    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)
    sdk_mode  = "new" if hasattr(databases, "list_rows") else "legacy"

    print(f"[INFO] SDK mode: {sdk_mode}")
    print(f"[INFO] Translation: MyMemory API (free, no key)")

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)

    # ════════════════════════════════════════════════
    # PHASE 1 — PARALLEL RSS SCAN + TREND SCORING
    # Budget: 20 seconds
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

    title    = candidate["title"]
    link     = candidate["link"]
    desc     = candidate["description"]
    feed_url = candidate["feed_url"]
    pub_date = candidate["pub_date"]
    entry    = candidate["entry"]
    score    = candidate["score"]

    print(
        f"[INFO] [{elapsed()}s] "
        f"Selected (score={score}): {title[:65]}"
    )

    # ════════════════════════════════════════════════
    # PHASE 2 — SCRAPE FULL ARTICLE
    # Budget: 10 seconds
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 2: Scraping...")

    try:
        full_text = await asyncio.wait_for(
            loop.run_in_executor(None, scrape_article, link),
            timeout=SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Scrape timed out.")
        full_text = None
    except Exception as e:
        print(f"[WARN] [{elapsed()}s] Scrape error: {e}")
        full_text = None

    content = (
        full_text
        if full_text and len(full_text) > len(desc)
        else desc[:MAX_RSS_CHARS]
    )

    print(
        f"[INFO] [{elapsed()}s] "
        f"{'scraped' if full_text else 'rss-summary'} "
        f"({len(content)} chars)"
    )

    if len(content) < MIN_CONTENT_CHARS:
        print(f"[WARN] [{elapsed()}s] Content too thin. Skipping.")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
        return {"status": "skipped", "reason": "thin_content", "posted": False}

    # ════════════════════════════════════════════════
    # PHASE 3 — OFFLINE SUMMARIZE + FREE TRANSLATE
    #
    # Step A: Extract key sentences with sumy (offline, instant)
    # Step B: Translate title separately (MyMemory API)
    # Step C: Translate summary body (MyMemory API, chunked)
    #
    # Budget: 40 seconds
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 3: Summarize + Translate...")

    # Step A — Offline extractive summarization
    english_summary = await loop.run_in_executor(
        None,
        extractive_summarize,
        content,
        SUMMARY_SENTENCES,
    )
    print(
        f"[INFO] [{elapsed()}s] Summary: {len(english_summary)} chars "
        f"from {len(content)} chars"
    )

    # Step B+C — Translate via MyMemory (free official API)
    try:
        title_fa, body_fa = await asyncio.wait_for(
            loop.run_in_executor(
                None,
                translate_article,
                title,
                english_summary,
            ),
            timeout=TRANSLATION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Translation timed out.")
        title_fa = title     # fallback: keep English title
        body_fa  = english_summary  # fallback: keep English body

    if not title_fa or not body_fa:
        print(f"[WARN] [{elapsed()}s] Translation returned empty.")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
        return {
            "status": "error",
            "reason": "translation_failed",
            "posted": False,
        }

    print(
        f"[INFO] [{elapsed()}s] Translation done: "
        f"title={len(title_fa)} chars, body={len(body_fa)} chars"
    )

    # ════════════════════════════════════════════════
    # PHASE 4 — CAPTION + TELEGRAM POST
    # Budget: 10 seconds
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 4: Posting...")

    caption   = build_caption(title_fa, body_fa)
    image_url = extract_image(entry)

    print(
        f"[INFO] [{elapsed()}s] "
        f"Caption: {len(caption)} chars | "
        f"Image: {'yes' if image_url else 'no'}"
    )

    try:
        posted = await asyncio.wait_for(
            send_to_telegram(bot, chat_id, caption, image_url),
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
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
    else:
        print(f"[ERROR] [{elapsed()}s] Telegram failed.")

    print(
        f"[INFO] ═══ v7.0 done in {elapsed()}s | posted={posted} ═══"
    )
    return {"status": "success", "posted": posted}


# ─────────────────────────────────────────────
# PHASE 1 — CANDIDATE SELECTION
# ─────────────────────────────────────────────

async def find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, now,
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
        c["score"] = score_article(c, now)

    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    print("[INFO] Top candidates:")
    for c in all_candidates[:5]:
        print(f"       [{c['score']:>3}] {c['title'][:60]}")

    for c in all_candidates:
        if not is_duplicate(
            databases, database_id, collection_id, c["link"], sdk_mode
        ):
            return c
        print(f"[INFO] Duplicate: {c['title'][:55]}")

    return None


def score_article(candidate, now):
    score     = 0
    age_hours = (now - candidate["pub_date"]).total_seconds() / 3600

    # Recency (0–40)
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

    # Image bonus
    if extract_image(candidate["entry"]):
        score += SCORE_HAS_IMAGE

    # Description length bonus
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
# PHASE 2 — SCRAPER
# ─────────────────────────────────────────────

def scrape_article(url):
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
            timeout=SCRAPE_TIMEOUT - 2,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        for tag in soup([
            "script", "style", "nav", "footer", "header",
            "aside", "form", "iframe", "noscript", "figure",
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
        print(f"[WARN] Scrape timeout: {url[:60]}")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"[WARN] HTTP {e.response.status_code}: {url[:60]}")
        return None
    except Exception as e:
        print(f"[WARN] Scrape: {e}")
        return None


# ─────────────────────────────────────────────
# PHASE 3A — OFFLINE EXTRACTIVE SUMMARIZER
# Uses sumy LSA algorithm — no internet, no API
# Reduces text to most important sentences
# before sending to translation API
# ─────────────────────────────────────────────

def extractive_summarize(text, sentence_count=8):
    """
    Extract the N most important sentences from English text.
    100% offline — uses sumy LSA algorithm.
    Returns joined sentence string.
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


# ─────────────────────────────────────────────
# PHASE 3B — MYMEMORY FREE TRANSLATION API
#
# Official free API by Translated.net
# No API key required for 5000 chars/day
# Add MYMEMORY_EMAIL for 50000 chars/day
# Docs: https://mymemory.translated.net/doc/spec.php
# ─────────────────────────────────────────────

def translate_mymemory(text, source="en", target="fa"):
    """
    Translate text using MyMemory free API.
    Splits into chunks to respect per-request limits.
    Returns translated string or None on failure.
    """
    if not text or not text.strip():
        return ""

    chunks     = split_into_chunks(text, MYMEMORY_CHUNK_SIZE)
    translated = []

    for i, chunk in enumerate(chunks):
        if not chunk.strip():
            continue
        try:
            params = {
                "q":        chunk,
                "langpair": f"{source}|{target}",
            }
            if MYMEMORY_EMAIL:
                params["de"] = MYMEMORY_EMAIL

            resp = requests.get(
                "https://api.mymemory.translated.net/get",
                params=params,
                timeout=12,
            )
            resp.raise_for_status()
            data = resp.json()

            result    = data.get("responseData", {})
            trans     = (result.get("translatedText") or "").strip()
            quota_msg = data.get("quotaFinished", False)

            if quota_msg:
                print("[WARN] MyMemory: daily quota reached.")
                translated.append(chunk)   # keep English for this chunk
                continue

            if (
                trans
                and "MYMEMORY WARNING" not in trans
                and "YOU USED ALL AVAILABLE" not in trans
                and len(trans) > 2
            ):
                translated.append(trans)
            else:
                print(f"[WARN] MyMemory chunk {i+1}: bad response — {trans[:60]}")
                translated.append(chunk)  # keep English

            # Rate limit protection
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
    """
    Split text at sentence boundaries to respect API char limits.
    Never splits mid-sentence.
    """
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks    = []
    current   = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            # Single sentence too long — split on comma
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
    Translate title and body separately.
    Returns (title_fa, body_fa) tuple.
    Runs synchronously — call via run_in_executor.
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
    Build Telegram caption in exact format:

    <b>Persian Title</b>

    @irfashionnews

    Persian body text...

    Channel signature

    Enforces 1024-char Telegram hard limit.
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

    # Trim body if over Telegram limit
    if len(caption) > CAPTION_MAX:
        overflow   = len(caption) - CAPTION_MAX
        safe_body  = safe_body[:max(0, len(safe_body) - overflow - 5)] + "…"
        parts[2]   = safe_body
        caption    = "\n\n".join(parts)

    return caption


# ─────────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────────

async def send_to_telegram(bot, chat_id, caption, image_url):
    try:
        if image_url:
            await bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=caption,
                parse_mode="HTML",
                disable_notification=True,
            )
            print(f"[INFO] Photo sent: {image_url[:80]}")
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                disable_notification=True,
            )
            print("[INFO] Text post sent.")
        return True
    except Exception as e:
        print(f"[ERROR] Telegram: {e}")
        return False


# ─────────────────────────────────────────────
# IMAGE EXTRACTOR
# ─────────────────────────────────────────────

def extract_image(entry):
    for m in entry.get("media_content", []):
        if m.get("url") and m.get("medium") == "image":
            return m["url"]
    for m in entry.get("media_content", []):
        url = m.get("url", "")
        if url and any(
            url.lower().endswith(e)
            for e in [".jpg", ".jpeg", ".png", ".webp"]
        ):
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
# APPWRITE HELPERS
# ─────────────────────────────────────────────

def _build_db_data(link, title, feed_url, pub_date, source_type):
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)
    return {
        "link":         link[:DB_LINK_MAX],
        "title":        title[:DB_TITLE_MAX],
        "published_at": pub_date.strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
        "feed_url":     feed_url[:DB_FEED_URL_MAX],
        "source_type":  source_type[:DB_SOURCE_TYPE_MAX],
    }


def is_duplicate(databases, database_id, collection_id, link, sdk_mode):
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
            print(f"[WARN] is_duplicate: {e.message}")
            return False
        except Exception as e:
            print(f"[WARN] is_duplicate: {e}")
            return False


def save_to_db(databases, database_id, collection_id,
               link, title, feed_url, pub_date, source_type, sdk_mode):
    data = _build_db_data(link, title, feed_url, pub_date, source_type)
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
