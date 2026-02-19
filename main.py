# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    6.0 — PRODUCTION STABLE
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds (Appwrite free plan setting)
#
# What's new in v6.0:
#   [1] Trend-aware article selection
#       Scores each article by freshness + title signals
#       Picks highest-scored unposted article, not just newest
#   [2] Verified free OpenRouter model fallback chain
#       Primary:  deepseek/deepseek-chat:free
#       Fallback: mistralai/mistral-7b-instruct:free
#       Last:     qwen/qwen-2-7b-instruct:free
#   [3] Content quality gate
#       Skips articles with < 150 chars of usable content
#   [4] Cleaner prompt — reduces DeepSeek token usage
#   [5] All syntax errors fixed (no triple-quote f-strings)
#   [6] Appwrite SDK auto-detection preserved
#   [7] Full deprecation warning suppression
#
# Flow:
#   RSS scan (parallel) → score + select → scrape →
#   quality gate → LLM → caption → Telegram → DB save
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

warnings.filterwarnings("ignore", category=DeprecationWarning, module="appwrite")


# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────

COLLECTION_ID     = "history"
SOURCE_TYPE       = "en"
ARTICLE_AGE_HOURS = 36      # scan window — wider = more candidates to score
CAPTION_MAX       = 1020    # Telegram caption hard limit
MAX_SCRAPED_CHARS = 2500
MAX_RSS_CHARS     = 900
MIN_CONTENT_CHARS = 150     # quality gate — skip thin articles

# Appwrite schema field limits (1 under max)
DB_LINK_MAX        = 999
DB_TITLE_MAX       = 499
DB_FEED_URL_MAX    = 499
DB_SOURCE_TYPE_MAX = 19

# Timeout budgets (seconds)
# Total budget: 120s (Appwrite timeout setting)
FEED_FETCH_TIMEOUT = 7      # per-feed socket timeout
FEEDS_SCAN_TIMEOUT = 20     # parallel scan hard cap
SCRAPE_TIMEOUT     = 10     # article scrape hard cap
LLM_TIMEOUT        = 55     # per-model attempt cap
TELEGRAM_TIMEOUT   = 10     # Telegram send hard cap

# ── Trend scoring weights ──
# Used to rank articles by interest level
SCORE_RECENCY_MAX   = 40    # max points for freshness
SCORE_TITLE_KEYWORD = 15    # per matching trend keyword in title
SCORE_HAS_IMAGE     = 10    # bonus if RSS entry has image
SCORE_DESC_LENGTH   = 10    # bonus if description is rich

# ── High-interest fashion keywords for scoring ──
TREND_KEYWORDS = [
    # Business signals
    "launches", "unveils", "debuts", "announces", "names",
    "acquires", "appoints", "partners", "expands", "opens",
    # Trend signals
    "trend", "collection", "season", "runway", "fashion week",
    "capsule", "collab", "collaboration", "limited edition",
    # Engagement signals
    "viral", "popular", "iconic", "exclusive", "first look",
    "top", "best", "most", "new", "latest",
    # Brand signals (high interest)
    "chanel", "dior", "gucci", "prada", "louis vuitton",
    "zara", "h&m", "nike", "adidas", "balenciaga",
    "versace", "fendi", "burberry", "valentino", "armani",
]

# ── Verified free OpenRouter models (Feb 2026) ──
# Tried in order — first success wins
LLM_MODELS = [
    "deepseek/deepseek-chat:free",
    "mistralai/mistral-7b-instruct:free",
    "qwen/qwen-2-7b-instruct:free",
]


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
    print("[INFO] ═══ Function 1 v6.0 started ═══")
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
    openrouter_key    = os.environ.get("OPENROUTER_API_KEY")

    missing = [
        k for k, v in {
            "TELEGRAM_BOT_TOKEN":   token,
            "TELEGRAM_CHANNEL_ID":  chat_id,
            "APPWRITE_PROJECT_ID":  appwrite_project,
            "APPWRITE_API_KEY":     appwrite_key,
            "APPWRITE_DATABASE_ID": database_id,
            "OPENROUTER_API_KEY":   openrouter_key,
        }.items() if not v
    ]
    if missing:
        print(f"[ERROR] Missing env vars: {missing}")
        return {"status": "error", "missing_vars": missing}

    # ── Clients ──
    llm = AsyncOpenAI(
        api_key=openrouter_key,
        base_url="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://t.me/irfashionnews",
            "X-Title":      "IrFashionNews Bot",
        },
    )

    bot = Bot(token=token)

    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)
    sdk_mode  = "new" if hasattr(databases, "list_rows") else "legacy"

    print(f"[INFO] SDK mode: {sdk_mode} | Models: {LLM_MODELS}")

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)

    # ════════════════════════════════════════════════
    # PHASE 1 — PARALLEL RSS SCAN + TREND SCORING
    # Fetch all feeds simultaneously.
    # Score every article by interest signals.
    # Pick the highest-scored unposted article.
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
        f"[INFO] [{elapsed()}s] Selected (score={score}): "
        f"{title[:65]}"
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

    # Use richest available text
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

    # ── Quality gate ──
    # If we have almost no content, the LLM will produce a
    # thin article. Skip and mark as posted to avoid retry.
    if len(content) < MIN_CONTENT_CHARS:
        print(
            f"[WARN] [{elapsed()}s] Content too thin "
            f"({len(content)} chars < {MIN_CONTENT_CHARS}). "
            "Skipping — saving to DB."
        )
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
        return {
            "status": "skipped",
            "reason": "content_too_thin",
            "posted": False,
        }

    # ════════════════════════════════════════════════
    # PHASE 3 — LLM CASCADE
    # Try each model in order until one succeeds.
    # Each attempt gets a fair time slice.
    # Budget: 55 seconds total across all attempts
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 3: LLM translation...")

    prompt          = build_prompt(title, desc, content, pub_date)
    persian_article = None
    llm_start       = loop.time()
    time_per_model  = LLM_TIMEOUT / len(LLM_MODELS)

    for model in LLM_MODELS:
        if persian_article:
            break

        time_used = loop.time() - llm_start
        remaining = LLM_TIMEOUT - time_used
        if remaining < 5:
            print(f"[WARN] [{elapsed()}s] LLM budget exhausted.")
            break

        attempt_budget = min(time_per_model, remaining)
        print(
            f"[INFO] [{elapsed()}s] Trying {model} "
            f"({attempt_budget:.0f}s budget)..."
        )

        try:
            persian_article = await asyncio.wait_for(
                call_llm(llm, prompt, model),
                timeout=attempt_budget,
            )
            if persian_article:
                print(
                    f"[SUCCESS] [{elapsed()}s] "
                    f"{model}: {len(persian_article)} chars"
                )
        except asyncio.TimeoutError:
            print(f"[WARN] [{elapsed()}s] {model} timed out.")
        except Exception as e:
            print(f"[WARN] [{elapsed()}s] {model} error: {e}")

    if not persian_article:
        print(
            f"[ERROR] [{elapsed()}s] All LLMs failed. "
            "Saving link to prevent retry loop."
        )
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
        return {"status": "error", "reason": "llm_failed", "posted": False}

    # ════════════════════════════════════════════════
    # PHASE 4 — CAPTION + TELEGRAM POST
    # Budget: 10 seconds
    # ════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 4: Posting...")

    caption   = build_caption(persian_article)
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
        print(f"[ERROR] [{elapsed()}s] Telegram error: {e}")
        posted = False

    if posted:
        print(f"[SUCCESS] [{elapsed()}s] Posted: {title[:60]}")
        save_to_db(
            databases, database_id, COLLECTION_ID,
            link, title, feed_url, pub_date, SOURCE_TYPE, sdk_mode,
        )
    else:
        print(
            f"[ERROR] [{elapsed()}s] "
            "Telegram failed — not saving (will retry next run)."
        )

    print(
        f"[INFO] ═══ v6.0 done in {elapsed()}s "
        f"| posted={posted} ═══"
    )
    return {"status": "success", "posted": posted}


# ─────────────────────────────────────────────
# PHASE 1 — TREND-AWARE CANDIDATE SELECTION
# ─────────────────────────────────────────────

async def find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, now,
):
    """
    Fetch all RSS feeds in parallel.
    Score every recent article by interest signals.
    Check duplicates only for top candidates (saves DB calls).
    Return highest-scored unposted article or None.
    """
    loop  = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(
            None, fetch_feed_entries, url, time_threshold
        )
        for url in feeds
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_candidates = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            print(f"[WARN] Feed error ({feeds[i][:45]}): {result}")
            continue
        if result:
            all_candidates.extend(result)

    print(f"[INFO] {len(all_candidates)} recent articles collected.")

    if not all_candidates:
        return None

    # Score every candidate
    for c in all_candidates:
        c["score"] = score_article(c, now)

    # Sort by score descending
    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    # Log top 5 for visibility
    print("[INFO] Top candidates by score:")
    for c in all_candidates[:5]:
        print(
            f"       score={c['score']:>3} | "
            f"{c['title'][:55]}"
        )

    # Check duplicates — return first unposted from top of list
    for c in all_candidates:
        if not is_duplicate(
            databases, database_id, collection_id, c["link"], sdk_mode
        ):
            return c
        print(f"[INFO] Duplicate: {c['title'][:50]}")

    return None


def score_article(candidate, now):
    """
    Score an article 0–100 based on free signals only.
    Higher = more likely to be interesting to readers.

    Signals used:
      - Recency (up to 40 points)
      - Trend keyword matches in title (up to 45 points)
      - Has image in RSS (10 points)
      - Description richness (10 points)
    """
    score = 0

    # ── Recency score (0–40) ──
    # Articles from last 3 hours get full points
    # Older articles decay linearly
    age_hours = (now - candidate["pub_date"]).total_seconds() / 3600
    if age_hours <= 3:
        recency = SCORE_RECENCY_MAX
    elif age_hours <= ARTICLE_AGE_HOURS:
        recency = int(
            SCORE_RECENCY_MAX * (1 - (age_hours - 3) / (ARTICLE_AGE_HOURS - 3))
        )
    else:
        recency = 0
    score += recency

    # ── Keyword score ──
    title_lower = candidate["title"].lower()
    desc_lower  = candidate["description"].lower()

    matched = 0
    for kw in TREND_KEYWORDS:
        if kw in title_lower:
            score   += SCORE_TITLE_KEYWORD
            matched += 1
        elif kw in desc_lower:
            score   += 5   # description match worth less
            matched += 1
        if matched >= 3:   # cap keyword bonus
            break

    # ── Image bonus ──
    if extract_image(candidate["entry"]):
        score += SCORE_HAS_IMAGE

    # ── Description richness bonus ──
    if len(candidate["description"]) > 200:
        score += SCORE_DESC_LENGTH

    return min(score, 100)


def fetch_feed_entries(feed_url, time_threshold):
    """
    Synchronous RSS parser — runs in thread pool.
    Returns list of article candidate dicts.
    """
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
# PHASE 2 — ARTICLE SCRAPER
# ─────────────────────────────────────────────

def scrape_article(url):
    """
    Fetch article and extract clean paragraph text.
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
        print(f"[WARN] Scrape HTTP {e.response.status_code}: {url[:60]}")
        return None
    except Exception as e:
        print(f"[WARN] Scrape: {e}")
        return None


# ─────────────────────────────────────────────
# PHASE 3 — LLM PROMPT + CALL
# ─────────────────────────────────────────────

def build_prompt(title, description, content, pub_date):
    """
    Persian magazine rewrite prompt.
    Uses explicit string concatenation — no triple-quote f-string.
    """
    date_str = pub_date.strftime("%Y-%m-%d")
    d        = description[:400]
    c        = content[:1600]

    return (
        "You are a senior Persian fashion magazine editor at a prestigious Iranian publication.\n"
        "Your task: translate and rewrite the following English fashion news\n"
        "into a fluent, elegant Persian article.\n"
        "\n"
        "SOURCE ARTICLE:\n"
        f"Title:   {title}\n"
        f"Summary: {d}\n"
        f"Content: {c}\n"
        f"Date:    {date_str}\n"
        "\n"
        "WRITING RULES:\n"
        "1. Write entirely in Persian (Farsi).\n"
        "2. Keep proper nouns in original language:\n"
        "   brand names, designer names, city names, event names.\n"
        "   Examples: Chanel, Dior, Gucci, Milan, Paris Fashion Week\n"
        "3. Do NOT add section labels (no Headline, Lead, Body, etc.).\n"
        "4. Article structure:\n"
        "   - Line 1: Strong Persian headline (8-12 words)\n"
        "   - Blank line\n"
        "   - Lead paragraph: 2 sentences, the most important fact\n"
        "   - Blank line\n"
        "   - Body: 2 to 3 paragraphs with smooth logical flow\n"
        "   - Blank line\n"
        "   - Closing: 2 sentences of neutral industry perspective\n"
        "5. Tone: formal, elegant, journalistic.\n"
        "   Write as if publishing in a top Iranian fashion magazine.\n"
        "6. Target length: 200 to 280 words.\n"
        "7. Use ONLY facts stated in the source. No invented details.\n"
        "\n"
        "Output ONLY the Persian article text.\n"
        "Do not include any English commentary, labels, or preamble.\n"
        "Start directly with the Persian headline:"
    )


async def call_llm(llm, prompt, model):
    """
    Call one OpenRouter model.
    Cleans response of artifacts.
    Returns Persian text string or None.
    """
    try:
        response = await llm.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.65,
            max_tokens=850,
        )

        text = (response.choices[0].message.content or "").strip()

        # Remove DeepSeek chain-of-thought blocks
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # Remove markdown fences
        text = re.sub(r"^```[\w]*\n?", "", text).strip()
        text = re.sub(r"\n?```$", "", text).strip()

        if not text:
            print(f"[WARN] {model}: empty response.")
            return None

        return text

    except Exception as e:
        err = str(e)
        if "429" in err:
            print(f"[WARN] {model}: rate limited.")
        elif "404" in err:
            print(f"[WARN] {model}: not found.")
        else:
            print(f"[WARN] {model}: {err[:120]}")
        return None


# ─────────────────────────────────────────────
# PHASE 4 — CAPTION BUILDER
# ─────────────────────────────────────────────

def build_caption(persian_article):
    """
    Build Telegram caption:

    <b>Persian headline</b>

    @irfashionnews

    Body text...

    Channel signature

    Hard limit: 1024 chars (Telegram caption maximum).
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
        idx       = 2 if title_line else 1
        if idx < len(parts):
            parts[idx] = trimmed
            caption    = "\n\n".join(parts)

    return caption


# ─────────────────────────────────────────────
# TELEGRAM SENDER
# ─────────────────────────────────────────────

async def send_to_telegram(bot, chat_id, caption, image_url):
    """
    Send photo + caption or text-only message.
    Returns True on success.
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
            print("[INFO] Text post sent.")
        return True
    except Exception as e:
        print(f"[ERROR] Telegram: {e}")
        return False


# ─────────────────────────────────────────────
# IMAGE EXTRACTOR — 6 methods in priority order
# ─────────────────────────────────────────────

def extract_image(entry):
    # 1. media:content explicit image medium
    for m in entry.get("media_content", []):
        if m.get("url") and m.get("medium") == "image":
            return m["url"]

    # 2. media:content image file extension
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

    # 5. img tag in summary/description HTML
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
    """Build schema-safe data dict. No auto fields included."""
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
