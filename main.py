# ============================================================
# Function 1: International Fashion Poster
# Project: @irfashionnews — FashionBotProject
# Version: 4.1 — TIMEOUT FIXED (Feb 2026)
#
# Key fix: Separate fast RSS scan from slow operations.
# 1. Quick scan ALL feeds (parallel, 15s timeout total)
# 2. Pick first unposted article
# 3. Scrape + LLM + Post (single article only)
#
# Appwrite Free plan: 60-second function timeout
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
MAX_SCRAPED_CHARS = 2000    # reduced from 2500 to save LLM time
MAX_RSS_CHARS     = 800

# Timeout budgets (seconds) — must total well under 60
FEED_FETCH_TIMEOUT   = 6    # per feed HTTP timeout
FEEDS_SCAN_TIMEOUT   = 18   # total time allowed for scanning all feeds
SCRAPE_TIMEOUT       = 8    # article scrape timeout
LLM_TIMEOUT          = 30   # LLM call timeout
TELEGRAM_TIMEOUT     = 8    # Telegram send timeout


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
    print("[INFO] ═══ Function 1 v4.1 started ═══")
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

    # ── Validate ──
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

    # ════════════════════════════════════════════
    # PHASE 1: FAST PARALLEL RSS SCAN
    # Fetch all feeds simultaneously with tight timeout
    # Goal: find one unposted article quickly
    # Time budget: ~15 seconds
    # ════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 1: Scanning {len(RSS_FEEDS)} feeds in parallel...")

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

    print(f"[INFO] [{elapsed()}s] Phase 1 complete.")

    if not candidate:
        print("[INFO] No new articles found in any feed.")
        return {"status": "success", "posted": False, "reason": "no_new_articles"}

    title    = candidate["title"]
    link     = candidate["link"]
    desc     = candidate["description"]
    feed_url = candidate["feed_url"]
    pub_date = candidate["pub_date"]
    entry    = candidate["entry"]

    print(f"[INFO] [{elapsed()}s] Candidate: {title[:70]}")

    # ════════════════════════════════════════════
    # PHASE 2: SCRAPE ARTICLE
    # Single article scrape — tight timeout
    # Time budget: ~8 seconds
    # ════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 2: Scraping article...")

    try:
        full_content = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None, scrape_article, link
            ),
            timeout=SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Scrape timed out, using RSS summary.")
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

    # ════════════════════════════════════════════
    # PHASE 3: LLM TRANSLATION + REWRITING
    # Single LLM call — tight timeout
    # Time budget: ~25 seconds
    # ════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 3: Calling LLM...")

    prompt = build_prompt(
        title=title,
        description=desc,
        content=content_for_llm,
        feed_url=feed_url,
        pub_date=pub_date,
    )

    try:
        persian_article = await asyncio.wait_for(
            call_llm(llm_client, prompt),
            timeout=LLM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] LLM timed out.")
        persian_article = None

    if not persian_article:
        print("[WARN] LLM failed — saving link to DB to avoid retry loop.")
        # Save to DB so we don't keep retrying this article
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, now, SOURCE_TYPE,
        )
        return {"status": "error", "reason": "llm_failed", "posted": False}

    print(f"[INFO] [{elapsed()}s] LLM done ({len(persian_article)} chars).")

    # ════════════════════════════════════════════
    # PHASE 4: BUILD CAPTION + POST TO TELEGRAM
    # Time budget: ~8 seconds
    # ════════════════════════════════════════════
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

    if send_ok:
        print(f"[SUCCESS] [{elapsed()}s] Posted: {title[:60]}")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, now, SOURCE_TYPE,
        )
    else:
        print(f"[ERROR] [{elapsed()}s] Telegram send failed.")

    print(f"[INFO] ═══ Function 1 finished in {elapsed()}s | posted={send_ok} ═══")
    return {"status": "success", "posted": send_ok}


# ─────────────────────────────────────────────
# PHASE 1 HELPERS: PARALLEL FEED SCANNING
# ─────────────────────────────────────────────

async def find_candidate_parallel(
    feeds, databases, database_id, collection_id, time_threshold
):
    """
    Fetch all RSS feeds in parallel.
    Return the first unposted article found, or None.

    Uses asyncio to run feedparser (blocking) in thread pool
    so all feeds are fetched simultaneously instead of sequentially.
    """
    loop = asyncio.get_event_loop()

    # Create tasks for all feeds simultaneously
    tasks = [
        loop.run_in_executor(None, fetch_feed_entries, feed_url, time_threshold)
        for feed_url in feeds
    ]

    # Wait for all feeds with a shared timeout
    # as_completed lets us process results as they arrive
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect all candidates from all feeds
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

    # Sort by publish date — newest first
    all_candidates.sort(key=lambda x: x["pub_date"], reverse=True)

    # Check duplicates — find first unposted
    for candidate in all_candidates:
        if not is_duplicate(databases, database_id, collection_id, candidate["link"]):
            return candidate
        else:
            print(f"[INFO] Already posted: {candidate['title'][:50]}")

    return None


def fetch_feed_entries(feed_url, time_threshold):
    """
    Synchronous function to fetch and parse one RSS feed.
    Runs in thread pool executor.
    Returns list of candidate dicts (recent articles only).
    """
    try:
        # feedparser has its own timeout via socket
        import socket
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

            description = (
                entry.get("summary") or entry.get("description") or ""
            ).strip()
            # Strip HTML from description
            description = re.sub(r"<[^>]+>", " ", description)
            description = re.sub(r"\s+", " ", description).strip()

            candidates.append({
                "title":       title,
                "link":        link,
                "description": description,
                "feed_url":    feed_url,
                "pub_date":    pub_date,
                "entry":       entry,   # keep full entry for image extraction
            })

        return candidates

    except Exception as e:
        print(f"[WARN] fetch_feed_entries error ({feed_url[:50]}): {e}")
        return []


# ─────────────────────────────────────────────
# PHASE 2: ARTICLE SCRAPER
# ─────────────────────────────────────────────

def scrape_article(url):
    """
    Fetch article page and extract clean text.
    Synchronous — run via run_in_executor.
    Returns text string or None.
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

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer",
                          "header", "aside", "form", "iframe",
                          "noscript", "figure", "figcaption",
                          "button", "input", "select", "svg"]):
            tag.decompose()

        # Priority article containers
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
# PHASE 3: LLM — PROMPT + CALL
# ─────────────────────────────────────────────

def build_prompt(title, description, content, feed_url, pub_date):
    """
    Build concise but effective prompt for Persian magazine article.
    Kept shorter than v3.0 to reduce LLM processing time.
    """
    return f"""You are a Persian fashion magazine editor. Write a fluent Persian fashion news article.

SOURCE:
Title: {title}
Summary: {description[:400]}
Content: {content[:1500]}
Date: {pub_date.strftime('%Y-%m-%d')}

RULES:
- Write entirely in Persian (Farsi)
- Keep brand/designer/city names in English (Chanel, Dior, Milan, etc.)
- NO section labels
- Start with a bold headline (8-12 words)
- Then lead paragraph (2 sentences)
- Then 2-3 body paragraphs
- End with brief industry analysis (2 sentences)
- Total: 180-280 words
- Only use facts from the source above

Output ONLY the Persian article:"""


async def call_llm(client, prompt):
    """
    Call OpenRouter LLM. Returns text or None.
    Uses faster/lighter model settings to beat timeout.
    """
    try:
        response = await client.chat.completions.create(
            model="deepseek/deepseek-r1-0528:free",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=700,      # reduced from 1000 — saves 10-15 sec
        )
        result = (response.choices[0].message.content or "").strip()

        # Remove any <think>...</think> tags DeepSeek sometimes includes
        result = re.sub(r"<think>.*?</think>", "", result, flags=re.DOTALL).strip()

        if not result:
            return None

        return result

    except Exception as e:
        print(f"[ERROR] LLM error: {e}")
        return None


# ─────────────────────────────────────────────
# PHASE 4: CAPTION BUILDER
# ─────────────────────────────────────────────

def build_caption(persian_article):
    """
    Final caption format:
    1. [Photo as media]
    2. Bold Persian title (first line)
    3. @irfashionnews
    4. Article body
    5. Channel signature
    """
    lines = persian_article.strip().split("\n")

    # Extract first non-empty line as title
    title_line = ""
    body_lines = []
    found_title = False

    for line in lines:
        stripped = line.strip()
        # Strip any markdown bold markers LLM might add
        stripped = stripped.strip("*").strip("#").strip()
        if not stripped:
            if found_title:
                body_lines.append("")
            continue
        if not found_title:
            title_line = stripped
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

    # Hard trim to Telegram 1024-char limit
    if len(caption) > CAPTION_MAX:
        overflow = len(caption) - CAPTION_MAX
        if body_text:
            safe_body = esc(body_text)
            trimmed   = safe_body[:max(0, len(safe_body) - overflow - 5)] + "…"
            # Find body index (index 2 if title exists, else 1)
            body_idx = 2 if title_line else 1
            if body_idx < len(parts):
                parts[body_idx] = trimmed
                caption = "\n\n".join(parts)

    return caption


# ─────────────────────────────────────────────
# SHARED HELPERS
# ─────────────────────────────────────────────

def extract_image(entry):
    """
    Extract best image URL from RSS entry.
    Priority: media:content → enclosure → thumbnail → HTML img
    """
    # 1. media:content with medium=image
    for media in entry.get("media_content", []):
        url = media.get("url", "")
        if url and media.get("medium") == "image":
            return url

    # 2. media:content any image extension
    for media in entry.get("media_content", []):
        url = media.get("url", "")
        if url and any(
            url.lower().endswith(ext)
            for ext in [".jpg", ".jpeg", ".png", ".webp"]
        ):
            return url

    # 3. enclosure
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

    # 5. Parse img tag from summary/description HTML
    for field in ["summary", "description"]:
        html = entry.get(field, "")
        if html:
            soup = BeautifulSoup(html, "html.parser")
            img  = soup.find("img")
            if img:
                src = img.get("src", "")
                if src and src.startswith("http"):
                    return src

    # 6. content:encoded
    if hasattr(entry, "content") and entry.content:
        html = entry.content[0].get("value", "")
        if html:
            soup = BeautifulSoup(html, "html.parser")
            img  = soup.find("img")
            if img:
                src = img.get("src", "")
                if src and src.startswith("http"):
                    return src

    return None


def is_duplicate(databases, database_id, collection_id, link):
    """Return True if link already in Appwrite collection."""
    try:
        result = databases.list_documents(
            database_id=database_id,
            collection_id=collection_id,
            queries=[Query.equal("link", link)],
        )
        return result["total"] > 0
    except AppwriteException as e:
        print(f"[WARN] Duplicate check failed: {e.message}")
        return False
    except Exception as e:
        print(f"[WARN] Duplicate check error: {e}")
        return False


async def send_to_telegram(bot, chat_id, caption, image_url):
    """Send photo+caption or text to Telegram."""
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
            print("[INFO] Text message sent.")
        return True
    except Exception as e:
        print(f"[ERROR] Telegram send error: {e}")
        return False


def save_to_db(databases, database_id, collection_id,
               link, title, feed_url, pub_date, now, source_type):
    """Save article metadata to Appwrite."""
    try:
        databases.create_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id="unique()",
            data={
                "link":         link,
                "title":        title[:500],
                "published_at": pub_date.isoformat(),
                "feed_url":     feed_url,
                "created_at":   now.isoformat(),
                "source_type":  source_type,
            },
        )
        print("[SUCCESS] Saved to Appwrite.")
    except AppwriteException as e:
        print(f"[WARN] Appwrite save error: {e.message}")
    except Exception as e:
        print(f"[WARN] DB save error: {e}")


# ─────────────────────────────────────────────
# LOCAL TEST
# ─────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())