# ============================================================
# Function 1: International Fashion Poster
# Project: @irfashionnews — FashionBotProject
# Version: 5.1 — SYNTAX FIXED (Feb 2026)
#
# Fix: build_prompt() rewritten to avoid triple-quote
#      conflict inside f-string (Python syntax error 491)
#
# LLM: deepseek/deepseek-chat:free via OpenRouter
# Flow: RSS scan → scrape → DeepSeek → Telegram → Appwrite
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
DB_LINK_MAX        = 999
DB_TITLE_MAX       = 499
DB_FEED_URL_MAX    = 499
DB_SOURCE_TYPE_MAX = 19

# Timeout budgets (seconds)
FEED_FETCH_TIMEOUT = 6
FEEDS_SCAN_TIMEOUT = 18
SCRAPE_TIMEOUT     = 8
LLM_TIMEOUT        = 30
TELEGRAM_TIMEOUT   = 8

# LLM model
LLM_MODEL = "deepseek/deepseek-chat:free"


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
    print("[INFO] ═══ Function 1 v5.1 started ═══")
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

    # ── Initialize OpenRouter ──
    llm = AsyncOpenAI(
        api_key=openrouter_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://t.me/irfashionnews",
            "X-Title":      "IrFashionNews Bot",
        },
    )
    print(f"[INFO] LLM model: {LLM_MODEL}")

    # ── Initialize Telegram ──
    bot = Bot(token=token)

    # ── Initialize Appwrite ──
    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)
    sdk_mode  = "new" if hasattr(databases, "list_rows") else "legacy"
    print(f"[INFO] Appwrite SDK mode: {sdk_mode}")

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)

    # ════════════════════════════════════════════════
    # PHASE 1 — PARALLEL RSS SCAN
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
        print("[INFO] No new unposted articles found.")
        return {"status": "success", "posted": False}

    title    = candidate["title"]
    link     = candidate["link"]
    desc     = candidate["description"]
    feed_url = candidate["feed_url"]
    pub_date = candidate["pub_date"]
    entry    = candidate["entry"]

    print(f"[INFO] [{elapsed()}s] Selected: {title[:70]}")

    # ════════════════════════════════════════════════
    # PHASE 2 — SCRAPE ARTICLE
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

    content = (
        full_text
        if full_text and len(full_text) > len(desc)
        else desc[:MAX_RSS_CHARS]
    )

    print(
        f"[INFO] [{elapsed()}s] Content: "
        f"{'scraped' if full_text else 'rss-summary'} "
        f"({len(content)} chars)"
    )

    # ════════════════════════════════════════════════
    # PHASE 3 — DEEPSEEK LLM
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 3: Calling DeepSeek...")

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

    if not persian_article:
        print(f"[ERROR] [{elapsed()}s] LLM failed. Saving to prevent retry.")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
        return {"status": "error", "reason": "llm_failed", "posted": False}

    print(f"[INFO] [{elapsed()}s] DeepSeek: {len(persian_article)} chars")

    # ════════════════════════════════════════════════
    # PHASE 4 — POST TO TELEGRAM
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 4: Posting to Telegram...")

    caption   = build_caption(persian_article)
    image_url = extract_image(entry)

    print(f"[INFO] [{elapsed()}s] Caption: {len(caption)} chars")
    print(
        f"[INFO] [{elapsed()}s] Image: "
        f"{image_url[:80] if image_url else 'None'}"
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

    if posted:
        print(f"[SUCCESS] [{elapsed()}s] Posted: {title[:60]}")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
    else:
        print(f"[ERROR] [{elapsed()}s] Telegram failed.")

    print(
        f"[INFO] ═══ v5.1 done in {elapsed()}s "
        f"| posted={posted} ═══"
    )
    return {"status": "success", "posted": posted}


# ─────────────────────────────────────────────
# PHASE 1 HELPERS
# ─────────────────────────────────────────────

async def find_candidate_parallel(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode,
):
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

    all_candidates.sort(key=lambda x: x["pub_date"], reverse=True)

    for c in all_candidates:
        if not is_duplicate(
            databases, database_id, collection_id, c["link"], sdk_mode
        ):
            return c
        print(f"[INFO] Already posted: {c['title'][:55]}")

    return None


def fetch_feed_entries(feed_url, time_threshold):
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
            timeout=SCRAPE_TIMEOUT - 1,
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
        print(f"[WARN] Scrape HTTP {e.response.status_code}: {url[:60]}")
        return None
    except Exception as e:
        print(f"[WARN] Scrape error: {e}")
        return None


# ─────────────────────────────────────────────
# PHASE 3 — PROMPT + DEEPSEEK CALL
# ─────────────────────────────────────────────

def build_prompt(title, description, content, pub_date):
    """
    Build Persian magazine rewrite prompt.
    Uses string concatenation — avoids triple-quote
    syntax error inside f-strings.
    """
    date_str      = pub_date.strftime("%Y-%m-%d")
    desc_short    = description[:400]
    content_short = content[:1500]

    return (
        "You are a senior Persian fashion magazine editor.\n"
        "Translate and rewrite the following English fashion news into\n"
        "a fluent, elegant Persian article for a top fashion publication.\n"
        "\n"
        "SOURCE:\n"
        f"Title:   {title}\n"
        f"Summary: {desc_short}\n"
        f"Content: {content_short}\n"
        f"Date:    {date_str}\n"
        "\n"
        "RULES:\n"
        "1. Write entirely in Persian (Farsi).\n"
        "2. Keep proper nouns in English: brand names, designer names,\n"
        "   city names, event names "
        "(e.g. Chanel, Dior, Milan, Paris Fashion Week).\n"
        "3. Do NOT use section labels (no Headline, Body, etc.).\n"
        "4. Structure:\n"
        "   - Line 1: Persian headline (8-12 words).\n"
        "   - Empty line.\n"
        "   - Paragraph 1: Lead (2 sentences, most important fact).\n"
        "   - Empty line.\n"
        "   - Paragraphs 2-4: Body with logical flow.\n"
        "   - Empty line.\n"
        "   - Final paragraph: 2-sentence industry analysis.\n"
        "5. Tone: formal, engaging, journalistic.\n"
        "6. Length: 200-280 words.\n"
        "7. Use ONLY facts from the source. No speculation.\n"
        "\n"
        "Output ONLY the Persian article. No preamble. No commentary:"
    )


async def call_deepseek(llm, prompt):
    """
    Call DeepSeek model via OpenRouter.
    Returns cleaned Persian text or None.
    """
    try:
        response = await llm.chat.completions.create(
            model=LLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.65,
            max_tokens=800,
        )

        text = (response.choices[0].message.content or "").strip()

        # Remove DeepSeek thinking tags
        text = re.sub(
            r"<think>.*?</think>", "", text, flags=re.DOTALL
        ).strip()

        # Remove markdown code fences
        text = re.sub(r"^```[\w]*\n?", "", text).strip()
        text = re.sub(r"\n?```$", "", text).strip()

        if not text:
            print("[WARN] DeepSeek returned empty text.")
            return None

        return text

    except Exception as e:
        err = str(e)
        if "429" in err:
            print("[WARN] DeepSeek: rate limited (429).")
        elif "404" in err:
            print("[WARN] DeepSeek: model not found (404).")
        else:
            print(f"[WARN] DeepSeek error: {err[:150]}")
        return None


# ─────────────────────────────────────────────
# PHASE 4 — CAPTION BUILDER
# ─────────────────────────────────────────────

def build_caption(persian_article):
    """
    Build Telegram caption:
    1. Bold title
    2. @irfashionnews
    3. Body text
    4. Channel signature
    Enforces 1024-char Telegram limit.
    """
    lines       = persian_article.strip().split("\n")
    title_line  = ""
    body_lines  = []
    found_title = False

    for line in lines:
        s = line.strip().strip("*").strip("#").strip("_").strip()
        if not s:
            if found_title:
                body_lines.append("")
            continue
        if not found_title:
            title_line  = s
            found_title = True
        else:
            body_lines.append(s)

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

    if len(caption) > CAPTION_MAX:
        overflow  = len(caption) - CAPTION_MAX
        safe_body = esc(body_text)
        trimmed   = safe_body[:max(0, len(safe_body) - overflow - 5)] + "…"
        body_idx  = 2 if title_line else 1
        if body_idx < len(parts):
            parts[body_idx] = trimmed
            caption = "\n\n".join(parts)

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
            print("[INFO] Text post sent (no image).")
        return True
    except Exception as e:
        print(f"[ERROR] Telegram: {e}")
        return False


# ─────────────────────────────────────────────
# IMAGE EXTRACTOR
# ─────────────────────────────────────────────

def extract_image(entry):
    # 1. media:content explicit image
    for m in entry.get("media_content", []):
        if m.get("url") and m.get("medium") == "image":
            return m["url"]

    # 2. media:content image extension
    for m in entry.get("media_content", []):
        url = m.get("url", "")
        if url and any(
            url.lower().endswith(e)
            for e in [".jpg", ".jpeg", ".png", ".webp"]
        ):
            return url

    # 3. enclosure
    enc = entry.get("enclosure")
    if enc:
        url = enc.get("href") or enc.get("url", "")
        if url and enc.get("type", "").startswith("image/"):
            return url

    # 4. media:thumbnail
    thumbs = entry.get("media_thumbnail", [])
    if thumbs and thumbs[0].get("url"):
        return thumbs[0]["url"]

    # 5. img in summary/description
    for field in ["summary", "description"]:
        html = entry.get(field, "")
        if html:
            img = BeautifulSoup(html, "html.parser").find("img")
            if img:
                src = img.get("src", "")
                if src.startswith("http"):
                    return src

    # 6. content:encoded
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
# APPWRITE DATABASE HELPERS
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
                result = databases.list_rows(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=[Query.equal("link", link[:DB_LINK_MAX])],
                )
            else:
                result = databases.list_documents(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=[Query.equal("link", link[:DB_LINK_MAX])],
                )
            return result["total"] > 0
        except AppwriteException as e:
            print(f"[WARN] is_duplicate: {e.message}")
            return False
        except Exception as e:
            print(f"[WARN] is_duplicate: {e}")
            return False


def save_to_db(databases, database_id, collection_id,
               link, title, feed_url, pub_date, source_type, sdk_mode):
    data = _build_db_data(link, title, feed_url, pub_date, source_type)
    print(
        f"[INFO] Saving → "
        f"link: {data['link'][:60]} | "
        f"published_at: {data['published_at']}"
    )
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
