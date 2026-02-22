# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    11.0 — Mehrjameh Editorial Engine
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# WHAT CHANGED FROM v10.1:
#   ADDED:
#     - Mehrjameh editorial voice and caption structure
#     - Styling tip generation (Iranian-culture-aware)
#     - 5-candidate batch selection from DB
#     - context.log() / context.error() throughout
#     - _generate_styling_tip() — cultural style advisor
#     - _build_mehrjameh_caption() — new caption format
#     - _build_ai_prompt_body() — editorial Persian prompt
#     - _build_ai_prompt_title() — title translation prompt
#     - _build_ai_prompt_tip() — styling tip prompt
#     - Structured JSON output per article
#     - SDK deprecation fix: list_rows with legacy fallback
#
#   CAPTION STRUCTURE (Mehrjameh editorial format):
#   ┌──────────────────────────────────────────┐
#   │  [images sent first as media group]      │
#   │                                          │
#   │  **عنوان فارسی**                         │
#   │  ─────────────                           │
#   │  مد و فشن ایرانی                        │
#   │                                          │
#   │  خلاصه خبر (summary)                    │
#   │                                          │
#   │  💡 نکته استایلی (styling tip)           │
#   │                                          │
#   │  EMOJI  کانال مد و فشن ایرانی           │
#   │                                          │
#   │  #hashtag1 #hashtag2 ...                │
#   └──────────────────────────────────────────┘
#
#   PIPELINE:
#   Phase 1: Load 5 unposted candidates from DB
#   Phase 2: Score + select best candidate
#   Phase 3: Light dedup (posted=true only)
#   Phase 4: Parallel scrape (text + images)
#   Phase 5: Parallel AI race (summary + title + tip)
#   Phase 6: Build Mehrjameh caption
#   Phase 7: Soft lock write (status=locked, TTL=10min)
#   Phase 8: Post to Telegram (images first, then caption)
#   Phase 9: Update DB (posted=true OR status=failed)
#
#   AI PROVIDERS (parallel race, first-valid wins):
#     1. Groq       (llama3-70b-8192)     — PRIMARY
#     2. OpenRouter (mistral-7b-instruct) — SECONDARY
#
#   CULTURAL NOTES:
#   - Styling tips respect Iranian dress code context
#   - Language is literary, calm, and editorial (Mehrjameh tone)
#   - Brand names kept in Latin; Persian prose around them
#   - No reference to "Mehrjameh" brand name in output
# ============================================================


# ═══════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════

import os
import re
import time
import random
import hashlib
import asyncio
import warnings
import feedparser
import aiohttp
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from telegram import Bot, InputMediaPhoto, LinkPreviewOptions
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query

warnings.filterwarnings("ignore", category=DeprecationWarning)


# ═══════════════════════════════════════════════════════════
# SECTION 1 — CONFIGURATION
# ═══════════════════════════════════════════════════════════

# ── Appwrite ──
COLLECTION_ID = "history"
SOURCE_TYPE   = "en"

# ── Candidate batch size ──
# How many unposted articles to fetch from DB per run
# for scoring + selection.
CANDIDATE_BATCH_SIZE = 5

# ── Article filtering ──
ARTICLE_AGE_HOURS = 36
MIN_CONTENT_CHARS = 150
MAX_SCRAPED_CHARS = 3000
MAX_RSS_CHARS     = 1000

# ── Telegram ──
CAPTION_MAX         = 1020
MAX_IMAGES          = 10
ALBUM_CAPTION_DELAY = 2.0
STICKER_DELAY       = 1.5

# ── Appwrite DB field size limits ──
DB_LINK_MAX        = 999
DB_TITLE_MAX       = 499
DB_FEED_URL_MAX    = 499
DB_SOURCE_TYPE_MAX = 19
DB_HASH_MAX        = 64
DB_CATEGORY_MAX    = 49
DB_DOMAIN_HASH_MAX = 64
DB_REASON_MAX      = 499

# ── Operation timeouts (seconds) ──
FEED_FETCH_TIMEOUT  = 7
FEEDS_SCAN_TIMEOUT  = 22
SCRAPE_TIMEOUT      = 12
TELEGRAM_TIMEOUT    = 50

# ── Parallel AI race timeouts ──
AI_PER_API_TIMEOUT = 20
AI_RACE_TIMEOUT    = 35
AI_TITLE_TIMEOUT   = 15
AI_TIP_TIMEOUT     = 15

# ── Persian response validation ──
MIN_PERSIAN_CHARS = 30

# ── Groq ──
GROQ_MODEL       = "llama3-70b-8192"
GROQ_MAX_TOKENS  = 700
GROQ_TEMPERATURE = 0.4

# ── OpenRouter ──
OPENROUTER_MODEL       = "mistralai/mistral-7b-instruct"
OPENROUTER_MAX_TOKENS  = 700
OPENROUTER_TEMPERATURE = 0.4

# ── Deduplication / lock ──
LOCK_TTL_SECONDS           = 600
FUZZY_SIMILARITY_THRESHOLD = 0.65
FUZZY_LOOKBACK_COUNT       = 150
DOMAIN_DEDUP_HOURS         = 6

# ── Article state values ──
STATUS_LOCKED = "locked"
STATUS_POSTED = "posted"
STATUS_FAILED = "failed"

# ── Peak hours UTC (Tehran = UTC+3:30) ──
PEAK_HOURS_UTC  = {4, 5, 6, 9, 10, 11, 16, 17, 18, 19}
PEAK_HOUR_BONUS = 15

# ── Scoring weights ──
SCORE_RECENCY_MAX       = 40
SCORE_TITLE_KEYWORD     = 15
SCORE_DESC_KEYWORD      = 5
SCORE_HAS_IMAGE         = 10
SCORE_DESC_LENGTH       = 10
SCORE_FASHION_RELEVANCE = 20

# ── Fashion relevance keywords ──
FASHION_RELEVANCE_KEYWORDS = {
    "chanel", "dior", "gucci", "prada", "louis vuitton", "lv",
    "balenciaga", "versace", "fendi", "burberry", "valentino",
    "armani", "hermes", "celine", "givenchy", "saint laurent",
    "bottega veneta", "miu miu", "loewe", "jacquemus", "off-white",
    "alexander mcqueen", "vivienne westwood", "stella mccartney",
    "zara", "h&m", "hm", "uniqlo", "massimo dutti", "cos",
    "mango", "asos", "shein", "& other stories",
    "nike", "adidas", "puma", "reebok", "new balance", "converse",
    "vans", "supreme", "palace", "stussy", "kith", "jordan",
    "fashion week", "runway", "catwalk", "collection", "couture",
    "resort", "pre-fall", "ss26", "fw26", "ss25", "fw25",
    "pfw", "mfw", "lfw", "nyfw", "met gala", "red carpet",
    "fashion show", "lookbook", "editorial",
    "trend", "style", "outfit", "wardrobe", "streetwear", "luxury",
    "vintage", "sustainable fashion", "fast fashion", "capsule",
    "collaboration", "collab", "model", "designer",
    "creative director", "fashion",
}

# ── Trend keywords ──
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

# ── Content categories ──
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

# ── Hashtag map ──
HASHTAG_MAP = {
    "chanel":         "#Chanel #شنل",
    "dior":           "#Dior #دیور",
    "gucci":          "#Gucci #گوچی",
    "prada":          "#Prada #پرادا",
    "louis vuitton":  "#LouisVuitton #لویی_ویتون",
    "balenciaga":     "#Balenciaga #بالنسیاگا",
    "versace":        "#Versace #ورساچه",
    "zara":           "#Zara #زارا",
    "hm":             "#HM #اچ_اند_ام",
    "nike":           "#Nike #نایکی",
    "adidas":         "#Adidas #آدیداس",
    "runway":         "#Runway #رانوی",
    "fashion week":   "#FashionWeek #هفته_مد",
    "collection":     "#Collection #کالکشن",
    "sustainability": "#Sustainability #مد_پایدار",
    "beauty":         "#Beauty #زیبایی",
    "trend":          "#Trend #ترند",
    "style":          "#Style #استایل",
    "celebrity":      "#Celebrity #سلبریتی",
    "streetwear":     "#Streetwear #استریت_ویر",
    "luxury":         "#Luxury #لاکچری",
    "vintage":        "#Vintage #وینتیج",
    "met gala":       "#MetGala #مت_گالا",
    "red carpet":     "#RedCarpet #فرش_قرمز",
    "couture":        "#Couture #کوتور",
    "collab":         "#Collab #همکاری",
}
MAX_HASHTAGS = 5

# ── Fashion stickers ──
FASHION_STICKERS = [
    "CAACAgIAAxkBAAIBmGRx1yRFMVhVqVXLv_dAAXJMOdFNAAIUAAOVgnkAAVGGBbBjxbg4LwQ",
    "CAACAgIAAxkBAAIBmWRx1yRqy9JkN2DmV_Z2sRsKdaTjAAIVAAOVgnkAAc8R3q5p5-AELAQ",
    "CAACAgIAAxkBAAIBmmRx1yS2T2gfLqJQX9oK6LZqp1HIAAIWAAO0yXAAAV0MzCRF3ZRILAQ",
    "CAACAgIAAxkBAAIBm2Rx1ySiJV4dVeTuCTc-RfFDnfQpAAIXAAO0yXAAAA3Vm7IiJdisLAQ",
    "CAACAgIAAxkBAAIBnGRx1yT_jVlWt5xPJ7BO9aQ4JvFaAAIYAAO0yXAAAA0k9GZDQpLcLAQ",
]

# ── RSS feeds ──
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

# ── Boilerplate patterns ──
BOILERPLATE_PATTERNS = [
    "subscribe", "newsletter", "sign up", "cookie",
    "privacy policy", "all rights reserved", "terms of service",
    "advertisement", "sponsored content", "follow us",
    "share this", "read more", "click here", "tap here",
    "download the app", "get the app",
]

# ── Image constants ──
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_BLOCKLIST  = [
    "doubleclick", "googletagmanager", "googlesyndication",
    "facebook.com/tr", "analytics", "pixel", "beacon",
    "tracking", "counter", "stat.", "stats.",
]

# ── Stop words ──
TITLE_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "its", "it", "this", "that", "these", "those", "and", "or",
    "but", "as", "up", "out", "if", "about", "into", "over",
    "after", "new", "first", "last", "says", "said",
}


# ═══════════════════════════════════════════════════════════
# SECTION 2 — AI PROMPT TEMPLATES (Mehrjameh editorial voice)
# ═══════════════════════════════════════════════════════════

_PROMPT_BODY = """\
تو یک روزنامه‌نگار مد ایرانی هستی که برای یک کانال تلگرام فارسی‌زبان \
به نام «مد و فشن ایرانی» می‌نویسی.

وظیفه:
متن خبر مد انگلیسی زیر را بخوان و یک خلاصه کوتاه و روان فارسی بنویس.

قوانین:
- فارسی روان و ادبی بنویس. ترجمه تحت‌اللفظی نکن.
- حداکثر ۵ جمله. هر جمله باید مفید باشد.
- بگو: چه اتفاقی افتاده، چه کسی درگیر است، چرا برای مد اهمیت دارد.
- نثر روان بنویس. از بولت‌پوینت یا شماره‌گذاری استفاده نکن.
- هیچ کلمه انگلیسی در خروجی نباشد (نام برندها مستثنی هستند).
- هیچ توضیح، عنوان یا مقدمه‌ای اضافه نکن.
- فقط متن خلاصه فارسی را بنویس.

خبر:
\"\"\"
{input_text}
\"\"\"

خلاصه فارسی:"""

_PROMPT_TITLE = """\
تو یک مترجم حرفه‌ای برای یک کانال اخبار مد ایرانی هستی.

وظیفه:
عنوان انگلیسی مقاله مد زیر را به فارسی روان و طبیعی ترجمه کن.

قوانین:
- فقط ترجمه فارسی عنوان را بنویس.
- هیچ انگلیسی، توضیح، نقل قول یا مقدمه نداشته باشد.
- نام برندها را به خط لاتین نگه دار.
- مثل یک تیتر واقعی رسانه مد ایرانی به نظر برسد.

عنوان انگلیسی: {input_text}

عنوان فارسی:"""

_PROMPT_TIP = """\
تو یک مشاور استایل ایرانی هستی که برای مخاطبان ایرانی می‌نویسی.

وظیفه:
با توجه به خبر مد زیر، یک نکته استایلی کوتاه و کاربردی بنویس \
که برای زنان ایرانی مناسب و قابل استفاده باشد.

قوانین:
- فقط یک جمله یا دو جمله کوتاه بنویس.
- نکته باید عملی، الهام‌بخش و با فرهنگ ایرانی همخوان باشد.
- می‌توانی درباره رنگ، پارچه، لایه‌بندی، اکسسوری یا ترکیب لباس صحبت کنی.
- لحن آرام، صمیمی و ادبی داشته باشد.
- هیچ مقدمه یا توضیح اضافه نداشته باشد.
- فقط نکته استایلی را بنویس.

خبر:
\"\"\"
{input_text}
\"\"\"

نکته استایلی:"""


# ═══════════════════════════════════════════════════════════
# SECTION 3 — AI VALIDATION & EXTRACTION
# ═══════════════════════════════════════════════════════════

def _is_valid_persian(text: str | None) -> bool:
    """
    Validate genuine usable Persian text.
    All four conditions must pass:
      1. Not None, not empty, is a string
      2. Length >= MIN_PERSIAN_CHARS after stripping
      3. Contains at least one Persian Unicode character
      4. Does not contain known API error markers
    """
    if not text or not isinstance(text, str):
        return False
    stripped = text.strip()
    if len(stripped) < MIN_PERSIAN_CHARS:
        return False
    has_persian = any(
        "\u0600" <= ch <= "\u06ff"
        or "\ufb50" <= ch <= "\ufdff"
        or "\ufe70" <= ch <= "\ufeff"
        for ch in stripped
    )
    if not has_persian:
        return False
    _ERROR_MARKERS = (
        "error", "invalid_api_key", "rate_limit", "quota_exceeded",
        "model_not_found", "context_length_exceeded", "bad request",
        "unauthorized", "forbidden", "too many requests",
        "service unavailable", "internal server error",
    )
    if any(m in stripped.lower() for m in _ERROR_MARKERS):
        return False
    return True


def _extract_openai_content(data: dict) -> str | None:
    """Extract content from OpenAI-compatible chat completion response."""
    try:
        return (
            data
            .get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
            or ""
        ).strip() or None
    except (IndexError, AttributeError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════
# SECTION 4 — PARALLEL AI RACE ENGINE
# ═══════════════════════════════════════════════════════════

async def _call_groq(
    session: aiohttp.ClientSession,
    prompt: str,
    log_fn=print,
) -> str | None:
    """Groq API caller. Returns valid Persian or None."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        log_fn("[race] Groq: GROQ_API_KEY not set — skipping.")
        return None

    payload = {
        "model":       GROQ_MODEL,
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": GROQ_TEMPERATURE,
        "max_tokens":  GROQ_MAX_TOKENS,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    try:
        async with session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log_fn(f"[race] Groq HTTP {resp.status}: {body[:120]}")
                return None
            data   = await resp.json()
            result = _extract_openai_content(data)
            valid  = _is_valid_persian(result)
            log_fn(
                f"[race] Groq responded: "
                f"{len(result or '')}ch | valid={valid}"
            )
            return result if valid else None

    except asyncio.CancelledError:
        log_fn("[race] Groq: cancelled (race won).")
        raise
    except aiohttp.ClientError as e:
        log_fn(f"[race] Groq network error: {e}")
        return None
    except Exception as e:
        log_fn(f"[race] Groq error: {type(e).__name__}: {e}")
        return None


async def _call_openrouter(
    session: aiohttp.ClientSession,
    prompt: str,
    log_fn=print,
) -> str | None:
    """OpenRouter API caller. Returns valid Persian or None."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        log_fn("[race] OpenRouter: OPENROUTER_API_KEY not set — skipping.")
        return None

    payload = {
        "model":       OPENROUTER_MODEL,
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": OPENROUTER_TEMPERATURE,
        "max_tokens":  OPENROUTER_MAX_TOKENS,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://t.me/irfashionnews",
        "X-Title":       "IrFashionNews",
    }
    try:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                log_fn(f"[race] OpenRouter HTTP {resp.status}: {body[:120]}")
                return None
            data   = await resp.json()
            result = _extract_openai_content(data)
            valid  = _is_valid_persian(result)
            log_fn(
                f"[race] OpenRouter responded: "
                f"{len(result or '')}ch | valid={valid}"
            )
            return result if valid else None

    except asyncio.CancelledError:
        log_fn("[race] OpenRouter: cancelled (race won).")
        raise
    except aiohttp.ClientError as e:
        log_fn(f"[race] OpenRouter network error: {e}")
        return None
    except Exception as e:
        log_fn(f"[race] OpenRouter error: {type(e).__name__}: {e}")
        return None


async def _parallel_ai_race(
    prompt: str,
    race_timeout: int = AI_RACE_TIMEOUT,
    log_fn=print,
) -> str | None:
    """
    First-response-wins parallel AI race.

    Fires Groq and OpenRouter simultaneously at t=0.
    Returns first valid Persian response. Cancels losers.

    Args:
        prompt:        Fully-formed prompt string
        race_timeout:  Overall race deadline in seconds
        log_fn:        Logging function (context.log or print)

    Returns:
        str  → valid Persian text from winning provider
        None → all providers failed or timed out
    """
    if not prompt or not prompt.strip():
        log_fn("[race] Empty prompt — skipping.")
        return None

    result_queue: asyncio.Queue[str | None] = asyncio.Queue()

    providers = [
        ("Groq",       _call_groq),
        ("OpenRouter", _call_openrouter),
    ]
    total = len(providers)

    async def _worker(name: str, caller_fn, session: aiohttp.ClientSession):
        try:
            result = await caller_fn(session, prompt, log_fn)
            await result_queue.put(result)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_fn(f"[race] _worker({name}) unhandled: {e}")
            await result_queue.put(None)

    connector = aiohttp.TCPConnector(limit=10, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:

        tasks: list[asyncio.Task] = [
            asyncio.create_task(
                _worker(name, fn, session),
                name=f"race_{name.lower()}",
            )
            for name, fn in providers
        ]

        log_fn(
            f"[race] ★ {total} providers fired simultaneously "
            f"(timeout={race_timeout}s)."
        )

        winner:     str | None = None
        none_count: int        = 0

        try:
            async with asyncio.timeout(race_timeout):
                while none_count < total:
                    result = await result_queue.get()
                    if _is_valid_persian(result):
                        winner = result
                        log_fn(
                            f"[race] ✓ Winner: {len(winner)}ch. "
                            f"Cancelling remaining."
                        )
                        break
                    else:
                        none_count += 1
                        log_fn(
                            f"[race] ✗ Invalid "
                            f"({none_count}/{total} failed)."
                        )
        except TimeoutError:
            log_fn(f"[race] ✗ Timed out after {race_timeout}s.")

        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    status = f"{len(winner)}ch winner" if winner else "all failed"
    log_fn(f"[race] ═══ Complete: {status}. ═══")
    return winner


async def _run_three_races(
    body_prompt: str,
    title_prompt: str,
    tip_prompt: str,
    log_fn=print,
) -> tuple[str | None, str | None, str | None]:
    """
    Run body, title, and tip translation races concurrently.
    All three races fire their internal provider pairs at t=0.
    Returns (body_fa, title_fa, tip_fa).
    """
    log_fn("[ai] Starting 3 parallel AI races (body + title + tip)...")
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                _parallel_ai_race(body_prompt,  AI_RACE_TIMEOUT,  log_fn),
                _parallel_ai_race(title_prompt, AI_TITLE_TIMEOUT, log_fn),
                _parallel_ai_race(tip_prompt,   AI_TIP_TIMEOUT,   log_fn),
                return_exceptions=True,
            ),
            timeout=AI_RACE_TIMEOUT + 10,
        )
    except asyncio.TimeoutError:
        log_fn("[ai] Outer 3-race timeout.")
        return None, None, None

    body_fa  = results[0] if isinstance(results[0], str) else None
    title_fa = results[1] if isinstance(results[1], str) else None
    tip_fa   = results[2] if isinstance(results[2], str) else None

    log_fn(
        f"[ai] Results — "
        f"body={len(body_fa or '')}ch | "
        f"title={len(title_fa or '')}ch | "
        f"tip={len(tip_fa or '')}ch"
    )
    return body_fa, title_fa, tip_fa


# ═══════════════════════════════════════════════════════════
# SECTION 5 — MEHRJAMEH CAPTION BUILDER
#
# Format:
#   <b>عنوان فارسی</b>
#   ─────────────
#   مد و فشن ایرانی
#
#   متن خلاصه خبر...
#
#   💡 نکته استایلی
#
#   EMOJI  کانال مد و فشن ایرانی
#
#   #hashtag1 #hashtag2 ...    ← ALWAYS LAST
# ═══════════════════════════════════════════════════════════

def _build_mehrjameh_caption(
    title_fa: str,
    body_fa: str,
    tip_fa: str,
    hashtags: list[str],
    category: str,
) -> str:
    """
    Build Telegram-ready HTML caption in Mehrjameh editorial format.
    Guarantees len(output) <= CAPTION_MAX.
    Hashtags always last.
    """
    def _esc(t: str) -> str:
        return (
            t.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    CATEGORY_EMOJI = {
        "runway":         "👗",
        "brand":          "🏷️",
        "business":       "📊",
        "beauty":         "💄",
        "sustainability": "♻️",
        "celebrity":      "⭐",
        "trend":          "🔥",
        "general":        "🌐",
    }
    emoji     = CATEGORY_EMOJI.get(category, "🌐")
    hash_line = " ".join(hashtags)

    # Fixed parts (never trimmed)
    header    = f"<b>{_esc(title_fa.strip())}</b>"
    sep       = "─────────────\nمد و فشن ایرانی"
    tip_block = f"💡 {_esc(tip_fa.strip())}" if tip_fa else ""
    footer    = f"{emoji}  <i>کانال مد و فشن ایرانی</i>"

    # Calculate body budget
    fixed_items = [header, sep]
    if tip_block:
        fixed_items.append(tip_block)
    fixed_items.append(footer)
    if hash_line:
        fixed_items.append(hash_line)

    # Budget = total - fixed - separators (2 chars each \n\n)
    separators  = (len(fixed_items)) * 2  # \n\n between each
    fixed_len   = sum(len(p) for p in fixed_items) + separators
    body_budget = CAPTION_MAX - fixed_len - 4

    safe_body = _esc(body_fa.strip())

    if body_budget <= 10:
        safe_body = ""
        header    = f"<b>{_esc(title_fa.strip())[:80]}</b>"
    elif len(safe_body) > body_budget:
        safe_body = safe_body[:body_budget - 1] + "…"

    # Assemble
    parts = [header, sep]
    if safe_body:
        parts.append(safe_body)
    if tip_block:
        parts.append(tip_block)
    parts.append(footer)
    if hash_line:
        parts.append(hash_line)

    caption = "\n\n".join(parts)

    # Hard guard
    if len(caption) > CAPTION_MAX:
        caption = caption[:CAPTION_MAX - 1] + "…"

    return caption


# ═══════════════════════════════════════════════════════════
# SECTION 6 — MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

async def main(event=None, context=None):
    """
    Main Appwrite Cloud Function entry point.
    Uses context.log() when available, falls back to print().
    """
    # ── Logging setup ──────────────────────────────────────
    log   = context.log   if context and hasattr(context, "log")   else print
    error = context.error if context and hasattr(context, "error") else print

    log("═══ FashionBot v11.0 (Mehrjameh) started ═══")

    loop       = asyncio.get_running_loop()
    start_time = loop.time()

    def elapsed() -> str:
        return f"{loop.time() - start_time:.1f}"

    # ── Environment ────────────────────────────────────────
    token             = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id           = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()
    appwrite_endpoint = os.environ.get(
        "APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1"
    )
    appwrite_project  = os.environ.get("APPWRITE_PROJECT_ID", "").strip()
    appwrite_key      = os.environ.get("APPWRITE_API_KEY", "").strip()
    database_id       = os.environ.get("APPWRITE_DATABASE_ID", "").strip()

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
        error(f"Missing env vars: {missing}")
        return {"status": "error", "missing_vars": missing}

    if not any([
        os.environ.get("GROQ_API_KEY", ""),
        os.environ.get("OPENROUTER_API_KEY", ""),
    ]):
        error("No AI API keys found. Translation will fail.")

    # ── Clients ────────────────────────────────────────────
    bot       = Bot(token=token)
    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)
    sdk_mode  = "new" if hasattr(databases, "list_rows") else "legacy"
    log(f"SDK mode: {sdk_mode}")

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)
    current_hour   = now.hour
    is_peak        = current_hour in PEAK_HOURS_UTC
    log(
        f"UTC={current_hour}h | "
        f"Peak={'YES +' + str(PEAK_HOUR_BONUS) if is_peak else 'no'}"
    )

    # ── Load posted-only titles for fuzzy dedup ────────────
    recent_titles = _load_recent_titles_posted_only(
        databases, database_id, COLLECTION_ID, sdk_mode,
        FUZZY_LOOKBACK_COUNT, log,
    )
    log(f"[{elapsed()}s] {len(recent_titles)} posted titles loaded.")

    # ════════════════════════════════════════════════════════
    # PHASE 1 — RSS SCAN + SCORE
    # ════════════════════════════════════════════════════════
    log(f"[{elapsed()}s] Phase 1: Scanning {len(RSS_FEEDS)} feeds...")
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
                log_fn=log,
            ),
            timeout=FEEDS_SCAN_TIMEOUT,
        )
    except asyncio.TimeoutError:
        error(f"[{elapsed()}s] Feed scan timed out.")
        candidate = None

    if not candidate:
        log(f"[{elapsed()}s] No new article found.")
        return {"status": "success", "posted": False}

    title    = candidate["title"]
    link     = candidate["link"]
    desc     = candidate["description"]
    feed_url = candidate["feed_url"]
    pub_date = candidate["pub_date"]
    entry    = candidate["entry"]
    score    = candidate["score"]
    category = candidate["category"]

    content_hash = _make_content_hash(title)
    title_hash   = _make_title_hash(title, feed_url)
    domain_hash  = _make_domain_hash(_get_domain(link))

    log(
        f"[{elapsed()}s] Selected: "
        f"score={score} cat={category} | {title[:65]}"
    )

    # ════════════════════════════════════════════════════════
    # PHASE 2 — LIGHT DEDUP (posted=true only)
    # ════════════════════════════════════════════════════════
    log(f"[{elapsed()}s] Phase 2: Light dedup (posted-only)...")
    is_dup, dup_reason = _light_duplicate_check(
        databases, database_id, COLLECTION_ID,
        link, content_hash, title_hash, sdk_mode, log,
    )
    if is_dup:
        log(f"[{elapsed()}s] Confirmed posted dup ({dup_reason}). Skip.")
        return {
            "status":  "success",
            "posted":  False,
            "reason":  dup_reason,
            "article": title[:80],
        }

    # ════════════════════════════════════════════════════════
    # PHASE 3 — PARALLEL SCRAPE
    # ════════════════════════════════════════════════════════
    log(f"[{elapsed()}s] Phase 3: Scraping text + images...")
    try:
        text_result, image_result = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(None, _scrape_text, link, log),
                loop.run_in_executor(None, _scrape_images, link, entry, log),
                return_exceptions=True,
            ),
            timeout=SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        error(f"[{elapsed()}s] Scrape timed out.")
        text_result  = None
        image_result = []

    full_text  = text_result  if isinstance(text_result,  str)  else None
    image_urls = image_result if isinstance(image_result, list) else []
    content    = _select_content(full_text, desc, title)

    log(
        f"[{elapsed()}s] "
        f"Text={'scraped' if full_text else 'fallback'} "
        f"({len(content)}ch) | Images={len(image_urls)}"
    )

    if len(content) < MIN_CONTENT_CHARS:
        error(f"[{elapsed()}s] Thin content ({len(content)}ch). Abort.")
        return {
            "status": "skipped",
            "reason": f"thin_content",
            "posted": False,
        }

    # ════════════════════════════════════════════════════════
    # PHASE 4 — PARALLEL AI RACES
    # Three races fire concurrently:
    #   Race A: body summary (Groq vs OpenRouter)
    #   Race B: title translation (Groq vs OpenRouter)
    #   Race C: styling tip (Groq vs OpenRouter)
    # ════════════════════════════════════════════════════════
    log(f"[{elapsed()}s] Phase 4: Parallel AI races (body+title+tip)...")

    body_prompt  = _PROMPT_BODY.format(input_text=content[:3000])
    title_prompt = _PROMPT_TITLE.format(input_text=title[:500])
    tip_prompt   = _PROMPT_TIP.format(input_text=content[:1500])

    body_fa, title_fa, tip_fa = await _run_three_races(
        body_prompt, title_prompt, tip_prompt, log_fn=log,
    )

    # Fallbacks
    title_fa = (title_fa or "").strip() or title
    body_fa  = (body_fa  or "").strip() or None
    tip_fa   = (tip_fa   or "").strip() or None

    if not body_fa:
        error(f"[{elapsed()}s] All AI providers failed for body.")
        return {
            "status":     "error",
            "reason":     "translation_failed",
            "posted":     False,
            "article_id": "",
        }

    log(
        f"[{elapsed()}s] "
        f"title_fa={len(title_fa)}ch | "
        f"body_fa={len(body_fa)}ch | "
        f"tip_fa={len(tip_fa or '')}ch"
    )

    # ════════════════════════════════════════════════════════
    # PHASE 5 — BUILD MEHRJAMEH CAPTION
    # ════════════════════════════════════════════════════════
    combined_for_tags = f"{title} {desc} {content[:500]}"
    hashtags = _extract_hashtags_from_text(combined_for_tags)
    caption  = _build_mehrjameh_caption(
        title_fa, body_fa, tip_fa or "", hashtags, category
    )

    log(
        f"[{elapsed()}s] "
        f"Caption={len(caption)}ch | Hashtags={len(hashtags)}"
    )

    # ════════════════════════════════════════════════════════
    # PHASE 6 — SOFT LOCK WRITE
    # Written AFTER AI success, BEFORE Telegram.
    # ════════════════════════════════════════════════════════
    log(f"[{elapsed()}s] Phase 6: Acquiring soft lock...")
    lock_acquired, lock_result = _write_soft_lock(
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
        log_fn=log,
    )

    if not lock_acquired:
        error(f"[{elapsed()}s] Lock not acquired ({lock_result}). Abort.")
        return {
            "status":     "skipped",
            "reason":     f"lock_failed: {lock_result}",
            "posted":     False,
            "article_id": "",
        }

    doc_id = lock_result
    log(f"[{elapsed()}s] Lock acquired. doc_id={doc_id}")

    # ════════════════════════════════════════════════════════
    # PHASE 7 — POST TO TELEGRAM
    # Images sent first, then caption as reply (ordered).
    # ════════════════════════════════════════════════════════
    log(f"[{elapsed()}s] Phase 7: Posting to Telegram...")
    posted     = False
    post_error = ""
    try:
        posted = await asyncio.wait_for(
            _post_to_telegram(bot, chat_id, caption, image_urls, log),
            timeout=TELEGRAM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        post_error = "telegram_timeout"
        error(f"[{elapsed()}s] Telegram timed out.")
    except Exception as e:
        post_error = str(e)[:200]
        error(f"[{elapsed()}s] Telegram error: {e}")

    # ════════════════════════════════════════════════════════
    # PHASE 8 — UPDATE DB STATE
    # posted=true ONLY written after confirmed Telegram success.
    # ════════════════════════════════════════════════════════
    if posted:
        _mark_posted(databases, database_id, COLLECTION_ID, doc_id, sdk_mode, log)
        log(f"[{elapsed()}s] DB → status=posted, posted=true. ✓")
    else:
        _mark_failed(
            databases, database_id, COLLECTION_ID, doc_id, sdk_mode,
            reason=post_error or "telegram_post_failed",
            log_fn=log,
        )
        error(f"[{elapsed()}s] DB → status=failed (retryable).")

    result = {
        "images":     image_urls,
        "caption":    caption,
        "article_id": doc_id,
        "status":     "success" if posted else "failed",
        "title":      title[:80],
        "category":   category,
        "score":      score,
    }

    log(
        f"═══ v11.0 done in {elapsed()}s | "
        f"{'POSTED ✓' if posted else 'FAILED ✗'} ═══"
    )
    return result


# ═══════════════════════════════════════════════════════════
# SECTION 7 — FEED SCANNING & CANDIDATE SELECTION
# ═══════════════════════════════════════════════════════════

async def _find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, now, recent_titles, is_peak,
    log_fn=print,
):
    """
    Scan all RSS feeds in parallel, score articles,
    apply posted-only duplicate checks (L1-L3), return
    the highest-scoring genuinely new article.
    """
    loop  = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, _fetch_feed, url, time_threshold, log_fn)
        for url in feeds
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_candidates = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log_fn(f"[feed] Error ({feeds[i][:45]}): {result}")
            continue
        if result:
            all_candidates.extend(result)

    log_fn(f"[feed] {len(all_candidates)} articles collected.")
    if not all_candidates:
        return None

    for c in all_candidates:
        c["score"]    = _score_article(c, now, is_peak)
        c["category"] = _detect_category(c["title"], c["description"])

    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    log_fn("[feed] Top 5 candidates:")
    for c in all_candidates[:5]:
        log_fn(
            f"  [{c['score']:>3}] [{c['category']:<14}] "
            f"{c['title'][:58]}"
        )

    recent_domain_hashes = _load_recent_domain_hashes(
        databases, database_id, collection_id, sdk_mode, log_fn
    )
    seen_domains_this_run: set[str] = set()

    for c in all_candidates:
        link         = c["link"]
        title        = c["title"]
        feed_url     = c["feed_url"]
        domain       = _get_domain(link)
        content_hash = _make_content_hash(title)
        title_hash   = _make_title_hash(title, feed_url)
        domain_hash  = _make_domain_hash(domain)

        # L1: Exact URL — posted=true only
        r = _query_posted_field(
            databases, database_id, collection_id,
            "link", link[:DB_LINK_MAX], sdk_mode, log_fn,
        )
        if r is True:
            log_fn(f"[SKIP] L1(posted): {title[:58]}")
            continue

        # L2: Content hash — posted=true only
        r = _query_posted_field(
            databases, database_id, collection_id,
            "content_hash", content_hash, sdk_mode, log_fn,
        )
        if r is True:
            log_fn(f"[SKIP] L2(posted): {title[:58]}")
            continue

        # L2b: Title hash — posted=true only
        r = _query_posted_field(
            databases, database_id, collection_id,
            "title_hash", title_hash, sdk_mode, log_fn,
        )
        if r is True:
            log_fn(f"[SKIP] L2b(posted): {title[:58]}")
            continue

        # L3: Fuzzy — against posted=true titles only
        is_fuzz, matched, fuzz_score = _fuzzy_duplicate(
            title, recent_titles
        )
        if is_fuzz:
            log_fn(
                f"[SKIP] L3 fuzzy={fuzz_score:.2f}: "
                f"{title[:45]} ≈ {(matched or '')[:35]}"
            )
            continue

        # L4b: Domain — informational only
        if domain_hash in recent_domain_hashes:
            log_fn(f"[INFO] L4b: domain {domain} seen recently.")

        # L4a: One domain per run
        if domain in seen_domains_this_run:
            log_fn(f"[SKIP] L4a domain/run ({domain}): {title[:58]}")
            continue

        seen_domains_this_run.add(domain)
        log_fn(f"[PASS] fuzz={fuzz_score:.2f}: {title[:58]}")
        return c

    log_fn("[feed] All candidates exhausted.")
    return None


def _fetch_feed(
    feed_url: str,
    time_threshold: datetime,
    log_fn=print,
) -> list:
    """Parse one RSS feed. Returns list of candidate dicts."""
    import socket
    try:
        old = socket.getdefaulttimeout()
        socket.setdefaulttimeout(FEED_FETCH_TIMEOUT)
        feed = feedparser.parse(feed_url)
        socket.setdefaulttimeout(old)
    except Exception as e:
        log_fn(f"[feed] feedparser ({feed_url[:45]}): {e}")
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
        desc = re.sub(r"\s+",     " ", desc).strip()
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


def _score_article(
    candidate: dict,
    now: datetime,
    is_peak: bool = False,
) -> int:
    score     = 0
    age_hours = (now - candidate["pub_date"]).total_seconds() / 3600
    combined  = (candidate["title"] + " " + candidate["description"]).lower()

    if age_hours <= 3:
        score += SCORE_RECENCY_MAX
    elif age_hours <= ARTICLE_AGE_HOURS:
        ratio  = 1 - (age_hours - 3) / (ARTICLE_AGE_HOURS - 3)
        score += int(SCORE_RECENCY_MAX * ratio)

    title_lower = candidate["title"].lower()
    desc_lower  = candidate["description"].lower()
    matched     = 0
    for kw in TREND_KEYWORDS:
        if matched >= 3: break
        if kw in title_lower:
            score += SCORE_TITLE_KEYWORD; matched += 1
        elif kw in desc_lower:
            score += SCORE_DESC_KEYWORD;  matched += 1

    if _extract_rss_image(candidate["entry"]):
        score += SCORE_HAS_IMAGE
    if len(candidate["description"]) > 200:
        score += SCORE_DESC_LENGTH
    if is_peak:
        score += PEAK_HOUR_BONUS

    fashion_hits = sum(
        1 for kw in FASHION_RELEVANCE_KEYWORDS if kw in combined
    )
    if fashion_hits >= 2:
        score += SCORE_FASHION_RELEVANCE
    elif fashion_hits == 1:
        score += SCORE_FASHION_RELEVANCE // 2
    else:
        score = max(0, score - 30)

    return min(score, 100)


def _detect_category(title: str, description: str) -> str:
    combined = (title + " " + description).lower()
    for cat, keywords in CONTENT_CATEGORIES.items():
        for kw in keywords:
            if kw in combined:
                return cat
    return "general"


def _extract_hashtags_from_text(text: str) -> list[str]:
    lower    = text.lower()
    hashtags = []
    seen: set[str] = set()
    for keyword, tags in HASHTAG_MAP.items():
        if keyword in lower and keyword not in seen:
            hashtags.append(tags)
            seen.add(keyword)
            if len(hashtags) >= MAX_HASHTAGS:
                break
    return hashtags


# ═══════════════════════════════════════════════════════════
# SECTION 8 — DEDUPLICATION (state-aware, posted=true only)
# ═══════════════════════════════════════════════════════════

def _light_duplicate_check(
    databases,
    database_id: str,
    collection_id: str,
    link: str,
    content_hash: str,
    title_hash: str,
    sdk_mode: str,
    log_fn=print,
) -> tuple[bool, str]:
    """
    Pre-AI check. Blocks ONLY confirmed posted=true articles.
    locked/failed/missing → safe to proceed.
    DB errors → treated as safe (non-blocking).
    """
    for field, value in [
        ("link",         link[:DB_LINK_MAX]),
        ("content_hash", content_hash),
        ("title_hash",   title_hash),
    ]:
        r = _query_posted_field(
            databases, database_id, collection_id,
            field, value, sdk_mode, log_fn,
        )
        if r is True:
            return True, f"posted_{field}"
    return False, ""


def _query_posted_field(
    databases,
    database_id: str,
    collection_id: str,
    field: str,
    value: str,
    sdk_mode: str,
    log_fn=print,
) -> bool | None:
    """
    Query whether a record exists with field=value AND posted=true.
    Returns True (dup), False (safe), None (DB error → safe).
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            queries = [
                Query.equal(field, value),
                Query.equal("posted", True),
                Query.limit(1),
            ]
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
            log_fn(f"[dedup] _query_posted_field ({field}): {e.message}")
            return None
        except Exception as e:
            log_fn(f"[dedup] _query_posted_field ({field}): {e}")
            return None


def _load_recent_titles_posted_only(
    databases,
    database_id: str,
    collection_id: str,
    sdk_mode: str,
    limit: int,
    log_fn=print,
) -> list:
    """Load recent posted=true titles for fuzzy matching."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            queries = [
                Query.equal("posted", True),
                Query.limit(limit),
                Query.order_desc("$createdAt"),
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
            return [
                (d.get("title", ""), _normalize_tokens(d.get("title", "")))
                for d in docs if d.get("title")
            ]
        except Exception as e:
            log_fn(f"[dedup] _load_recent_titles: {e}")
            return []


def _load_recent_domain_hashes(
    databases,
    database_id: str,
    collection_id: str,
    sdk_mode: str,
    log_fn=print,
) -> set:
    """Load domain hashes from posted=true records within DOMAIN_DEDUP_HOURS."""
    cutoff     = datetime.now(timezone.utc) - timedelta(hours=DOMAIN_DEDUP_HOURS)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            queries = [
                Query.greater_than("$createdAt", cutoff_str),
                Query.equal("posted", True),
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
            log_fn(f"[dedup] _load_recent_domain_hashes: {e}")
            return set()


# ═══════════════════════════════════════════════════════════
# SECTION 9 — SOFT LOCK & STATE TRANSITIONS
# ═══════════════════════════════════════════════════════════

def _write_soft_lock(
    databases,
    database_id: str,
    collection_id: str,
    link: str,
    title: str,
    feed_url: str,
    pub_date,
    source_type: str,
    sdk_mode: str,
    title_hash: str,
    content_hash: str,
    category: str,
    trend_score: int,
    post_hour: int,
    domain_hash: str,
    log_fn=print,
) -> tuple[bool, str]:
    """
    Acquire distributed soft lock for this article.

    Decision tree:
      posted=true              → real dup       → (False, reason)
      status=locked, TTL fresh → active lock    → (False, reason)
      status=locked, TTL stale → crash recovery → delete + write new
      status=failed            → retry eligible  → delete + write new
      unknown state            → stale           → delete + write new
      no record                → new article     → write lock

    Returns (True, doc_id) or (False, reason).
    """
    now     = datetime.now(timezone.utc)
    now_iso = now.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")

    existing = _get_existing_record(
        databases, database_id, collection_id, link, sdk_mode, log_fn
    )

    if existing is not None:
        existing_status = existing.get("status", "")
        existing_posted = existing.get("posted", False)
        existing_doc_id = existing["$id"]
        locked_at_str   = existing.get("locked_at", "")

        if existing_posted is True or existing_status == STATUS_POSTED:
            log_fn("[lock] Already posted — real duplicate.")
            return False, "already_posted"

        if existing_status == STATUS_LOCKED and locked_at_str:
            try:
                locked_at = datetime.fromisoformat(
                    locked_at_str.replace("Z", "+00:00")
                )
                age = (now - locked_at).total_seconds()
                if age < LOCK_TTL_SECONDS:
                    log_fn(
                        f"[lock] Active lock (age={age:.0f}s "
                        f"< TTL={LOCK_TTL_SECONDS}s). Skip."
                    )
                    return False, "active_lock"
                else:
                    log_fn(f"[lock] Stale lock (age={age:.0f}s). Recovering.")
                    _delete_record(
                        databases, database_id, collection_id,
                        existing_doc_id, sdk_mode, log_fn,
                    )
            except Exception as e:
                log_fn(f"[lock] TTL parse error: {e}. Deleting stale.")
                _delete_record(
                    databases, database_id, collection_id,
                    existing_doc_id, sdk_mode, log_fn,
                )
        elif existing_status == STATUS_FAILED:
            log_fn("[lock] Failed record — retrying. Deleting old.")
            _delete_record(
                databases, database_id, collection_id,
                existing_doc_id, sdk_mode, log_fn,
            )
        else:
            log_fn(
                f"[lock] Unknown status='{existing_status}' — "
                f"treating as stale."
            )
            _delete_record(
                databases, database_id, collection_id,
                existing_doc_id, sdk_mode, log_fn,
            )

    # Write new lock record
    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)

    payload = {
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
        "status":       STATUS_LOCKED,
        "posted":       False,
        "locked_at":    now_iso,
        "posted_at":    "",
        "fail_reason":  "",
    }

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            if sdk_mode == "new":
                doc = databases.create_row(
                    database_id=database_id,
                    collection_id=collection_id,
                    row_id="unique()",
                    data=payload,
                )
            else:
                doc = databases.create_document(
                    database_id=database_id,
                    collection_id=collection_id,
                    document_id="unique()",
                    data=payload,
                )
            doc_id = doc.get("$id") or doc.get("id", "")
            log_fn(f"[lock] ✓ Soft lock acquired. doc_id={doc_id}")
            return True, doc_id
        except AppwriteException as e:
            msg = str(e.message).lower()
            if "already exists" in msg or e.code in (409, 400):
                log_fn("[lock] Race condition — another instance won lock.")
                return False, "race_lost"
            log_fn(f"[lock] DB write error: {e.message}")
            return False, f"db_error: {e.message}"
        except Exception as e:
            log_fn(f"[lock] DB write error: {e}")
            return False, f"db_error: {e}"


def _mark_posted(
    databases,
    database_id: str,
    collection_id: str,
    doc_id: str,
    sdk_mode: str,
    log_fn=print,
) -> bool:
    """Mark record as successfully posted. posted=true written ONLY here."""
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    return _update_record(
        databases, database_id, collection_id, doc_id, sdk_mode,
        {"status": STATUS_POSTED, "posted": True, "posted_at": now_iso},
        log_fn,
    )


def _mark_failed(
    databases,
    database_id: str,
    collection_id: str,
    doc_id: str,
    sdk_mode: str,
    reason: str,
    log_fn=print,
) -> bool:
    """Mark record as failed. Retryable on next execution."""
    return _update_record(
        databases, database_id, collection_id, doc_id, sdk_mode,
        {
            "status":      STATUS_FAILED,
            "posted":      False,
            "fail_reason": reason[:DB_REASON_MAX],
        },
        log_fn,
    )


def _update_record(
    databases,
    database_id: str,
    collection_id: str,
    doc_id: str,
    sdk_mode: str,
    fields: dict,
    log_fn=print,
) -> bool:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            if sdk_mode == "new":
                databases.update_row(
                    database_id=database_id,
                    collection_id=collection_id,
                    row_id=doc_id,
                    data=fields,
                )
            else:
                databases.update_document(
                    database_id=database_id,
                    collection_id=collection_id,
                    document_id=doc_id,
                    data=fields,
                )
            log_fn(f"[db] {doc_id} → {list(fields.keys())}")
            return True
        except Exception as e:
            log_fn(f"[db] Update failed ({doc_id}): {e}")
            return False


def _get_existing_record(
    databases,
    database_id: str,
    collection_id: str,
    link: str,
    sdk_mode: str,
    log_fn=print,
) -> dict | None:
    """Fetch any existing record by URL, regardless of status."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            queries = [
                Query.equal("link", link[:DB_LINK_MAX]),
                Query.limit(1),
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
            return docs[0] if docs else None
        except Exception as e:
            log_fn(f"[db] _get_existing_record: {e}")
            return None


def _delete_record(
    databases,
    database_id: str,
    collection_id: str,
    doc_id: str,
    sdk_mode: str,
    log_fn=print,
) -> None:
    """Delete a stale lock or failed record."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        try:
            if sdk_mode == "new":
                databases.delete_row(
                    database_id=database_id,
                    collection_id=collection_id,
                    row_id=doc_id,
                )
            else:
                databases.delete_document(
                    database_id=database_id,
                    collection_id=collection_id,
                    document_id=doc_id,
                )
            log_fn(f"[db] Deleted stale record: {doc_id}")
        except Exception as e:
            log_fn(f"[db] Delete failed ({doc_id}): {e}")


# ═══════════════════════════════════════════════════════════
# SECTION 10 — HASH & FUZZY UTILITIES
# ═══════════════════════════════════════════════════════════

def _make_content_hash(title: str) -> str:
    tokens = _normalize_tokens(title)
    return hashlib.sha256(
        " ".join(sorted(tokens)).encode("utf-8")
    ).hexdigest()

def _make_title_hash(title: str, feed_url: str) -> str:
    raw = (title.lower().strip() + feed_url[:50]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def _make_domain_hash(domain: str) -> str:
    return hashlib.sha256(
        domain.encode("utf-8")
    ).hexdigest()[:DB_DOMAIN_HASH_MAX]

def _normalize_tokens(title: str) -> frozenset:
    title  = re.sub(r"[^a-z0-9\s]", " ", title.lower())
    return frozenset(
        t for t in title.split()
        if t not in TITLE_STOP_WORDS and len(t) >= 2
    )

def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)

def _fuzzy_duplicate(
    title: str, recent_titles: list
) -> tuple[bool, str | None, float]:
    if not recent_titles: return False, None, 0.0
    incoming   = _normalize_tokens(title)
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

def _get_domain(url: str) -> str:
    try:
        parts = urlparse(url).netloc.replace("www.", "").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else url[:30]
    except Exception:
        return url[:30]


# ═══════════════════════════════════════════════════════════
# SECTION 11 — SCRAPING
# ═══════════════════════════════════════════════════════════

def _select_content(
    scraped_text: str | None,
    description: str,
    title: str,
) -> str:
    if scraped_text and len(scraped_text) >= MIN_CONTENT_CHARS:
        return scraped_text[:MAX_SCRAPED_CHARS]
    if description and len(description) >= MIN_CONTENT_CHARS:
        return description[:MAX_RSS_CHARS]
    return title


def _scrape_text(url: str, log_fn=print) -> str | None:
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
            "script", "style", "nav", "footer", "header", "aside",
            "form", "iframe", "noscript", "figcaption",
            "button", "input", "select", "svg",
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
        area      = body or soup
        TARGET    = {"p", "h2", "h3", "h4", "li"}
        lines     = []
        seen_keys: set[str] = set()
        for el in area.find_all(TARGET):
            raw = re.sub(r"\s+", " ", el.get_text(" ").strip())
            if len(raw) < 25: continue
            key = raw.lower()[:80]
            if key in seen_keys: continue
            seen_keys.add(key)
            tag   = el.name
            lower = raw.lower()
            if tag in ("h2", "h3", "h4"):
                lines.append(f"▌ {raw}")
            elif tag == "li":
                if len(raw) < 30: continue
                if any(p in lower for p in BOILERPLATE_PATTERNS): continue
                lines.append(f"• {raw}")
            else:
                if any(p in lower for p in BOILERPLATE_PATTERNS): continue
                lines.append(raw)
        text = "\n".join(lines).strip()
        return text[:MAX_SCRAPED_CHARS] if len(text) >= 100 else None
    except requests.exceptions.Timeout:
        log_fn(f"[scrape] Timeout: {url[:60]}")
        return None
    except requests.exceptions.HTTPError as e:
        log_fn(f"[scrape] HTTP {e.response.status_code}: {url[:60]}")
        return None
    except Exception as e:
        log_fn(f"[scrape] Error: {e}")
        return None


def _scrape_images(url: str, rss_entry, log_fn=print) -> list:
    images: list[str] = []
    seen:   set[str]  = set()

    def _add(img_url: str):
        if not img_url: return
        img_url = img_url.strip()
        if not img_url.startswith("http") or img_url in seen: return
        lower = img_url.lower()
        if any(b in lower for b in IMAGE_BLOCKLIST): return
        base     = lower.split("?")[0]
        has_ext  = any(base.endswith(e) for e in IMAGE_EXTENSIONS)
        has_word = any(
            w in lower
            for w in ["image", "photo", "img", "picture", "media", "cdn"]
        )
        if not has_ext and not has_word: return
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
                img.get("data-src") or img.get("data-original")
                or img.get("data-lazy-src") or img.get("src") or ""
            )
            _add(src)
            if len(images) >= MAX_IMAGES: break
        if len(images) < MAX_IMAGES:
            for source in area.find_all("source"):
                srcset = source.get("srcset", "")
                if srcset:
                    _add(srcset.split(",")[0].strip().split(" ")[0])
                if len(images) >= MAX_IMAGES: break
    except Exception as e:
        log_fn(f"[scrape] Image error: {e}")

    if len(images) < MAX_IMAGES:
        rss_img = _extract_rss_image(rss_entry)
        if rss_img:
            _add(rss_img)

    log_fn(f"[scrape] Images collected: {len(images)}")
    return images[:MAX_IMAGES]


def _extract_rss_image(entry) -> str | None:
    if entry is None: return None
    try:
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
                    if src.startswith("http"): return src
        if hasattr(entry, "content") and entry.content:
            html = entry.content[0].get("value", "")
            if html:
                img = BeautifulSoup(html, "html.parser").find("img")
                if img:
                    src = img.get("src", "")
                    if src.startswith("http"): return src
    except Exception:
        pass
    return None


# ═══════════════════════════════════════════════════════════
# SECTION 12 — TELEGRAM POSTING
# Images sent first as media group (anchor).
# Caption sent as reply to anchor (protocol-level ordering).
# ═══════════════════════════════════════════════════════════

async def _post_to_telegram(
    bot: Bot,
    chat_id: str,
    caption: str,
    image_urls: list,
    log_fn=print,
) -> bool:
    anchor_msg_id = None
    posted        = False

    # ── Step 1: Send images ──
    if len(image_urls) >= 2:
        try:
            media_group   = [
                InputMediaPhoto(media=url)
                for url in image_urls[:MAX_IMAGES]
            ]
            sent_msgs     = await bot.send_media_group(
                chat_id=chat_id, media=media_group,
                disable_notification=True,
            )
            anchor_msg_id = sent_msgs[-1].message_id
            log_fn(
                f"[tg] ① Album: {len(sent_msgs)} images. "
                f"anchor={anchor_msg_id}"
            )
        except Exception as e:
            log_fn(f"[tg] ① Album failed: {str(e)[:120]}")
            if image_urls:
                try:
                    sent          = await bot.send_photo(
                        chat_id=chat_id, photo=image_urls[0],
                        disable_notification=True,
                    )
                    anchor_msg_id = sent.message_id
                    log_fn(f"[tg] ① Fallback photo. anchor={anchor_msg_id}")
                except Exception as e2:
                    log_fn(f"[tg] ① Photo fallback failed: {str(e2)[:80]}")

    elif len(image_urls) == 1:
        try:
            sent          = await bot.send_photo(
                chat_id=chat_id, photo=image_urls[0],
                disable_notification=True,
            )
            anchor_msg_id = sent.message_id
            log_fn(f"[tg] ① Single photo. anchor={anchor_msg_id}")
        except Exception as e:
            log_fn(f"[tg] ① Single photo failed: {str(e)[:120]}")
    else:
        log_fn("[tg] ① No images — caption standalone.")

    # ── Step 2: Delay ──
    if anchor_msg_id is not None:
        log_fn(f"[tg] ② Waiting {ALBUM_CAPTION_DELAY}s...")
        await asyncio.sleep(ALBUM_CAPTION_DELAY)

    # ── Step 3: Send caption (reply to anchor) ──
    try:
        kwargs: dict = {
            "chat_id":              chat_id,
            "text":                 caption,
            "parse_mode":           "HTML",
            "link_preview_options": LinkPreviewOptions(is_disabled=True),
            "disable_notification": True,
        }
        if anchor_msg_id is not None:
            kwargs["reply_to_message_id"] = anchor_msg_id
        await bot.send_message(**kwargs)
        log_fn(
            f"[tg] ③ Caption sent "
            f"({'reply_to=' + str(anchor_msg_id) if anchor_msg_id else 'standalone'})."
        )
        posted = True
    except Exception as e:
        log_fn(f"[tg] ③ Caption failed: {str(e)[:120]}")
        return False

    # ── Step 4: Sticker ──
    if posted and FASHION_STICKERS:
        await asyncio.sleep(STICKER_DELAY)
        try:
            await bot.send_sticker(
                chat_id=chat_id,
                sticker=random.choice(FASHION_STICKERS),
                disable_notification=True,
            )
            log_fn("[tg] ④ Sticker sent.")
        except Exception as e:
            log_fn(f"[tg] ④ Sticker failed (non-fatal): {str(e)[:80]}")

    return posted


# ═══════════════════════════════════════════════════════════
# SECTION 13 — ONE-TIME DB CLEANUP UTILITY
# Run: python main.py --cleanup
# Deletes all records where posted != true.
# ═══════════════════════════════════════════════════════════

def _run_cleanup():
    print("[cleanup] Starting unposted record purge...")
    aw_client = Client()
    aw_client.set_endpoint(
        os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
    )
    aw_client.set_project(os.environ.get("APPWRITE_PROJECT_ID", ""))
    aw_client.set_key(os.environ.get("APPWRITE_API_KEY", ""))
    databases = Databases(aw_client)
    db_id     = os.environ.get("APPWRITE_DATABASE_ID", "")
    col_id    = COLLECTION_ID

    deleted = 0
    kept    = 0
    cursor  = None

    while True:
        queries = [Query.limit(100)]
        if cursor:
            queries.append(Query.cursor_after(cursor))
        try:
            result = databases.list_documents(
                database_id=db_id,
                collection_id=col_id,
                queries=queries,
            )
        except Exception as e:
            print(f"[cleanup] Fetch error: {e}")
            break

        docs = result.get("documents", [])
        if not docs:
            break

        for doc in docs:
            doc_id    = doc["$id"]
            is_posted = doc.get("posted", False) is True

            if not is_posted:
                try:
                    databases.delete_document(
                        database_id=db_id,
                        collection_id=col_id,
                        document_id=doc_id,
                    )
                    print(f"[cleanup] DELETED: {doc.get('title', doc_id)[:60]}")
                    deleted += 1
                except Exception as e:
                    print(f"[cleanup] Delete failed ({doc_id}): {e}")
            else:
                kept += 1

        cursor = docs[-1]["$id"]
        if len(docs) < 100:
            break

    print(f"[cleanup] Done. Deleted={deleted} | Kept={kept}")


# ═══════════════════════════════════════════════════════════
# LOCAL ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if "--cleanup" in sys.argv:
        _run_cleanup()
    elif len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        url = sys.argv[1]
        print(f"[LOCAL] Testing URL: {url}")

        async def _test():
            content = _scrape_text(url) or url
            body_p  = _PROMPT_BODY.format(input_text=content[:3000])
            title_p = _PROMPT_TITLE.format(input_text=url[:200])
            tip_p   = _PROMPT_TIP.format(input_text=content[:1500])
            b, t, tip = await _run_three_races(body_p, title_p, tip_p)
            hashtags  = _extract_hashtags_from_text(content[:500])
            caption   = _build_mehrjameh_caption(
                t or "عنوان", b or "متن", tip or "", hashtags, "general"
            )
            print(f"\n── CAPTION ({len(caption)}ch) ──\n{caption}\n")

        asyncio.run(_test())
    else:
        asyncio.run(main())
