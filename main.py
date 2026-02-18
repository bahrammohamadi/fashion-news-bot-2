# ============================================================
# Function 1: International Fashion Poster
# Project: @irfashionnews — FashionBotProject
# Version: 4.3 — SCHEMA FIXED (Feb 2026)
#
# Fixes in this version vs 4.2:
#   [1] published_at → sent as datetime object (not ISO string)
#   [2] link         → hard trimmed to 999 chars (schema size: 1000)
#   [3] feed_url     → hard trimmed to 499 chars (schema size: 500)
#   [4] title        → hard trimmed to 499 chars (schema size: 500)
#   [5] Removed all unknown fields (created_at was already removed)
#   [6] Dual SDK support: create_row (>=1.8) + create_document fallback
#   [7] Dual SDK support: list_rows  (>=1.8) + list_documents  fallback
#
# Database: fashion_db
# Collection: history
# Columns: $id, link, title, published_at, feed_url, source_type,
#          $createdAt, $updatedAt
#
# Schedule: Every 45 minutes
# ============================================================

import os
import re
import asyncio
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


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
COLLECTION_ID     = "history"
SOURCE_TYPE       = "en"
ARTICLE_AGE_HOURS = 24
CAPTION_MAX       = 1020
MAX_SCRAPED_CHARS = 2000
MAX_RSS_CHARS     = 800

# Appwrite schema size limits (stay 1 char under to be safe)
DB_LINK_MAX        = 999    # schema: 1000
DB_TITLE_MAX       = 499    # schema: 500
DB_FEED_URL_MAX    = 499    # schema: 500
DB_SOURCE_TYPE_MAX = 19     # schema: 20

# Timeout budgets (seconds) — total must stay under 60s
FEED_FETCH_TIMEOUT  = 6
FEEDS_SCAN_TIMEOUT  = 18
SCRAPE_TIMEOUT      = 8
LLM_TIMEOUT         = 45
TELEGRAM_TIMEOUT    = 8

# LLM models — primary (fast) + fallback
LLM_PRIMARY  = "google/gemini-flash-1.5:free"            # ~3-8s
LLM_FALLBACK = "meta-llama/llama-3.1-8b-instruct:free"  # ~8-12s


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
    print("[INFO] ═══ Function 1 v4.3 started ═══")
    start_time = asyncio.get_event_loop().time()

    def elapsed():
        return round(asyncio.get_event_loop().time() - start_time, 1)

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

    # ── Validate all required vars ──
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

    # ── Initialize clients ──
    bot = Bot(token=token)

    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)

    llm_client = AsyncOpenAI(
        api_key=openrouter_key,
        base_url="https://openrouter.ai/api/v1",
    )

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)

    # ════════════════════════════════════════════════
    # PHASE 1: FAST PARALLEL RSS SCAN
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
            ),
            timeout=FEEDS_SCAN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Feed scan timed out.")
        candidate = None

    print(f"[INFO] [{elapsed()}s] Phase 1 complete.")

    if not candidate:
        print("[INFO] No new unposted articles found.")
        return {"status": "success", "posted": False, "reason": "no_new_articles"}

    title    = candidate["title"]
    link     = candidate["link"]
    desc     = candidate["description"]
    feed_url = candidate["feed_url"]
    pub_date = candidate["pub_date"]
    entry    = candidate["entry"]

    print(f"[INFO] [{elapsed()}s] Candidate: {title[:70]}")

    # ════════════════════════════════════════════════
    # PHASE 2: SCRAPE FULL ARTICLE
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 2: Scraping article...")

    try:
        full_content = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, scrape_article, link
            ),
            timeout=SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Scrape timed out — using RSS summary.")
        full_content = None
    except Exception as e:
        print(f"[WARN] [{elapsed()}s] Scrape error: {e}")
        full_content = None

    content_for_llm = (
        full_content
        if full_content and len(full_content) > len(desc)
        else desc[:MAX_RSS_CHARS]
    )

    print(
        f"[INFO] [{elapsed()}s] Content: "
        f"{'scraped' if full_content else 'rss-summary'} "
        f"({len(content_for_llm)} chars)"
    )

    # ════════════════════════════════════════════════
    # PHASE 3: LLM — TRANSLATE + REWRITE IN PERSIAN
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 3: Calling LLM ({LLM_PRIMARY})...")

    prompt = build_prompt(
        title=title,
        description=desc,
        content=content_for_llm,
        feed_url=feed_url,
        pub_date=pub_date,
    )

    persian_article = None

    # Try primary model
    try:
        persian_article = await asyncio.wait_for(
            call_llm(llm_client, prompt, model=LLM_PRIMARY),
            timeout=LLM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Primary LLM timed out.")
    except Exception as e:
        print(f"[WARN] [{elapsed()}s] Primary LLM error: {e}")

    # Try fallback model if primary failed
    if not persian_article:
        remaining = max(5.0, 57.0 - elapsed())
        print(
            f"[INFO] [{elapsed()}s] "
            f"Trying fallback LLM ({LLM_FALLBACK}) "
            f"— budget: {remaining}s..."
        )
        try:
            persian_article = await asyncio.wait_for(
                call_llm(llm_client, prompt, model=LLM_FALLBACK),
                timeout=remaining,
            )
        except asyncio.TimeoutError:
            print(f"[WARN] [{elapsed()}s] Fallback LLM also timed out.")
        except Exception as e:
            print(f"[WARN] [{elapsed()}s] Fallback LLM error: {e}")

    # Both LLMs failed — save link to prevent infinite retry loop
    if not persian_article:
        print(
            f"[ERROR] [{elapsed()}s] Both LLMs failed. "
            "Saving link to DB to prevent retry."
        )
        save_to_db(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_ID,
            link=link,
            title=title,
            feed_url=feed_url,
            pub_date=pub_date,
            source_type=SOURCE_TYPE,
        )
        return {"status": "error", "reason": "llm_failed", "posted": False}

    print(f"[INFO] [{elapsed()}s] LLM done ({len(persian_article)} chars).")

    # ════════════════════════════════════════════════
    # PHASE 4: BUILD CAPTION + POST TO TELEGRAM
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 4: Posting to Telegram...")

    caption   = build_caption(persian_article)
    image_url = extract_image(entry)

    try:
        send_ok = await asyncio.wait_for(
            send_to_telegram(bot, chat_id, caption, image_url),
            timeout=TELEGRAM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Telegram send timed out.")
        send_ok = False
    except Exception as e:
        print(f"[ERROR] [{elapsed()}s] Telegram error: {e}")
        send_ok = False

    if send_ok:
        print(f"[SUCCESS] [{elapsed()}s] Posted: {title[:60]}")
        save_to_db(
            databases=databases,
            database_id=database_id,
            collection_id=COLLECTION_ID,
            link=link,
            title=title,
            feed_url=feed_url,
            pub_date=pub_date,
            source_type=SOURCE_TYPE,
        )
    else:
        print(f"[ERROR] [{elapsed()}s] Telegram failed — not saving to DB.")

    print(
        f"[INFO] ═══ v4.3 finished in {elapsed()}s "
        f"| posted={send_ok} ═══"
    )
    return {"status": "success", "posted": send_ok}


# ─────────────────────────────────────────────
# PHASE 1 HELPERS: PARALLEL FEED SCANNING
# ─────────────────────────────────────────────

async def find_candidate_parallel(
    feeds, databases, database_id, collection_id, time_threshold
):
    """
    Fetch all RSS feeds simultaneously.
    Returns first unposted article (newest first), or None.
    """
    loop = asyncio.get_event_loop()

    tasks = [
        loop.run_in_executor(
            None, fetch_feed_entries, feed_url, time_threshold
        )
        for feed_url in feeds
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_candidates = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"[WARN] Feed error ({feeds[i][:50]}): {result}")
            continue
        if result:
            all_candidates.extend(result)

    print(f"[INFO] Found {len(all_candidates)} recent articles across all feeds.")

    if not all_candidates:
        return None

    # Newest articles first
    all_candidates.sort(key=lambda x: x["pub_date"], reverse=True)

    # Return first unposted article
    for candidate in all_candidates:
        if not is_duplicate(
            databases, database_id, collection_id, candidate["link"]
        ):
            return candidate
        else:
            print(f"[INFO] Already posted: {candidate['title'][:50]}")

    return None


def fetch_feed_entries(feed_url, time_threshold):
    """
    Synchronous RSS fetcher — runs in thread pool executor.
    Returns list of recent article dicts from this feed.
    """
    import socket
    try:
        old_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(FEED_FETCH_TIMEOUT)
        feed = feedparser.parse(feed_url)
        socket.setdefaulttimeout(old_timeout)

        if not feed.entries:
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

            raw_desc    = entry.get("summary") or entry.get("description") or ""
            description = re.sub(r"<[^>]+>", " ", raw_desc)
            description = re.sub(r"\s+", " ", description).strip()

            candidates.append({
                "title":       title,
                "link":        link,
                "description": description,
                "feed_url":    feed_url,
                "pub_date":    pub_date,
                "entry":       entry,
            })

        return candidates

    except Exception as e:
        print(f"[WARN] fetch_feed_entries ({feed_url[:50]}): {e}")
        return []


# ─────────────────────────────────────────────
# PHASE 2: ARTICLE SCRAPER
# ─────────────────────────────────────────────

def scrape_article(url):
    """
    Fetch article page and extract clean plain text.
    Synchronous — called via run_in_executor.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }
        resp = requests.get(url, headers=headers, timeout=SCRAPE_TIMEOUT - 1)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        for tag in soup([
            "script", "style", "nav", "footer", "header",
            "aside", "form", "iframe", "noscript", "figure",
            "figcaption", "button", "input", "select", "svg",
        ]):
            tag.decompose()

        article_body = (
            soup.find("article")
            or soup.find("div", {"class": re.compile(r"article[-_]?body", re.I)})
            or soup.find("div", {"class": re.compile(r"post[-_]?content", re.I)})
            or soup.find("div", {"class": re.compile(r"entry[-_]?content", re.I)})
            or soup.find("div", {"class": re.compile(r"story[-_]?body", re.I)})
            or soup.find("main")
        )

        target     = article_body if article_body else soup
        paragraphs = target.find_all("p")

        text = " ".join(
            p.get_text(separator=" ").strip()
            for p in paragraphs
            if len(p.get_text().strip()) > 40
        )
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) < 100:
            return None

        return text[:MAX_SCRAPED_CHARS]

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
# PHASE 3: LLM PROMPT + CALL
# ─────────────────────────────────────────────

def build_prompt(title, description, content, feed_url, pub_date):
    """
    Concise prompt for Persian magazine-style fashion article.
    Shorter prompt = faster LLM response time.
    """
    return f"""You are a Persian fashion magazine editor. Write a fluent Persian fashion news article.

SOURCE:
Title: {title}
Summary: {description[:400]}
Content: {content[:1500]}
Date: {pub_date.strftime('%Y-%m-%d')}

RULES:
- Write ENTIRELY in Persian (Farsi)
- Keep brand/designer/city/event names in English (Chanel, Dior, Milan, etc.)
- NO section labels
- Start directly with a bold headline (8-12 words)
- Lead paragraph (2 sentences, most important fact)
- 2-3 body paragraphs with logical flow
- End with brief neutral industry analysis (2 sentences)
- Total: 180-280 words
- Use ONLY facts from the source above

Output ONLY the Persian article — no extra commentary:"""


async def call_llm(client, prompt, model):
    """
    Call OpenRouter with specified model.
    Returns cleaned Persian text or None.
    """
    try:
        print(f"[INFO] Requesting: {model}")
        response = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=700,
        )

        result = (response.choices[0].message.content or "").strip()

        # Strip <think> blocks (DeepSeek artifact — harmless for other models)
        result = re.sub(
            r"<think>.*?</think>", "", result, flags=re.DOTALL
        ).strip()

        # Strip markdown code fences if any
        result = re.sub(r"^```[\w]*\n?", "", result).strip()
        result = re.sub(r"\n?```$", "", result).strip()

        if not result:
            print(f"[WARN] {model} returned empty content.")
            return None

        print(f"[INFO] LLM response: {len(result)} chars")
        return result

    except Exception as e:
        print(f"[ERROR] LLM call failed ({model}): {e}")
        return None


# ─────────────────────────────────────────────
# PHASE 4: CAPTION BUILDER
# ─────────────────────────────────────────────

def build_caption(persian_article):
    """
    Build Telegram caption — exact format:

    1. [Photo as media]
    2. <b>Bold Persian title</b>
    3. @irfashionnews
    4. Article body text
    5. Channel signature

    Enforces 1024-char Telegram caption limit.
    """
    lines       = persian_article.strip().split("\n")
    title_line  = ""
    body_lines  = []
    found_title = False

    for line in lines:
        stripped = line.strip().strip("*").strip("#").strip("_").strip()
        if not stripped:
            if found_title:
                body_lines.append("")
            continue
        if not found_title:
            title_line  = stripped
            found_title = True
        else:
            body_lines.append(stripped)

    body_text = "\n".join(body_lines).strip()

    def esc(t):
        return (
            t.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    parts = []
    if title_line:
        parts.append(f"<b>{esc(title_line)}</b>")
    parts.append("@irfashionnews")
    if body_text:
        parts.append(esc(body_text))
    parts.append("🌐 <i>کانال مد و فشن ایرانی</i>")

    caption = "\n\n".join(parts)

    # Trim to Telegram limit
    if len(caption) > CAPTION_MAX:
        overflow = len(caption) - CAPTION_MAX
        if body_text:
            safe_body = esc(body_text)
            trimmed   = safe_body[:max(0, len(safe_body) - overflow - 5)] + "…"
            body_idx  = 2 if title_line else 1
            if body_idx < len(parts):
                parts[body_idx] = trimmed
                caption = "\n\n".join(parts)

    return caption


# ─────────────────────────────────────────────
# DATABASE HELPERS
# Schema: link(1000), title(500), published_at(datetime),
#         feed_url(500), source_type(20)
# ─────────────────────────────────────────────

def _build_db_data(link, title, feed_url, pub_date, source_type):
    """
    Build the data dict that exactly matches the Appwrite schema.

    SCHEMA RULES APPLIED:
    ┌─────────────┬──────────┬──────────────────────────────────────┐
    │ Field       │ Type     │ Fix applied                          │
    ├─────────────┼──────────┼──────────────────────────────────────┤
    │ link        │ string   │ trimmed to 999 chars (limit: 1000)   │
    │ title       │ string   │ trimmed to 499 chars (limit: 500)    │
    │ published_at│ datetime │ sent as ISO-8601 string with Z       │
    │ feed_url    │ string   │ trimmed to 499 chars (limit: 500)    │
    │ source_type │ string   │ trimmed to 19 chars  (limit: 20)     │
    │ $id         │ auto     │ NOT sent — Appwrite generates it     │
    │ $createdAt  │ auto     │ NOT sent — Appwrite generates it     │
    │ $updatedAt  │ auto     │ NOT sent — Appwrite generates it     │
    └─────────────┴──────────┴──────────────────────────────────────┘
    """
    # Format datetime for Appwrite datetime field
    # Appwrite expects ISO-8601 format: "2026-02-18T10:30:00.000+00:00"
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)

    published_at_str = pub_date.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")

    return {
        "link":         link[:DB_LINK_MAX],
        "title":        title[:DB_TITLE_MAX],
        "published_at": published_at_str,
        "feed_url":     feed_url[:DB_FEED_URL_MAX],
        "source_type":  source_type[:DB_SOURCE_TYPE_MAX],
    }


def is_duplicate(databases, database_id, collection_id, link):
    """
    Return True if this link already exists in the history collection.
    Tries new SDK method first, falls back to deprecated method.
    """
    safe_link = link[:DB_LINK_MAX]

    # ── Try new SDK (>= 1.8.0) ──
    try:
        result = databases.list_rows(
            database_id=database_id,
            collection_id=collection_id,
            queries=[Query.equal("link", safe_link)],
        )
        return result["total"] > 0
    except AttributeError:
        pass  # SDK too old — try deprecated method
    except AppwriteException as e:
        print(f"[WARN] list_rows AppwriteException: {e.message}")
        return False
    except Exception as e:
        print(f"[WARN] list_rows error: {e}")
        return False

    # ── Fallback: deprecated SDK (< 1.8.0) ──
    try:
        result = databases.list_documents(
            database_id=database_id,
            collection_id=collection_id,
            queries=[Query.equal("link", safe_link)],
        )
        return result["total"] > 0
    except AppwriteException as e:
        print(f"[WARN] list_documents AppwriteException: {e.message}")
        return False
    except Exception as e:
        print(f"[WARN] list_documents error: {e}")
        return False


def save_to_db(databases, database_id, collection_id,
               link, title, feed_url, pub_date, source_type):
    """
    Save article metadata to Appwrite history collection.
    Tries new SDK method first, falls back to deprecated method.
    Only writes fields that exist in the schema.
    """
    data = _build_db_data(link, title, feed_url, pub_date, source_type)

    print(f"[INFO] Saving to DB: link={data['link'][:60]}...")
    print(f"[INFO] DB data: published_at={data['published_at']} | "
          f"source_type={data['source_type']}")

    # ── Try new SDK (>= 1.8.0) ──
    try:
        databases.create_row(
            database_id=database_id,
            collection_id=collection_id,
            row_id="unique()",
            data=data,
        )
        print("[SUCCESS] Saved to Appwrite via create_row.")
        return
    except AttributeError:
        pass  # SDK too old — try deprecated method
    except AppwriteException as e:
        print(f"[WARN] create_row AppwriteException: {e.message}")
        # Don't return — try fallback
    except Exception as e:
        print(f"[WARN] create_row error: {e}")

    # ── Fallback: deprecated SDK (< 1.8.0) ──
    try:
        databases.create_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id="unique()",
            data=data,
        )
        print("[SUCCESS] Saved to Appwrite via create_document (fallback).")
    except AppwriteException as e:
        print(f"[ERROR] create_document fallback failed: {e.message}")
    except Exception as e:
        print(f"[ERROR] DB save completely failed: {e}")


# ─────────────────────────────────────────────
# IMAGE EXTRACTOR
# ─────────────────────────────────────────────

def extract_image(entry):
    """
    Extract best available image URL from RSS entry.
    6 methods tried in priority order.
    """
    # 1. media:content — explicit image medium
    for media in entry.get("media_content", []):
        url = media.get("url", "")
        if url and media.get("medium") == "image":
            return url

    # 2. media:content — image file extension
    for media in entry.get("media_content", []):
        url = media.get("url", "")
        if url and any(
            url.lower().endswith(ext)
            for ext in [".jpg", ".jpeg", ".png", ".webp"]
        ):
            return url

    # 3. enclosure tag
    enc = entry.get("enclosure")
    if enc:
        enc_url  = enc.get("href") or enc.get("url", "")
        enc_type = enc.get("type", "")
        if enc_url and enc_type.startswith("image/"):
            return enc_url

    # 4. media:thumbnail
    thumbs = entry.get("media_thumbnail", [])
    if thumbs:
        url = thumbs[0].get("url", "")
        if url:
            return url

    # 5. <img> in summary/description HTML
    for field in ["summary", "description"]:
        html = entry.get(field, "")
        if html:
            soup = BeautifulSoup(html, "html.parser")
            img  = soup.find("img")
            if img:
                src = img.get("src", "")
                if src and src.startswith("http"):
                    return src

    # 6. content:encoded field
    if hasattr(entry, "content") and entry.content:
        html = entry.content[0].get("value", "")
        if html:
            soup = BeautifulSoup(html, "html.parser")
            img  = soup.find("img")
            if img:
                src = img.get("src", "")
                if src and src.startswith("http"):
                    return src

    print("[INFO] No image found in RSS entry.")
    return None


# ─────────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────────

async def send_to_telegram(bot, chat_id, caption, image_url):
    """Send photo+caption or text message to Telegram channel."""
    try:
        if image_url:
            await bot.send_photo(
                chat_id=chat_id,
                photo=image_url,
                caption=caption,
                parse_mode="HTML",
                disable_notification=True,
            )
            print(f"[INFO] Photo sent: {image_url[:70]}")
        else:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                disable_notification=True,
            )
            print("[INFO] Text message sent (no image).")
        return True
    except Exception as e:
        print(f"[ERROR] Telegram error: {e}")
        return False


# ─────────────────────────────────────────────
# LOCAL TEST RUNNER
# ─────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())