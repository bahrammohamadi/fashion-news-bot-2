# ============================================================
# Function 1: International Fashion Poster
# Project: @irfashionnews — FashionBotProject
# Version: 3.0 (Feb 2026)
# Purpose: Fetch EN fashion RSS → scrape full article →
#          Rewrite in magazine-quality Persian via LLM →
#          Post photo + caption to Telegram
# Schedule: Every 45 minutes
# ============================================================

import os
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
COLLECTION_ID = "history"
MAX_RSS_CHARS = 800          # fallback if scraping fails
MAX_SCRAPED_CHARS = 2500     # max chars sent to LLM from full article
ARTICLE_AGE_HOURS = 24       # ignore articles older than this
SOURCE_TYPE = "en"           # for database tracking

# Telegram caption limit is 1024 chars; we keep text clean and within limit
CAPTION_MAX = 1020


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
    print("[INFO] ═══ Function 1 (International) started ═══")

    # ── Load environment variables ──
    token            = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id          = os.environ.get("TELEGRAM_CHANNEL_ID")
    appwrite_endpoint = os.environ.get(
        "APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1"
    )
    appwrite_project = os.environ.get("APPWRITE_PROJECT_ID")
    appwrite_key     = os.environ.get("APPWRITE_API_KEY")
    database_id      = os.environ.get("APPWRITE_DATABASE_ID")
    openrouter_key   = os.environ.get("OPENROUTER_API_KEY")

    # ── Validate required vars ──
    missing = [
        name for name, val in {
            "TELEGRAM_BOT_TOKEN": token,
            "TELEGRAM_CHANNEL_ID": chat_id,
            "APPWRITE_PROJECT_ID": appwrite_project,
            "APPWRITE_API_KEY": appwrite_key,
            "APPWRITE_DATABASE_ID": database_id,
            "OPENROUTER_API_KEY": openrouter_key,
        }.items() if not val
    ]
    if missing:
        print(f"[ERROR] Missing environment variables: {missing}")
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

    # ── Time filter ──
    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)

    posted = False

    # ── Loop through feeds ──
    for feed_url in RSS_FEEDS:
        if posted:
            break

        try:
            print(f"[INFO] Parsing feed: {feed_url}")
            feed = feedparser.parse(feed_url)

            if not feed.entries:
                print(f"[INFO] Empty feed: {feed_url}")
                continue

        except Exception as feed_parse_err:
            print(f"[ERROR] Failed to parse feed {feed_url}: {feed_parse_err}")
            continue

        for entry in feed.entries:
            if posted:
                break

            # ── Date filter ──
            published = (
                entry.get("published_parsed") or entry.get("updated_parsed")
            )
            if not published:
                print(f"[INFO] No date found, skipping: {entry.get('title','')[:50]}")
                continue

            pub_date = datetime(*published[:6], tzinfo=timezone.utc)
            if pub_date < time_threshold:
                continue

            # ── Extract basic fields ──
            title       = (entry.get("title") or "").strip()
            link        = (entry.get("link") or "").strip()
            description = (
                entry.get("summary") or entry.get("description") or ""
            ).strip()

            if not title or not link:
                print("[INFO] Missing title or link, skipping.")
                continue

            # ── Duplicate check ──
            if is_duplicate(databases, database_id, COLLECTION_ID, link):
                print(f"[INFO] Duplicate skipped: {title[:60]}")
                continue

            # ── Scrape full article ──
            full_content = scrape_article(link)
            content_for_llm = (
                full_content
                if full_content and len(full_content) > len(description)
                else description[:MAX_RSS_CHARS]
            )

            print(
                f"[INFO] Content source: "
                f"{'scraped' if full_content else 'rss-summary'} "
                f"({len(content_for_llm)} chars)"
            )

            # ── Build LLM prompt ──
            prompt = build_prompt(
                title=title,
                description=description,
                content=content_for_llm,
                feed_url=feed_url,
                pub_date=pub_date,
            )

            # ── Call LLM ──
            persian_article = await call_llm(llm_client, prompt)
            if not persian_article:
                print("[WARN] LLM returned empty, skipping entry.")
                continue

            # ── Build final caption (exact format requested) ──
            caption = build_caption(persian_article)

            # ── Get image ──
            image_url = extract_image(entry)

            # ── Send to Telegram ──
            send_ok = await send_to_telegram(
                bot=bot,
                chat_id=chat_id,
                caption=caption,
                image_url=image_url,
            )

            if send_ok:
                posted = True
                print(f"[SUCCESS] Posted: {title[:70]}")

                # ── Save to Appwrite ──
                save_to_db(
                    databases=databases,
                    database_id=database_id,
                    collection_id=COLLECTION_ID,
                    link=link,
                    title=title,
                    feed_url=feed_url,
                    pub_date=pub_date,
                    now=now,
                    source_type=SOURCE_TYPE,
                )
            else:
                print(f"[WARN] Telegram send failed for: {title[:60]}")

    print(f"[INFO] ═══ Function 1 finished | posted={posted} ═══")
    return {"status": "success", "posted": posted}


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def is_duplicate(databases, database_id, collection_id, link):
    """Check if link already exists in Appwrite collection."""
    try:
        result = databases.list_documents(
            database_id=database_id,
            collection_id=collection_id,
            queries=[Query.equal("link", link)],
        )
        return result["total"] > 0
    except AppwriteException as e:
        print(f"[WARN] Appwrite duplicate check failed: {e.message}")
        return False  # proceed even if check fails
    except Exception as e:
        print(f"[WARN] Unexpected error in duplicate check: {e}")
        return False


def scrape_article(url):
    """
    Attempt to fetch and extract clean text from the article URL.
    Returns cleaned text string or None on failure.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; FashionBot/3.0; "
                "+https://t.me/irfashionnews)"
            )
        }
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "footer",
                          "header", "aside", "form", "iframe",
                          "noscript", "figure"]):
            tag.decompose()

        # Try common article containers first
        article_body = (
            soup.find("article")
            or soup.find("div", class_=lambda c: c and "article" in c.lower())
            or soup.find("div", class_=lambda c: c and "content" in c.lower())
            or soup.find("main")
        )

        if article_body:
            paragraphs = article_body.find_all("p")
        else:
            paragraphs = soup.find_all("p")

        text = " ".join(p.get_text(separator=" ").strip() for p in paragraphs)
        text = " ".join(text.split())  # normalize whitespace

        if len(text) < 100:
            return None

        # Trim to max allowed chars for LLM
        return text[:MAX_SCRAPED_CHARS]

    except requests.exceptions.Timeout:
        print(f"[WARN] Scrape timeout: {url}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"[WARN] Scrape request error: {e}")
        return None
    except Exception as e:
        print(f"[WARN] Scrape unexpected error: {e}")
        return None


def build_prompt(title, description, content, feed_url, pub_date):
    """
    Construct the LLM prompt for magazine-quality Persian fashion article.
    """
    return f"""You are a senior Persian fashion editor at a prestigious publication.
Your job is to write magazine-quality Persian fashion news articles.

─── SOURCE INFORMATION ───
Title: {title}
Summary: {description}
Full Content: {content}
Source: {feed_url}
Published: {pub_date.strftime('%Y-%m-%d')}

─── WRITING RULES ───
1. Write entirely in fluent, literary Persian (Farsi).
2. DO NOT translate proper nouns: brand names, designer names, city names, 
   event names (e.g. Chanel, Louis Vuitton, Dior, Milan, Paris Fashion Week).
3. DO NOT add any section labels (no "Headline:", "Lead:", "Body:" etc.).
4. Structure:
   • Line 1: Bold, attention-grabbing Persian headline (8–14 words).
   • Empty line.
   • Lines 2–3: Strong lead paragraph (1–2 sentences, most important fact).
   • Empty line.
   • Lines 4+: 2–4 body paragraphs with logical flow.
   • Final paragraph: 2–3 sentences of neutral industry analysis
     (market impact / designer / consumer perspective).
5. Tone: formal, engaging, journalistic — like Vogue or Harper's Bazaar Persian.
6. Length: 220–350 words exactly.
7. Base ONLY on provided information. No speculation. No invented facts.
8. Start immediately with the Persian headline — no preamble.

─── OUTPUT ───
Output only the clean Persian article text:
"""


async def call_llm(client, prompt):
    """
    Call LLM via OpenRouter. Returns Persian article string or None.
    """
    try:
        print("[INFO] Calling LLM (DeepSeek R1)...")
        response = await client.chat.completions.create(
            model="deepseek/deepseek-r1-0528:free",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.65,
            max_tokens=1000,
        )
        result = response.choices[0].message.content.strip()

        if not result:
            print("[WARN] LLM returned empty content.")
            return None

        print(f"[INFO] LLM response received ({len(result)} chars)")
        return result

    except Exception as e:
        print(f"[ERROR] LLM call failed: {e}")
        return None


def build_caption(persian_article):
    """
    Build the final Telegram caption in the exact requested format:

    [Photo is sent separately as media]
    1. Bold Persian Title (first line of article)
    2. @irfashionnews
    3. Full article body
    4. Channel signature at bottom

    Telegram caption limit: 1024 characters (HTML mode).
    We split title from body and bold the title using HTML.
    """
    lines = persian_article.strip().split("\n")

    # Extract title (first non-empty line)
    title_line = ""
    body_lines = []
    found_title = False

    for line in lines:
        stripped = line.strip()
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

    # Compose caption
    caption_parts = []

    if title_line:
        # Bold title using HTML
        safe_title = (
            title_line
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        caption_parts.append(f"<b>{safe_title}</b>")

    caption_parts.append("@irfashionnews")

    if body_text:
        safe_body = (
            body_text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        caption_parts.append(safe_body)

    caption_parts.append("🌐 <i>کانال مد و فشن ایرانی</i>")

    full_caption = "\n\n".join(caption_parts)

    # Telegram caption hard limit is 1024 chars
    if len(full_caption) > CAPTION_MAX:
        # Trim body to fit
        overflow = len(full_caption) - CAPTION_MAX
        if body_text and len(safe_body) > overflow + 10:
            trimmed_body = safe_body[: len(safe_body) - overflow - 5] + "…"
            caption_parts[2] = trimmed_body  # index 2 is body
            full_caption = "\n\n".join(caption_parts)

    return full_caption


def extract_image(entry):
    """
    Extract the best available image URL from an RSS entry.
    Tries multiple locations in priority order.
    """
    # 1. media:content tag
    media_content = entry.get("media_content", [])
    for media in media_content:
        url = media.get("url", "")
        medium = media.get("medium", "")
        if url and medium == "image":
            return url
    # Also try media_content without medium check
    for media in media_content:
        url = media.get("url", "")
        if url and (url.endswith(".jpg") or url.endswith(".jpeg")
                    or url.endswith(".png") or url.endswith(".webp")):
            return url

    # 2. enclosure tag
    enclosure = entry.get("enclosure")
    if enclosure:
        enc_type = enclosure.get("type", "")
        enc_url  = enclosure.get("href") or enclosure.get("url", "")
        if enc_url and enc_type.startswith("image/"):
            return enc_url

    # 3. media:thumbnail
    media_thumbnail = entry.get("media_thumbnail", [])
    if media_thumbnail:
        url = media_thumbnail[0].get("url", "")
        if url:
            return url

    # 4. Parse image from summary HTML
    summary_html = entry.get("summary", "") or entry.get("description", "")
    if summary_html:
        soup = BeautifulSoup(summary_html, "html.parser")
        img = soup.find("img")
        if img:
            src = img.get("src", "")
            if src and src.startswith("http"):
                return src

    # 5. content:encoded
    content_encoded = ""
    if hasattr(entry, "content") and entry.content:
        content_encoded = entry.content[0].get("value", "")
    if content_encoded:
        soup = BeautifulSoup(content_encoded, "html.parser")
        img = soup.find("img")
        if img:
            src = img.get("src", "")
            if src and src.startswith("http"):
                return src

    print("[INFO] No image found in RSS entry.")
    return None


async def send_to_telegram(bot, chat_id, caption, image_url):
    """
    Send post to Telegram channel.
    If image_url is available → send_photo with caption.
    Otherwise → send_message with text only.
    Returns True on success, False on failure.
    """
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
            print("[INFO] Text message sent (no image).")
        return True

    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")
        return False


def save_to_db(databases, database_id, collection_id,
               link, title, feed_url, pub_date, now, source_type):
    """
    Save posted article metadata to Appwrite for duplicate prevention.
    """
    try:
        databases.create_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id="unique()",
            data={
                "link":         link,
                "title":        title,
                "published_at": pub_date.isoformat(),
                "feed_url":     feed_url,
                "created_at":   now.isoformat(),
                "source_type":  source_type,   # "en" for international
            },
        )
        print("[SUCCESS] Saved to Appwrite database.")
    except AppwriteException as e:
        print(f"[WARN] Appwrite save failed: {e.message}")
    except Exception as e:
        print(f"[WARN] Unexpected error saving to DB: {e}")


# ─────────────────────────────────────────────
# LOCAL TEST RUNNER
# ─────────────────────────────────────────────
if __name__ == "__main__":
    asyncio.run(main())