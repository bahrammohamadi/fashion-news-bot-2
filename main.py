# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    10.0 — Parallel AI Translation Engine
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# WHAT CHANGED FROM v9.0:
#   REMOVED:
#     - _extractive_summarize()     (sumy LSA — offline, low quality)
#     - _translate_mymemory()       (MyMemory MT — literal, chunked)
#     - _translate_article()        (sequential wrapper)
#     - _split_chunks()             (chunking utility)
#     - MYMEMORY_* constants        (no longer needed)
#     - SUMMARY_SENTENCES constant  (no longer needed)
#     - sumy imports                (removed dependency)
#
#   ADDED:
#     - parallel_summarize_translate()   (race engine — core feature)
#     - _call_groq()                     (Groq async caller)
#     - _call_openrouter()               (OpenRouter async caller)
#     - _race_worker()                   (result queue worker)
#     - _is_valid_persian()              (response validator)
#     - aiohttp                          (async HTTP — new dependency)
#
#   PIPELINE CHANGE:
#     OLD: scrape → LSA summarize → MyMemory translate (sequential)
#     NEW: scrape → parallel_summarize_translate (concurrent race)
#          Both summarization AND translation happen in one LLM call.
#          First valid Persian response wins. Others are cancelled.
#
# PARALLEL RACE ARCHITECTURE:
#
#   article text
#        │
#        ▼  t=0 (simultaneous)
#   ┌────┴──────────────────────────┐
#   │  asyncio.Task: _call_groq()   │──── fires at t=0
#   │  asyncio.Task: _call_or()     │──── fires at t=0
#   └────┬──────────────────────────┘
#        │
#        │  first valid Persian result → result_queue
#        │  loser tasks → task.cancel()
#        ▼
#   winner returned to pipeline
#   (caption build → DB write → Telegram post)
#
# CONCURRENCY MODEL:
#   - asyncio.Task per provider (true parallel, not sequential)
#   - asyncio.Queue(1) for result handoff
#   - task.cancel() + gather(return_exceptions=True) for cleanup
#   - asyncio.timeout() as outer safety net
#   - aiohttp.ClientSession shared across tasks (connection pool)
#
# NO HARDCODED SECRETS.
# ALL KEYS READ FROM ENVIRONMENT VARIABLES ONLY.
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
import aiohttp          # NEW: replaces requests for async HTTP in race
import requests         # KEPT: still used in sync scrape functions
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from telegram import Bot, InputMediaPhoto, LinkPreviewOptions
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query

# REMOVED IMPORTS (were used by deleted functions):
# from sumy.parsers.plaintext import PlaintextParser
# from sumy.nlp.tokenizers import Tokenizer
# from sumy.summarizers.lsa import LsaSummarizer
# from sumy.nlp.stemmers import Stemmer
# from sumy.utils import get_stop_words

warnings.filterwarnings("ignore", category=DeprecationWarning)


# ═══════════════════════════════════════════════════════════
# SECTION 1 — CONFIGURATION
# ═══════════════════════════════════════════════════════════

# ── Appwrite ──
COLLECTION_ID = "history"
SOURCE_TYPE   = "en"

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

# ── Operation timeouts (seconds) ──
FEED_FETCH_TIMEOUT  = 7
FEEDS_SCAN_TIMEOUT  = 22
SCRAPE_TIMEOUT      = 12
TELEGRAM_TIMEOUT    = 50

# ── REMOVED CONSTANTS (were for sequential MyMemory pipeline): ──
# SUMMARY_SENTENCES    = 8       ← removed
# MYMEMORY_CHUNK_SIZE  = 450     ← removed
# MYMEMORY_CHUNK_DELAY = 1.0     ← removed
# MYMEMORY_EMAIL       = ...     ← removed
# TRANSLATION_TIMEOUT  = 45      ← replaced by race timeouts below

# ── Parallel AI race timeouts ──────────────────────────────
# Per-API timeout: how long to wait for a single provider.
# If Groq hasn't responded in 20s, its task is abandoned.
AI_PER_API_TIMEOUT   = 20    # seconds per individual API call

# Race timeout: hard ceiling for the entire parallel race.
# Even if no API has responded, we abort after this long.
# Must be < function timeout (120s) with room for other phases.
AI_RACE_TIMEOUT      = 35    # seconds for the whole race

# Title translation timeout: separate quick race for title only.
AI_TITLE_TIMEOUT     = 15    # seconds for title translation race

# ── Persian response validation ──
# Minimum chars a response must have to be considered valid.
# Rejects empty strings, one-word responses, error messages.
MIN_PERSIAN_CHARS = 50

# ── Groq configuration ──
# Model selection: llama3-70b for quality, llama3-8b as fallback
GROQ_MODEL         = "llama3-70b-8192"
GROQ_MAX_TOKENS    = 700
GROQ_TEMPERATURE   = 0.4

# ── OpenRouter configuration ──
OPENROUTER_MODEL       = "mistralai/mistral-7b-instruct"
OPENROUTER_MAX_TOKENS  = 700
OPENROUTER_TEMPERATURE = 0.4

# ── Image scraping ──
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_BLOCKLIST  = [
    "doubleclick", "googletagmanager", "googlesyndication",
    "facebook.com/tr", "analytics", "pixel", "beacon",
    "tracking", "counter", "stat.", "stats.",
]

# ── Duplicate detection ──
FUZZY_SIMILARITY_THRESHOLD = 0.65
FUZZY_LOOKBACK_COUNT       = 150
DOMAIN_DEDUP_HOURS         = 6

# ── Stop words for title normalization ──
TITLE_STOP_WORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will",
    "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from",
    "its", "it", "this", "that", "these", "those", "and", "or",
    "but", "as", "up", "out", "if", "about", "into", "over",
    "after", "new", "first", "last", "says", "said",
}

# ── Peak hours UTC (Tehran = UTC+3:30) ──
PEAK_HOURS_UTC  = {4, 5, 6, 9, 10, 11, 16, 17, 18, 19}
PEAK_HOUR_BONUS = 15

# ── Article scoring weights ──
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

# ── Trend scoring keywords ──
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
    "met gala":      "#MetGala #مت_گالا",
    "red carpet":    "#RedCarpet #فرش_قرمز",
    "couture":       "#Couture #کوتور",
    "collab":        "#Collab #همکاری",
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


# ═══════════════════════════════════════════════════════════
# SECTION 2 — PARALLEL AI TRANSLATION ENGINE
#
# This is the core new feature of v10.0.
# Replaces all of the following from v9.0:
#   _extractive_summarize()
#   _translate_mymemory()
#   _translate_article()
#   _split_chunks()
#
# HOW IT WORKS:
#   1. Both API callers (_call_groq, _call_openrouter) are
#      wrapped in asyncio.Tasks and created simultaneously.
#   2. Each task runs independently — no awaiting each other.
#   3. Each pushes its result to a shared asyncio.Queue.
#   4. The collector loop reads from the queue.
#   5. First valid Persian result → winner declared.
#   6. All remaining tasks are cancelled via task.cancel().
#   7. gather(return_exceptions=True) ensures clean shutdown.
#
# WHY asyncio.Queue OVER asyncio.as_completed:
#   Queue allows tasks to self-report validity before pushing.
#   Invalid results push None (sentinel) to advance the counter
#   without blocking. The collector never blocks on a slow API.
#
# WHY NOT asyncio.wait(FIRST_COMPLETED):
#   wait() returns the first task to FINISH, not the first to
#   return a VALID result. We need the validity check inside
#   the worker, not in the collector.
# ═══════════════════════════════════════════════════════════

# ── Prompt template ─────────────────────────────────────────
# Used for both body summarization and title translation.
# The {mode} and {input_text} placeholders are filled at call time.
_PROMPT_BODY = """\
You are a professional Persian-language fashion journalist \
writing for an Iranian Telegram channel called @irfashionnews.

TASK:
Read the following English fashion article and write a SHORT, \
NATURAL Persian summary.

RULES:
- Write in fluent, editorial Persian (Farsi). Not literal translation.
- Maximum 6 sentences. Make every sentence count.
- Cover: what happened, who is involved, why it matters.
- Write flowing prose. No bullet points. No numbered lists.
- Do NOT include any English text in your output.
- Do NOT add explanations, headers, or preamble.
- Output ONLY the Persian summary text.

ARTICLE:
\"\"\"
{input_text}
\"\"\"

Persian summary:"""

_PROMPT_TITLE = """\
You are a Persian translator for an Iranian fashion news channel.

TASK:
Translate the following English fashion article title into \
natural, fluent Persian (Farsi).

RULES:
- Output ONLY the Persian translation of the title.
- No English. No explanation. No quotes. No preamble.
- Keep brand names in their original Latin script.
- Make it sound like a real Iranian fashion headline.

English title: {input_text}

Persian title:"""


def _is_valid_persian(text: str | None) -> bool:
    """
    Validate that a response is genuine usable Persian text.

    Checks (all must pass):
      1. Not None, not empty, is a string
      2. Length >= MIN_PERSIAN_CHARS after stripping
      3. Contains at least one Persian/Arabic Unicode character
         (U+0600-U+06FF covers Persian, Arabic, Urdu scripts)
      4. Does not start with a known API error marker

    Returns True only if all checks pass.
    """
    if not text or not isinstance(text, str):
        return False

    stripped = text.strip()

    if len(stripped) < MIN_PERSIAN_CHARS:
        return False

    # Persian/Arabic Unicode block check
    # U+0600–U+06FF: Arabic and Persian letters
    # U+FB50–U+FDFF: Arabic Presentation Forms-A
    # U+FE70–U+FEFF: Arabic Presentation Forms-B
    has_persian = any(
        "\u0600" <= ch <= "\u06ff"
        or "\ufb50" <= ch <= "\ufdff"
        or "\ufe70" <= ch <= "\ufeff"
        for ch in stripped
    )
    if not has_persian:
        return False

    # Known API/LLM error string markers
    _ERROR_MARKERS = (
        "error", "invalid_api_key", "rate_limit", "quota_exceeded",
        "model_not_found", "context_length_exceeded", "bad request",
        "unauthorized", "forbidden", "too many requests",
        "service unavailable", "internal server error",
    )
    lower = stripped.lower()
    if any(marker in lower for marker in _ERROR_MARKERS):
        return False

    return True


def _extract_text_from_openai_response(data: dict) -> str | None:
    """
    Safely extract the assistant message content from an
    OpenAI-compatible chat completion response dict.
    Works for both Groq and OpenRouter (same response schema).
    Returns None if the structure is missing or malformed.
    """
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


async def _call_groq(
    session: aiohttp.ClientSession,
    prompt: str,
) -> str | None:
    """
    Send a chat completion request to Groq API.

    Uses llama3-70b-8192 — fast inference, excellent Persian quality.
    Raises CancelledError cleanly when the race is won by another API.

    Args:
        session: shared aiohttp session (connection pooled)
        prompt:  fully-formed prompt string

    Returns:
        Persian text string if valid, None otherwise.
        Never raises (except CancelledError which propagates).
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        print("[race] Groq: GROQ_API_KEY not set — skipping.")
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
                print(
                    f"[race] Groq HTTP {resp.status}: "
                    f"{body[:100]}"
                )
                return None

            data   = await resp.json()
            result = _extract_text_from_openai_response(data)
            valid  = _is_valid_persian(result)

            print(
                f"[race] Groq responded: "
                f"{len(result or '')}ch | valid={valid}"
            )
            return result if valid else None

    except asyncio.CancelledError:
        # This is expected when another API wins the race.
        # Must re-raise so asyncio can clean up the task properly.
        print("[race] Groq: task cancelled (race won by another API).")
        raise

    except aiohttp.ClientError as e:
        print(f"[race] Groq network error: {e}")
        return None

    except Exception as e:
        print(f"[race] Groq unexpected error: {type(e).__name__}: {e}")
        return None


async def _call_openrouter(
    session: aiohttp.ClientSession,
    prompt: str,
) -> str | None:
    """
    Send a chat completion request to OpenRouter API.

    Uses mistralai/mistral-7b-instruct — reliable, good Persian output.
    OpenRouter requires additional headers for attribution/routing.

    Args:
        session: shared aiohttp session (connection pooled)
        prompt:  fully-formed prompt string

    Returns:
        Persian text string if valid, None otherwise.
        Never raises (except CancelledError which propagates).
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("[race] OpenRouter: OPENROUTER_API_KEY not set — skipping.")
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
        # OpenRouter routing headers — required for proper attribution
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
                print(
                    f"[race] OpenRouter HTTP {resp.status}: "
                    f"{body[:100]}"
                )
                return None

            data   = await resp.json()
            result = _extract_text_from_openai_response(data)
            valid  = _is_valid_persian(result)

            print(
                f"[race] OpenRouter responded: "
                f"{len(result or '')}ch | valid={valid}"
            )
            return result if valid else None

    except asyncio.CancelledError:
        print("[race] OpenRouter: task cancelled (race won by another API).")
        raise

    except aiohttp.ClientError as e:
        print(f"[race] OpenRouter network error: {e}")
        return None

    except Exception as e:
        print(
            f"[race] OpenRouter unexpected error: "
            f"{type(e).__name__}: {e}"
        )
        return None


async def parallel_summarize_translate(
    input_text: str,
    mode: str = "body",
) -> str | None:
    """
    ════════════════════════════════════════════════════════
    CORE ENGINE: First-Response-Wins Parallel AI Race

    Fires Groq and OpenRouter SIMULTANEOUSLY at t=0.
    Returns the FIRST valid Persian response received.
    Cancels all remaining in-flight tasks immediately.

    Args:
        input_text: English text to summarize + translate.
                    For mode="body": full article text.
                    For mode="title": just the article title.
        mode:       "body"  → use _PROMPT_BODY template
                    "title" → use _PROMPT_TITLE template

    Returns:
        str  → valid Persian text (winner's output)
        None → ALL providers failed or timed out

    Concurrency flow:
        t=0.000  Task A (_call_groq)       created + starts
        t=0.000  Task B (_call_openrouter) created + starts
                 ↕  (both running concurrently)
        t=1.800  Task A resolves → valid Persian → queue.put()
        t=1.800  collector receives → winner = Task A output
        t=1.800  Task B → task.cancel()
        t=1.802  gather(return_exceptions=True) → cleanup done
        t=1.802  return winner

    Cancellation safety:
        Each API caller catches CancelledError and re-raises.
        gather(return_exceptions=True) suppresses the
        CancelledError from cancelled tasks so it doesn't
        bubble up to the caller.

    Sentinel protocol:
        Workers push result (str) on success.
        Workers push None on failure/invalid.
        Collector counts Nones to detect total failure.
        When none_count == total_tasks, all failed → return None.
    ════════════════════════════════════════════════════════
    """
    if not input_text or not input_text.strip():
        print("[race] Empty input — aborting race.")
        return None

    # Select prompt template
    if mode == "title":
        prompt = _PROMPT_TITLE.format(
            input_text=input_text.strip()[:500]
        )
    else:
        prompt = _PROMPT_BODY.format(
            input_text=input_text.strip()[:3000]
        )

    # Shared result queue — capacity 1 is enough
    # Workers push their result (str or None) exactly once.
    result_queue: asyncio.Queue[str | None] = asyncio.Queue()

    # Registry: list of (name, async_caller_fn)
    # Add more providers here without changing the race logic.
    providers = [
        ("Groq",       _call_groq),
        ("OpenRouter", _call_openrouter),
    ]
    total_tasks = len(providers)

    async def _worker(
        name: str,
        caller_fn,
        session: aiohttp.ClientSession,
    ) -> None:
        """
        Wraps one API caller.
        Pushes result (str if valid, None if invalid/error).
        Re-raises CancelledError for clean task termination.
        """
        try:
            result = await caller_fn(session, prompt)
            # Push to queue regardless of validity.
            # Collector handles None as "this worker failed".
            await result_queue.put(result)
        except asyncio.CancelledError:
            # Do NOT push to queue — task was cancelled after
            # another worker already won. Pushing here would
            # corrupt the none_count in the collector.
            raise
        except Exception as e:
            print(f"[race] _worker({name}) unhandled: {e}")
            await result_queue.put(None)

    # Shared aiohttp session — one TCP connection pool for all tasks
    connector = aiohttp.TCPConnector(
        limit=10,
        enable_cleanup_closed=True,
    )

    async with aiohttp.ClientSession(connector=connector) as session:

        # ── Fire ALL tasks simultaneously ──────────────────
        # asyncio.create_task() schedules coroutines on the
        # event loop immediately. They begin executing at the
        # next await point — effectively t=0 for all.
        tasks: list[asyncio.Task] = [
            asyncio.create_task(
                _worker(name, fn, session),
                name=f"ai_race_{name.lower()}",
            )
            for name, fn in providers
        ]

        print(
            f"[race] ★ {total_tasks} providers fired simultaneously "
            f"(mode={mode}, timeout={AI_RACE_TIMEOUT}s)."
        )

        winner:     str | None = None
        none_count: int        = 0

        # ── Collect results — first valid wins ─────────────
        try:
            async with asyncio.timeout(AI_RACE_TIMEOUT):
                # Loop until we find a winner OR all workers fail.
                # none_count tracks how many workers pushed None.
                # When none_count == total_tasks, all failed.
                while none_count < total_tasks:
                    result = await result_queue.get()

                    if _is_valid_persian(result):
                        winner = result
                        print(
                            f"[race] ✓ Winner selected: "
                            f"{len(winner)}ch. "
                            f"Cancelling {total_tasks - 1} "
                            f"remaining task(s)."
                        )
                        break
                    else:
                        none_count += 1
                        print(
                            f"[race] ✗ Invalid/empty result "
                            f"({none_count}/{total_tasks} failed)."
                        )

        except TimeoutError:
            # asyncio.timeout() raised — race took too long
            print(
                f"[race] ✗ Race timed out after "
                f"{AI_RACE_TIMEOUT}s. No winner."
            )

        finally:
            # ── Cancel ALL remaining in-flight tasks ───────
            # This runs whether we have a winner or not.
            # Ensures no zombie tasks remain after this function.
            cancelled = 0
            for task in tasks:
                if not task.done():
                    task.cancel()
                    cancelled += 1

            if cancelled:
                # Wait for cancelled tasks to finish their
                # CancelledError handling. return_exceptions=True
                # prevents CancelledError from propagating here.
                await asyncio.gather(*tasks, return_exceptions=True)
                print(f"[race] {cancelled} task(s) cancelled and cleaned up.")

    if winner:
        print(f"[race] ═══ Race complete. Winner: {len(winner)}ch. ═══")
    else:
        print("[race] ═══ Race complete. ALL providers failed. ═══")

    return winner


# ═══════════════════════════════════════════════════════════
# SECTION 3 — MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

async def main(event=None, context=None):
    print("[INFO] ═══ FashionBot v10.0 started ═══")

    loop       = asyncio.get_running_loop()
    start_time = loop.time()

    def elapsed() -> str:
        return f"{loop.time() - start_time:.1f}"

    # ── Load ALL secrets from environment only ──
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
        print(f"[ERROR] Missing env vars: {missing}")
        return {"status": "error", "missing_vars": missing}

    # Warn if no AI provider keys are present
    _ai_keys = [
        os.environ.get("GROQ_API_KEY", ""),
        os.environ.get("OPENROUTER_API_KEY", ""),
    ]
    if not any(_ai_keys):
        print(
            "[WARN] No AI API keys found "
            "(GROQ_API_KEY, OPENROUTER_API_KEY). "
            "Translation will fail."
        )

    # ── Initialize clients ──
    bot       = Bot(token=token)
    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)
    sdk_mode  = "new" if hasattr(databases, "list_rows") else "legacy"
    print(f"[INFO] SDK mode: {sdk_mode}")

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)
    current_hour   = now.hour
    is_peak        = current_hour in PEAK_HOURS_UTC
    print(
        f"[INFO] UTC={current_hour}h | "
        f"Peak={'YES +' + str(PEAK_HOUR_BONUS) if is_peak else 'no'}"
    )

    recent_titles = _load_recent_titles(
        databases, database_id, COLLECTION_ID,
        sdk_mode, FUZZY_LOOKBACK_COUNT,
    )
    print(f"[INFO] [{elapsed()}s] {len(recent_titles)} recent titles loaded.")

    # ════════════════════════════════
    # PHASE 1 — RSS SCAN
    # ════════════════════════════════
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
        print(f"[INFO] [{elapsed()}s] No new article found.")
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

    print(
        f"[INFO] [{elapsed()}s] Selected: "
        f"score={score} cat={category} | {title[:65]}"
    )

    is_dup, dup_reason = _strict_duplicate_check(
        databases, database_id, COLLECTION_ID,
        link, content_hash, title_hash, sdk_mode,
    )
    if is_dup:
        print(f"[WARN] [{elapsed()}s] Pre-flight dup ({dup_reason}). Abort.")
        return {"status": "success", "posted": False, "reason": dup_reason}

    # ════════════════════════════════
    # PHASE 2 — PARALLEL SCRAPE
    # ════════════════════════════════
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

    full_text  = text_result  if isinstance(text_result,  str)  else None
    image_urls = image_result if isinstance(image_result, list) else []
    content    = _select_content(full_text, desc, title)

    print(
        f"[INFO] [{elapsed()}s] "
        f"Text={'scraped' if full_text else 'fallback'} "
        f"({len(content)}ch) | Images={len(image_urls)}"
    )

    if len(content) < MIN_CONTENT_CHARS:
        print(f"[WARN] [{elapsed()}s] Thin content — aborting.")
        return {
            "status": "skipped",
            "reason": f"thin_content ({len(content)}ch)",
            "posted": False,
        }

    # ════════════════════════════════════════════════════════
    # PHASE 3 — PARALLEL AI: SUMMARIZE + TRANSLATE
    #
    # REPLACES (removed from v9.0):
    #   english_summary = await loop.run_in_executor(
    #       None, _extractive_summarize, content, SUMMARY_SENTENCES
    #   )
    #   title_fa, body_fa = await asyncio.wait_for(
    #       asyncio.gather(
    #           loop.run_in_executor(None, _translate_mymemory, title),
    #           loop.run_in_executor(None, _translate_mymemory, summary),
    #       ), timeout=TRANSLATION_TIMEOUT,
    #   )
    #
    # NEW: single parallel race handles summarize + translate
    # in ONE LLM prompt. No separate summarize step needed.
    # Body and title races run concurrently with each other.
    # ════════════════════════════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 3: Parallel AI race...")

    # Run body and title translation races concurrently.
    # Both fire their provider pairs simultaneously.
    # asyncio.gather here runs TWO races in parallel:
    #   Race A: Groq vs OpenRouter for body (with full article)
    #   Race B: Groq vs OpenRouter for title (with title only)
    try:
        body_fa, title_fa = await asyncio.wait_for(
            asyncio.gather(
                # Race A: body summarization + translation
                parallel_summarize_translate(
                    content,
                    mode="body",
                ),
                # Race B: title translation
                parallel_summarize_translate(
                    title,
                    mode="title",
                ),
                return_exceptions=True,
            ),
            # Outer safety net: both races together must
            # finish within this time. AI_RACE_TIMEOUT already
            # handles per-race limits; this catches edge cases.
            timeout=AI_RACE_TIMEOUT + AI_TITLE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Outer Phase 3 timeout.")
        body_fa  = None
        title_fa = None

    # Unwrap any exceptions from gather
    body_fa  = body_fa  if isinstance(body_fa,  str) else None
    title_fa = title_fa if isinstance(title_fa, str) else None

    # Fallbacks: use English if Persian failed
    title_fa = (title_fa or "").strip() or title
    body_fa  = (body_fa  or "").strip() or None

    if not body_fa:
        print(f"[WARN] [{elapsed()}s] All AI providers failed for body.")
        return {
            "status": "error",
            "reason": "translation_failed",
            "posted": False,
        }

    print(
        f"[INFO] [{elapsed()}s] "
        f"title_fa={len(title_fa)}ch | body_fa={len(body_fa)}ch"
    )

    # ════════════════════════════════
    # PHASE 4 — BUILD CAPTION
    # ════════════════════════════════
    combined_for_tags = f"{title} {desc} {content[:500]}"
    hashtags = _extract_hashtags_from_text(combined_for_tags)
    caption  = _build_caption(title_fa, body_fa, hashtags, category)

    print(
        f"[INFO] [{elapsed()}s] "
        f"Caption={len(caption)}ch | Hashtags={len(hashtags)}"
    )

    # ════════════════════════════════
    # PHASE 5 — DB WRITE (distributed lock)
    # ════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 5: DB write (lock)...")
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
        print(f"[WARN] [{elapsed()}s] DB write failed — aborting.")
        return {
            "status": "error",
            "reason": "db_save_failed",
            "posted": False,
        }
    print(f"[INFO] [{elapsed()}s] DB lock acquired.")

    # ════════════════════════════════
    # PHASE 6 — POST TO TELEGRAM
    # ════════════════════════════════
    print(f"[INFO] [{elapsed()}s] Phase 6: Posting to Telegram...")
    try:
        posted = await asyncio.wait_for(
            _post_to_telegram(bot, chat_id, caption, image_urls),
            timeout=TELEGRAM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print(f"[WARN] [{elapsed()}s] Telegram timed out.")
        posted = False
    except Exception as e:
        print(f"[ERROR] [{elapsed()}s] Telegram: {e}")
        posted = False

    print(
        f"[INFO] ═══ v10.0 done in {elapsed()}s | "
        f"{'POSTED ✓' if posted else 'FAILED ✗'} ═══"
    )
    return {"status": "success", "posted": posted}


# ═══════════════════════════════════════════════════════════
# SECTION 4 — STANDALONE ARTICLE PROCESSOR
# (updated to use parallel race — same engine as main())
# ═══════════════════════════════════════════════════════════

async def process_article(
    url: str,
    title: str = "",
    description: str = "",
    category: str = "",
) -> dict:
    """
    Standalone processor for a single article URL.
    Uses the same parallel AI race as the main pipeline.
    No Telegram posting — returns structured data only.
    """
    loop = asyncio.get_running_loop()

    _EMPTY = {
        "title_fa": "", "summary_fa": "",
        "hashtags": [], "caption":    "",
        "image_urls": [],
    }

    if not url or not url.startswith("http"):
        return {"status": "error", "reason": "invalid_url", **_EMPTY}

    print(f"[process_article] START url={url[:80]}")

    # ── Stub RSS entry for image scraper ──
    class _StubEntry:
        def get(self, key, default=None): return default
        content = []

    # ── Parallel scrape ──
    try:
        text_result, image_result = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(None, _scrape_text, url),
                loop.run_in_executor(
                    None, _scrape_images, url, _StubEntry()
                ),
                return_exceptions=True,
            ),
            timeout=SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        text_result  = None
        image_result = []

    scraped_text = text_result  if isinstance(text_result,  str)  else None
    image_urls   = image_result if isinstance(image_result, list) else []
    content      = _select_content(scraped_text, description, title)

    if len(content) < MIN_CONTENT_CHARS:
        return {
            "status": "skipped",
            "reason": f"thin_content ({len(content)}ch)",
            **_EMPTY,
            "image_urls": image_urls,
        }

    effective_title = title or _extract_first_sentence(content)

    # ── Parallel AI races (body + title simultaneously) ──
    try:
        body_fa, title_fa = await asyncio.wait_for(
            asyncio.gather(
                parallel_summarize_translate(content,         mode="body"),
                parallel_summarize_translate(effective_title, mode="title"),
                return_exceptions=True,
            ),
            timeout=AI_RACE_TIMEOUT + AI_TITLE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        body_fa  = None
        title_fa = None

    body_fa  = body_fa  if isinstance(body_fa,  str) else None
    title_fa = title_fa if isinstance(title_fa, str) else None

    title_fa = (title_fa or "").strip() or effective_title
    body_fa  = (body_fa  or "").strip() or None

    if not body_fa:
        return {
            "status": "error",
            "reason": "translation_failed",
            **_EMPTY,
            "image_urls": image_urls,
        }

    combined_for_tags = f"{title} {description} {content[:500]}"
    hashtags          = _extract_hashtags_from_text(combined_for_tags)
    detected_category = (
        category or _detect_category(title, description or content[:300])
    )
    caption = _build_caption(title_fa, body_fa, hashtags, detected_category)

    return {
        "status":     "success",
        "title_fa":   title_fa,
        "summary_fa": body_fa,
        "hashtags":   hashtags,
        "caption":    caption,
        "image_urls": image_urls,
    }


# ═══════════════════════════════════════════════════════════
# SECTION 5 — FEED SCANNING & CANDIDATE SELECTION
# (unchanged from v9.0)
# ═══════════════════════════════════════════════════════════

async def _find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, now, recent_titles, is_peak,
):
    loop  = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, _fetch_feed, url, time_threshold)
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

    print(f"[INFO] {len(all_candidates)} articles collected.")
    if not all_candidates:
        return None

    for c in all_candidates:
        c["score"]    = _score_article(c, now, is_peak)
        c["category"] = _detect_category(c["title"], c["description"])

    all_candidates.sort(key=lambda x: x["score"], reverse=True)

    print("[INFO] Top 5 candidates by score:")
    for c in all_candidates[:5]:
        print(
            f"       [{c['score']:>3}] [{c['category']:<14}] "
            f"{c['title'][:58]}"
        )

    recent_domain_hashes = _load_recent_domain_hashes(
        databases, database_id, collection_id, sdk_mode
    )
    seen_domains_this_run = set()

    for c in all_candidates:
        link         = c["link"]
        title        = c["title"]
        feed_url     = c["feed_url"]
        domain       = _get_domain(link)
        content_hash = _make_content_hash(title)
        title_hash   = _make_title_hash(title, feed_url)
        domain_hash  = _make_domain_hash(domain)

        r = _query_field(databases, database_id, collection_id,
                         "link", link[:DB_LINK_MAX], sdk_mode)
        if r is not False:
            print(f"[SKIP] L1({'err' if r is None else 'dup'}): {title[:58]}")
            continue

        r = _query_field(databases, database_id, collection_id,
                         "content_hash", content_hash, sdk_mode)
        if r is not False:
            print(f"[SKIP] L2({'err' if r is None else 'dup'}): {title[:58]}")
            continue

        r = _query_field(databases, database_id, collection_id,
                         "title_hash", title_hash, sdk_mode)
        if r is not False:
            print(f"[SKIP] L2b({'err' if r is None else 'dup'}): {title[:58]}")
            continue

        is_fuzz, matched, fuzz_score = _fuzzy_duplicate(title, recent_titles)
        if is_fuzz:
            print(
                f"[SKIP] L3 fuzzy={fuzz_score:.2f}: "
                f"{title[:45]} ≈ {(matched or '')[:35]}"
            )
            continue

        if domain_hash in recent_domain_hashes:
            print(f"[INFO] L4b: domain {domain} seen recently — not blocking.")

        if domain in seen_domains_this_run:
            print(f"[SKIP] L4a domain/run ({domain}): {title[:58]}")
            continue

        seen_domains_this_run.add(domain)
        print(f"[INFO] PASS fuzz={fuzz_score:.2f}: {title[:58]}")
        return c

    print("[INFO] All candidates exhausted.")
    return None


def _fetch_feed(feed_url: str, time_threshold: datetime) -> list:
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


def _score_article(candidate: dict, now: datetime, is_peak: bool = False) -> int:
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
        if matched >= 3:
            break
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


def _extract_hashtags_from_text(text: str) -> list:
    lower    = text.lower()
    hashtags = []
    seen     = set()
    for keyword, tags in HASHTAG_MAP.items():
        if keyword in lower and keyword not in seen:
            hashtags.append(tags)
            seen.add(keyword)
            if len(hashtags) >= MAX_HASHTAGS:
                break
    return hashtags


def _extract_hashtags(title: str, description: str) -> list:
    return _extract_hashtags_from_text(f"{title} {description}")


# ═══════════════════════════════════════════════════════════
# SECTION 6 — DUPLICATE DETECTION
# (unchanged from v9.0)
# ═══════════════════════════════════════════════════════════

def _make_content_hash(title: str) -> str:
    tokens     = _normalize_tokens(title)
    normalized = " ".join(sorted(tokens))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

def _make_title_hash(title: str, feed_url: str) -> str:
    raw = (title.lower().strip() + feed_url[:50]).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()

def _make_domain_hash(domain: str) -> str:
    return hashlib.sha256(
        domain.encode("utf-8")
    ).hexdigest()[:DB_DOMAIN_HASH_MAX]

def _normalize_tokens(title: str) -> frozenset:
    title  = title.lower()
    title  = re.sub(r"[^a-z0-9\s]", " ", title)
    tokens = title.split()
    return frozenset(
        t for t in tokens
        if t not in TITLE_STOP_WORDS and len(t) >= 2
    )

def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b: return 0.0
    return len(a & b) / len(a | b)

def _fuzzy_duplicate(
    title: str, recent_titles: list
) -> tuple[bool, str | None, float]:
    if not recent_titles:
        return False, None, 0.0
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

def _strict_duplicate_check(
    databases, database_id, collection_id,
    link, content_hash, title_hash, sdk_mode,
) -> tuple[bool, str]:
    for field, value in [
        ("link",         link[:DB_LINK_MAX]),
        ("content_hash", content_hash),
        ("title_hash",   title_hash),
    ]:
        r = _query_field(
            databases, database_id, collection_id,
            field, value, sdk_mode,
        )
        if r is True:  return True, f"found_{field}"
        if r is None:  return True, f"db_error_{field}"
    return False, ""

def _query_field(databases, database_id, collection_id,
                 field, value, sdk_mode):
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

def _load_recent_titles(
    databases, database_id, collection_id, sdk_mode, limit
) -> list:
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

def _load_recent_domain_hashes(
    databases, database_id, collection_id, sdk_mode
) -> set:
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

def _get_domain(url: str) -> str:
    try:
        parts = urlparse(url).netloc.replace("www.", "").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else url[:30]
    except Exception:
        return url[:30]


# ═══════════════════════════════════════════════════════════
# SECTION 7 — SCRAPING
# (unchanged from v9.0)
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

def _extract_first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return parts[0][:DB_TITLE_MAX] if parts else text[:DB_TITLE_MAX]

def _scrape_text(url: str) -> str | None:
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
        area = body or soup

        TARGET_TAGS = {"p", "h2", "h3", "h4", "li"}
        lines       = []
        seen_keys   = set()

        for el in area.find_all(TARGET_TAGS):
            raw = re.sub(r"\s+", " ", el.get_text(" ").strip())
            if len(raw) < 25:
                continue
            key = raw.lower()[:80]
            if key in seen_keys:
                continue
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
        print(f"[WARN] Text scrape timeout: {url[:60]}")
        return None
    except requests.exceptions.HTTPError as e:
        print(f"[WARN] Text scrape HTTP {e.response.status_code}: {url[:60]}")
        return None
    except Exception as e:
        print(f"[WARN] Text scrape: {e}")
        return None


def _scrape_images(url: str, rss_entry) -> list:
    images = []
    seen   = set()

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
        print(f"[WARN] Image scrape: {e}")

    if len(images) < MAX_IMAGES:
        rss_img = _extract_rss_image(rss_entry)
        if rss_img:
            _add(rss_img)

    print(f"[INFO] Images collected: {len(images)}")
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
# SECTION 8 — CAPTION BUILDER
# (unchanged from v9.0)
# ═══════════════════════════════════════════════════════════

def _build_caption(
    title_fa: str,
    body_fa: str,
    hashtags: list,
    category: str,
) -> str:
    def _esc(t: str) -> str:
        return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    CATEGORY_EMOJI = {
        "runway": "👗", "brand": "🏷️", "business": "📊",
        "beauty": "💄", "sustainability": "♻️", "celebrity": "⭐",
        "trend": "🔥", "general": "🌐",
    }
    emoji     = CATEGORY_EMOJI.get(category, "🌐")
    hash_line = " ".join(hashtags)

    header = f"<b>{_esc(title_fa.strip())}</b>"
    sep    = "─────────────\n@irfashionnews"
    footer = f"{emoji}  <i>کانال مد و فشن ایرانی</i>"

    fixed_parts = [header, sep, footer]
    if hash_line:
        fixed_parts.append(hash_line)
    fixed_len   = len("\n\n".join(fixed_parts))
    body_budget = CAPTION_MAX - fixed_len - 4

    safe_body = _esc(body_fa.strip())

    if body_budget <= 10:
        safe_body = ""
        header    = f"<b>{_esc(title_fa.strip())[:CAPTION_MAX - 80]}</b>"
    elif len(safe_body) > body_budget:
        safe_body = safe_body[:body_budget - 1] + "…"

    parts = [header, sep]
    if safe_body:
        parts.append(safe_body)
    parts.append(footer)
    if hash_line:
        parts.append(hash_line)

    caption = "\n\n".join(parts)
    if len(caption) > CAPTION_MAX:
        caption = caption[:CAPTION_MAX - 1] + "…"
    return caption


# ═══════════════════════════════════════════════════════════
# SECTION 9 — TELEGRAM POSTING
# (unchanged from v9.0)
# ═══════════════════════════════════════════════════════════

async def _post_to_telegram(
    bot: Bot, chat_id: str, caption: str, image_urls: list,
) -> bool:
    anchor_msg_id = None
    posted        = False

    if len(image_urls) >= 2:
        try:
            media_group = [
                InputMediaPhoto(media=url)
                for url in image_urls[:MAX_IMAGES]
            ]
            sent_msgs     = await bot.send_media_group(
                chat_id=chat_id, media=media_group,
                disable_notification=True,
            )
            anchor_msg_id = sent_msgs[-1].message_id
            print(f"[INFO] ① Album: {len(sent_msgs)} images. anchor={anchor_msg_id}")
        except Exception as e:
            print(f"[WARN] ① Album failed: {str(e)[:120]}")
            if image_urls:
                try:
                    sent          = await bot.send_photo(
                        chat_id=chat_id, photo=image_urls[0],
                        disable_notification=True,
                    )
                    anchor_msg_id = sent.message_id
                    print(f"[INFO] ① Fallback single photo. anchor={anchor_msg_id}")
                except Exception as e2:
                    print(f"[WARN] ① Single photo fallback failed: {str(e2)[:80]}")
    elif len(image_urls) == 1:
        try:
            sent          = await bot.send_photo(
                chat_id=chat_id, photo=image_urls[0],
                disable_notification=True,
            )
            anchor_msg_id = sent.message_id
            print(f"[INFO] ① Single photo. anchor={anchor_msg_id}")
        except Exception as e:
            print(f"[WARN] ① Single photo failed: {str(e)[:120]}")
    else:
        print("[INFO] ① No images — caption standalone.")

    if anchor_msg_id is not None:
        print(f"[INFO] ② Waiting {ALBUM_CAPTION_DELAY}s...")
        await asyncio.sleep(ALBUM_CAPTION_DELAY)

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
        print(
            f"[INFO] ③ Caption sent "
            f"({'reply_to=' + str(anchor_msg_id) if anchor_msg_id else 'standalone'})."
        )
        posted = True
    except Exception as e:
        print(f"[ERROR] ③ Caption failed: {str(e)[:120]}")
        return False

    if posted and FASHION_STICKERS:
        await asyncio.sleep(STICKER_DELAY)
        try:
            await bot.send_sticker(
                chat_id=chat_id,
                sticker=random.choice(FASHION_STICKERS),
                disable_notification=True,
            )
            print("[INFO] ④ Sticker sent.")
        except Exception as e:
            print(f"[WARN] ④ Sticker failed (non-fatal): {str(e)[:80]}")

    return posted


# ═══════════════════════════════════════════════════════════
# SECTION 10 — DATABASE WRITE
# (unchanged from v9.0)
# ═══════════════════════════════════════════════════════════

def _build_db_payload(
    link, title, feed_url, pub_date, source_type,
    title_hash, content_hash, category,
    trend_score, post_hour, domain_hash,
) -> dict:
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
) -> bool:
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
# LOCAL TEST ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        test_url = sys.argv[1]
        print(f"[LOCAL] Testing process_article: {test_url}")

        async def _test():
            result = await process_article(url=test_url)
            print("\n── RESULT ──")
            for k, v in result.items():
                if k == "caption":
                    print(f"  {k} ({len(v)}ch):\n{v}\n")
                elif k == "image_urls":
                    print(f"  {k}: {v[:2]}")
                else:
                    val = str(v)
                    print(f"  {k}: {val[:120]}")

        asyncio.run(_test())
    else:
        asyncio.run(main())
