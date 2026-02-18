# ============================================================
# Function 1: International Fashion Poster
# Project: @irfashionnews — FashionBotProject
# Version: 5.0 — OPENROUTER + DEEPSEEK ONLY (Feb 2026)
#
# LLM: deepseek/deepseek-chat:free via OpenRouter
# Flow: RSS scan → scrape → DeepSeek → Telegram → Appwrite
#
# Database schema (history collection):
#   link(1000), title(500), published_at(datetime),
#   feed_url(500), source_type(20)
#   $id, $createdAt, $updatedAt → auto by Appwrite
#
# Schedule: Every 45 minutes
# ============================================================

import os
import re
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
from openai import AsyncOpenAI

# Suppress Appwrite SDK deprecation warnings
warnings.filterwarnings(
    "ignore",
    category=DeprecationWarning,
    module="appwrite",
)


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
COLLECTION_ID     = "history"
SOURCE_TYPE       = "en"
ARTICLE_AGE_HOURS = 24
CAPTION_MAX       = 1020
MAX_SCRAPED_CHARS = 2000
MAX_RSS_CHARS     = 800

# Appwrite schema field size limits
DB_LINK_MAX        = 999   # schema: 1000
DB_TITLE_MAX       = 499   # schema: 500
DB_FEED_URL_MAX    = 499   # schema: 500
DB_SOURCE_TYPE_MAX = 19    # schema: 20

# Timeout budgets (seconds) — total must stay under 60s
FEED_FETCH_TIMEOUT = 6     # per-feed socket timeout
FEEDS_SCAN_TIMEOUT = 18    # total parallel RSS scan budget
SCRAPE_TIMEOUT     = 8     # article scrape budget
LLM_TIMEOUT        = 30    # DeepSeek call budget
TELEGRAM_TIMEOUT   = 8     # Telegram send budget

# ── LLM: DeepSeek via OpenRouter ──
LLM_MODEL = "deepseek/deepseek-chat:free"


# ─────────────────────────────────────────────
# RSS FEEDS — International Fashion
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
    print("[INFO] ═══ Function 1 v5.0 started ═══")
    loop       = asyncio.get_event_loop()
    start_time = loop.time()

    def elapsed():
        return round(loop.time() - start_time, 1)

    # ── Load environment variables ──
    token             = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id           = os.environ.get("TELEGRAM_CHANNEL_ID")
    appwrite_endpoint = os.environ.get(
        "APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1"
    )
    appwrite_project  = os.environ.get("APPWRITE_PROJECT_ID")
    appwrite_key      = os.environ.get("APPWRITE_API_KEY")
    database_id       = os.environ.get("APPWRITE_DATABASE_ID")
    openrouter_key    = os.environ.get("OPENROUTER_API_KEY")

    # ── Validate all required variables ──
    missing = [
        name for name, val in {
            "TELEGRAM_BOT_TOKEN":   token,
            "TELEGRAM_CHANNEL_ID":  chat_id,
            "APPWRITE_PROJECT_ID":  appwrite_project,
            "APPWRITE_API_KEY":     appwrite_key,
            "APPWRITE_DATABASE_ID": database_id,
            "OPENROUTER_API_KEY":   openrouter_key,
        }.items() if not val
    ]
    if missing:
        print(f"[ERROR] Missing env vars: {missing}")
        return {"status": "error", "missing_vars": missing}

    # ── Initialize OpenRouter client ──
    llm = AsyncOpenAI(
        api_key=openrouter_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://t.me/irfashionnews",
            "X-Title":      "IrFashionNews Bot",
        },
    )
    print(f"[INFO] LLM model: {LLM_MODEL}")

    # ── Initialize Telegram bot ──
    bot = Bot(token=token)

    # ── Initialize Appwrite ──
    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases  = Databases(aw_client)
    sdk_mode   = "new" if hasattr(databases, "list_rows") else "legacy"
    print(f"[INFO] Appwrite SDK mode: {sdk_mode}")

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)

    # ════════════════════════════════════════════════
    # PHASE 1 — PARALLEL RSS SCAN
    # Fetch all 20 feeds simultaneously
    # Pick the newest unposted article
    # Budget: 18 seconds
    # ════════════════════════════════════════════════
    print(
        f"[INFO] [{elapsed()}s] Phase 1: "
        f"Scanning {len(RSS_FEEDS)} feeds in parallel..."
    )

    try:
        candidate = await asyncio.wait_for(
            find_candidate_parallel(
                feeds=RSS_FEEDS,
                databases=databases,
                database_id=database_id,
                collection_id=COLLECTION_ID,
                time_threshold=time_threshold,
                sdk_mode=sdk_mode,
            ),
            timeout=FEEDS_SCAN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Feed scan timed out.")
        candidate = None

    print(f"[INFO] [{elapsed()}s] Phase 1 complete.")

    if not candidate:
        print("[INFO] No new unposted articles found. Done.")
        return {"status": "success", "posted": False}

    title    = candidate["title"]
    link     = candidate["link"]
    desc     = candidate["description"]
    feed_url = candidate["feed_url"]
    pub_date = candidate["pub_date"]
    entry    = candidate["entry"]

    print(f"[INFO] [{elapsed()}s] Selected: {title[:70]}")

    # ════════════════════════════════════════════════
    # PHASE 2 — SCRAPE FULL ARTICLE
    # Single article only — run in thread pool
    # Budget: 8 seconds
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 2: Scraping article...")

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

    # Use scraped text if richer than RSS summary
    content = (
        full_text
        if full_text and len(full_text) > len(desc)
        else desc[:MAX_RSS_CHARS]
    )

    print(
        f"[INFO] [{elapsed()}s] Content source: "
        f"{'scraped' if full_text else 'rss-summary'} "
        f"({len(content)} chars)"
    )

    # ════════════════════════════════════════════════
    # PHASE 3 — DEEPSEEK LLM
    # Translate + rewrite in magazine-quality Persian
    # Budget: 30 seconds
    # ════════════════════════════════════════════════
    print(
        f"[INFO] [{elapsed()}s] Phase 3: "
        f"Calling DeepSeek ({LLM_MODEL})..."
    )

    prompt = build_prompt(title, desc, content, pub_date)

    try:
        persian_article = await asyncio.wait_for(
            call_deepseek(llm, prompt),
            timeout=LLM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] DeepSeek timed out.")
        persian_article = None
    except Exception as e:
        print(f"[WARN] [{elapsed()}s] DeepSeek error: {e}")
        persian_article = None

    # Save to DB even on LLM failure to prevent infinite retry
    if not persian_article:
        print(
            f"[ERROR] [{elapsed()}s] LLM failed. "
            "Saving link to prevent retry loop."
        )
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
        return {"status": "error", "reason": "llm_failed", "posted": False}

    print(
        f"[INFO] [{elapsed()}s] "
        f"DeepSeek response: {len(persian_article)} chars"
    )

    # ════════════════════════════════════════════════
    # PHASE 4 — BUILD CAPTION + POST TO TELEGRAM
    # Budget: 8 seconds
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 4: Posting to Telegram...")

    caption   = build_caption(persian_article)
    image_url = extract_image(entry)

    print(f"[INFO] [{elapsed()}s] Caption length: {len(caption)} chars")
    print(
        f"[INFO] [{elapsed()}s] Image: "
        f"{image_url[:80] if image_url else 'None (text-only post)'}"
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
        print(f"[ERROR] [{elapsed()}s] Telegram error: {e}")
        posted = False

    # Save to DB only on successful post
    if posted:
        print(f"[SUCCESS] [{elapsed()}s] Posted: {title[:60]}")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
    else:
        print(
            f"[ERROR] [{elapsed()}s] "
            "Telegram failed — not saving to DB (will retry next run)."
        )

    print(
        f"[INFO] ═══ v5.0 done in {elapsed()}s "
        f"| posted={posted} ═══"
    )
    return {"status": "success", "posted": posted}


# ─────────────────────────────────────────────
# PHASE 1 — PARALLEL FEED SCANNER
# ─────────────────────────────────────────────

async def find_candidate_parallel(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode,
):
    """
    Fetch all RSS feeds simultaneously using asyncio thread pool.
    Returns the newest unposted article dict, or None.
    """
    loop    = asyncio.get_event_loop()
    tasks   = [
        loop.run_in_executor(
            None, fetch_feed_entries, url, time_threshold
        )
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

    print(f"[INFO] {len(all_candidates)} recent articles found.")

    if not all_candidates:
        return None

    # Newest articles first
    all_candidates.sort(key=lambda x: x["pub_date"], reverse=True)

    # Return first article not yet in DB
    for c in all_candidates:
        if not is_duplicate(
            databases, database_id, collection_id, c["link"], sdk_mode
        ):
            return c
        print(f"[INFO] Already posted: {c['title'][:55]}")

    return None


def fetch_feed_entries(feed_url, time_threshold):
    """
    Synchronous RSS parser — runs in thread pool.
    Returns list of recent article dicts from one feed.
    """
    import socket
    try:
        old = socket.getdefaulttimeout()
        socket.setdefaulttimeout(FEED_FETCH_TIMEOUT)
        feed = feedparser.parse(feed_url)
        socket.setdefaulttimeout(old)
    except Exception as e:
        print(f"[WARN] feedparser error ({feed_url[:50]}): {e}")
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

        raw   = entry.get("summary") or entry.get("description") or ""
        desc  = re.sub(r"<[^>]+>", " ", raw)
        desc  = re.sub(r"\s+", " ", desc).strip()

        candidates.append({
            "title":       title,
            "link":        link,
            "description": desc,
            "feed_url":    feed_url,
            "pub_date":    pub_date,
            "entry":       entry,
        })

    return candidates


# ─────────────────────────────────────────────
# PHASE 2 — ARTICLE SCRAPER
# ─────────────────────────────────────────────

def scrape_article(url):
    """
    Fetch article URL and extract clean paragraph text.
    Synchronous — called via run_in_executor.
    Returns plain text string or None.
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
            timeout=SCRAPE_TIMEOUT - 1,
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        # Remove noise elements
        for tag in soup([
            "script", "style", "nav", "footer", "header",
            "aside", "form", "iframe", "noscript", "figure",
            "figcaption", "button", "input", "select", "svg",
        ]):
            tag.decompose()

        # Find main article body
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
        print(f"[WARN] Scrape HTTP {e.response.status_code}: {url[:60]}")
        return None
    except Exception as e:
        print(f"[WARN] Scrape error: {e}")
        return None


# ─────────────────────────────────────────────
# PHASE 3 — DEEPSEEK VIA OPENROUTER
# ─────────────────────────────────────────────

def build_prompt(title, description, content, pub_date):
    """
    Magazine-quality Persian rewrite prompt for DeepSeek.
    Concise to reduce token usage and response time.
    """
    return f"""You are a senior Persian fashion magazine editor.
Your task: translate and rewrite the following English fashion news
into a fluent, elegant Persian article suitable for a top fashion publication.

─── SOURCE ───
Title:   {title}
Summary: {description[:400]}
Content: {content[:1500]}
Date:    {pub_date.strftime('%Y-%m-%d')}

─── RULES ───
1. Write entirely in Persian (Farsi).
2. Keep proper nouns in English: brand names, designer names,
   city names, event names (e.g. Chanel, Dior, Milan, Paris Fashion Week).
3. Do NOT use section labels (no "Headline:", "Body:", etc.).
4. Structure:
   • Line 1: Attention-grabbing Persian headline (8–12 words).
   • Empty line.
   • Paragraph 1: Strong lead (2 