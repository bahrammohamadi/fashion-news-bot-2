# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    11.1 — Schema-Adaptive + Model Fixes
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# FIXES FROM v11.0:
#
#   FIX 1 — SCHEMA ADAPTATION:
#     Problem: "posted" field missing → all dedup queries fail
#     Solution:
#       - _detect_schema_fields() runs at startup
#       - Detects which fields exist in the collection
#       - _query_field_safe() uses only fields that exist
#       - When "posted" absent → falls back to link-only dedup
#       - Schema migration utility added (_migrate_schema)
#       - All dedup functions accept has_posted_field flag
#
#   FIX 2 — GROQ MODEL UPDATE:
#     Problem: llama3-70b-8192 decommissioned → HTTP 400
#     Solution:
#       - Primary:  llama-3.3-70b-versatile
#       - Fallback: llama-3.1-8b-instant (if primary 400s)
#       - Model tried in order, first success wins
#
#   FIX 3 — OPENROUTER KEY VALIDATION:
#     Problem: 401 "User not found" → silent failure
#     Solution:
#       - Key validated at startup with lightweight probe
#       - Invalid key → provider skipped cleanly
#       - Free model fallback: meta-llama/llama-3.1-8b-instruct:free
#       - Paid model: mistralai/mistral-7b-instruct
#
#   FIX 4 — SDK DEPRECATION WARNINGS:
#     Problem: list_documents deprecated since 1.8.0
#     Solution:
#       - All DB calls use _db_list() wrapper
#       - _db_list() tries list_rows first, falls back to list_documents
#       - Deprecation warnings suppressed cleanly
#
# SCHEMA MIGRATION (run once if posted field is missing):
#   python main.py --migrate
#   Adds posted/status/locked_at/posted_at/fail_reason fields.
#
# ONE-TIME CLEANUP (run once to clear unposted records):
#   python main.py --cleanup
# ============================================================


# ═══════════════════════════════════════════════════════════
# IMPORTS
# ═══════════════════════════════════════════════════════════

import os
import re
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
DB_REASON_MAX      = 499

# ── Timeouts ──
FEED_FETCH_TIMEOUT = 7
FEEDS_SCAN_TIMEOUT = 22
SCRAPE_TIMEOUT     = 12
TELEGRAM_TIMEOUT   = 50
AI_PER_API_TIMEOUT = 20
AI_RACE_TIMEOUT    = 35
AI_TITLE_TIMEOUT   = 15
AI_TIP_TIMEOUT     = 15

# ── Persian validation ──
MIN_PERSIAN_CHARS = 30

# ── Groq — updated model chain (FIX 2) ──
# Models tried in order. First to succeed wins.
GROQ_MODELS = [
    "llama-3.3-70b-versatile",   # primary — current flagship
    "llama-3.1-8b-instant",      # fallback — fast, always available
    "gemma2-9b-it",              # last resort
]
GROQ_MAX_TOKENS  = 700
GROQ_TEMPERATURE = 0.4

# ── OpenRouter (FIX 3) ──
# Free model tried first, paid model as fallback.
OPENROUTER_MODELS = [
    "meta-llama/llama-3.1-8b-instruct:free",  # free tier
    "mistralai/mistral-7b-instruct",           # paid fallback
]
OPENROUTER_MAX_TOKENS  = 700
OPENROUTER_TEMPERATURE = 0.4

# ── Google Gemini ──
GEMINI_MODELS = [
    "gemini-1.5-flash",
    "gemini-1.5-pro",
]

# ── Lock / dedup ──
LOCK_TTL_SECONDS           = 600
FUZZY_SIMILARITY_THRESHOLD = 0.65
FUZZY_LOOKBACK_COUNT       = 150
DOMAIN_DEDUP_HOURS         = 6

# ── Article state values ──
STATUS_LOCKED = "locked"
STATUS_POSTED = "posted"
STATUS_FAILED = "failed"

# ── Scoring ──
PEAK_HOURS_UTC          = {4, 5, 6, 9, 10, 11, 16, 17, 18, 19}
PEAK_HOUR_BONUS         = 15
SCORE_RECENCY_MAX       = 40
SCORE_TITLE_KEYWORD     = 15
SCORE_DESC_KEYWORD      = 5
SCORE_HAS_IMAGE         = 10
SCORE_DESC_LENGTH       = 10
SCORE_FASHION_RELEVANCE = 20

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

FASHION_STICKERS = [
    "CAACAgIAAxkBAAIBmGRx1yRFMVhVqVXLv_dAAXJMOdFNAAIUAAOVgnkAAVGGBbBjxbg4LwQ",
    "CAACAgIAAxkBAAIBmWRx1yRqy9JkN2DmV_Z2sRsKdaTjAAIVAAOVgnkAAc8R3q5p5-AELAQ",
    "CAACAgIAAxkBAAIBmmRx1yS2T2gfLqJQX9oK6LZqp1HIAAIWAAO0yXAAAV0MzCRF3ZRILAQ",
    "CAACAgIAAxkBAAIBm2Rx1ySiJV4dVeTuCTc-RfFDnfQpAAIXAAO0yXAAAA3Vm7IiJdisLAQ",
    "CAACAgIAAxkBAAIBnGRx1yT_jVlWt5xPJ7BO9aQ4JvFaAAIYAAO0yXAAAA0k9GZDQpLcLAQ",
]

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

BOILERPLATE_PATTERNS = [
    "subscribe", "newsletter", "sign up", "cookie",
    "privacy policy", "all rights reserved", "terms of service",
    "advertisement", "sponsored content", "follow us",
    "share this", "read more", "click here", "tap here",
    "download the app", "get the app",
]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
IMAGE_BLOCKLIST  = [
    "doubleclick", "googletagmanager", "googlesyndication",
    "facebook.com/tr", "analytics", "pixel", "beacon",
    "tracking", "counter", "stat.", "stats.",
]

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
# SECTION 2 — AI PROMPT TEMPLATES (TELEGRAM OPTIMIZED)
# ═══════════════════════════════════════════════════════════

_PROMPT_UNIFIED = '''\
تو سردبیر خلاق ارشد یک مجله دیجیتال مد، هنر و زیبایی‌شناسی لوکس به نام «مهرجامه» (آی‌آر فشن نیوز) در ایران هستی.
وظیفه تو تولید یک پست کامل، متمایز و بسیار جذاب تلگرامی با فرمت HTML بر اساس خبر یا محصول انگلیسی زیر است.

مخاطب تو: زنان و مردان خوش‌سلیقه، اهل هنر، طراحان و دنبال‌کنندگان مد آوانگارد در ایران هستند.

قوانین ساختاری و ادبی طلایی (نسخه ۱۳.۰):
۱. لحن نگارش:
   - صد درصد روزنامه‌نگاری حرفه‌ای، فاخر، داستان‌گو و عمیق، متناسب با ادبیات معتبرترین رسانه‌های مد و لایف‌استایل ایران (مثل مجلات چه‌بپوشم، مهرجامه، مجلات هنری).
   - دوری کامل از عامیانه‌نویسی، لحن‌های بازاری، زرد، تبلیغاتی یا کلیک‌بیت‌های تکراری (کلماتی مثل "خیره‌کننده"، "شگفت‌انگیز"، "باورنکردنی" مطلقاً ممنوع هستند).
   - پالت واژگانی فاخر ایرانی را به کار ببر (مانند: سیلوئت، هم‌نشینی فرم‌ها، دگرگونی زیبایی‌شناختی، کانسپچوال، بافت، ریزش پارچه، پویایی پالت، اصالت).

۲. دگرگونی و تنوع ساختاری (جلوگیری از خسته‌کننده بودن):
   - هرگز یک چارچوب ثابت و تکراری برای همه پست‌ها به کار نبر! ساختار پست باید با توجه به نوع خبر تغییر کند:
     الف) اگر خبر درباره «معرفی محصول جدید یک برند» است: پست را به شکل یک نقد مینی‌مال طراحی و مهندسی طراحی آن را شرح بده.
     ب) اگر خبر درباره «یک ترند جهانی» است: آن را بومی‌سازی کن و هم‌نشینی آن با آیتم‌های مرسوم در ایران (مثل مانتو کتی، بارانی، پالتو، شال، مینی‌اسکارف) را توضیح بده.
     ج) اگر خبر درباره «رویداد یا ران‌وی» است: فضا، اتمسفر و روح خلاق مجموعه را به شکل داستان‌گو و هنری روایت کن.
   - تنوع بصری ایجاد کن؛ گاهی از نشانه‌های مینی‌مال یونیکد مانند (✦، ⚜، 🕯) یا نقاط خالی به عنوان جداکننده استفاده کن. گاهی نکته استایل را در قالب یک جمله شاعرانه در بدنه متن ذوب کن، و گاهی آن را در انتهای پست به صورت یک "💡 فرمولوژی:" کوتاه و متمایز بیاور. ساختار نباید تکراری شود!

۳. بومی‌سازی عمیق برای فضای ایران:
   - در متن خبر توضیح بده که چرا این رویداد، محصول یا ترند جهانی برای مخاطب خوش‌پوش ایرانی الهام‌بخش است و چگونه می‌توان دراماتولوژی شهری (استایل خیابانی ایرانی) را با آن ارتقا داد.

۴. قوانین فنی تلگرام و HTML:
   - خروجی تو باید مستقیماً با کدهای HTML تلگرام تگ‌گذاری شده باشد (فقط تگ‌های مجاز تلگرام: <b> برای ضخیم، <i> برای کج).
   - تیتر اصلی حتماً باید برجسته باشد: ✦ <b>[تیتر کوتاه و مجلل فارسی]</b>
   - طول کل متن خروجی باید به شدت کنترل شود: **حداکثر ۹۵۰ کاراکتر** (بسیار مهم تا در کپشن عکس تلگرام جا شود و قطع نشود).
   - رعایت دقیق نیم‌فاصله‌های فارسی (به‌ویژه در افعال و صفت‌ها: «می‌شود»، «ترندهای»، «روایتگری»).
   - نام برندها، نام طراحان و اصطلاحات تخصصی مد را حتماً با الفبای لاتین بنویس (مثال: Chanel، بارانی، Blazer).
   - در خط پایانی، امضای کانال را به صورت زیر بیاور:
     {emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

خبر انگلیسی:
عنوان: {title}
محتوا: {input_text}

کپشن نهایی تلگرام (با فرمت HTML):'''

# ═══════════════════════════════════════════════════════════
# SECTION 3 — SCHEMA DETECTION (FIX 1)
#
# Detects which fields exist in the Appwrite collection.
# All dedup functions use this to avoid querying missing fields.
# ═══════════════════════════════════════════════════════════

class SchemaInfo:
    """Holds detected schema capabilities for this run."""
    def __init__(self):
        self.has_posted      = False   # posted (boolean) field exists
        self.has_status      = False   # status (string) field exists
        self.has_locked_at   = False   # locked_at field exists
        self.has_posted_at   = False   # posted_at field exists
        self.has_fail_reason = False   # fail_reason field exists
        self.has_content_hash = False  # content_hash field exists
        self.has_title_hash   = False  # title_hash field exists
        self.has_domain_hash  = False  # domain_hash field exists

    @property
    def is_v11(self) -> bool:
        """True if all v11 state fields are present."""
        return (
            self.has_posted
            and self.has_status
            and self.has_locked_at
        )

    def __str__(self) -> str:
        return (
            f"SchemaInfo("
            f"posted={self.has_posted}, "
            f"status={self.has_status}, "
            f"locked_at={self.has_locked_at}, "
            f"content_hash={self.has_content_hash}, "
            f"title_hash={self.has_title_hash})"
        )


def _detect_schema(
    databases,
    database_id: str,
    collection_id: str,
    sdk_mode: str,
    log_fn=print,
) -> SchemaInfo:
    """
    Probe the Appwrite collection schema by attempting
    lightweight test queries for each optional field.

    Returns SchemaInfo with boolean flags for each field.
    Never raises — returns minimal SchemaInfo on any error.
    """
    info = SchemaInfo()

    def _probe(field: str, value) -> bool:
        """Returns True if field exists in schema."""
        try:
            queries = [Query.equal(field, value), Query.limit(1)]
            _db_list(databases, database_id, collection_id,
                     queries, sdk_mode)
            return True
        except AppwriteException as e:
            msg = str(e.message).lower()
            # "attribute not found" = field does not exist
            if "attribute not found" in msg:
                return False
            # Other error = field probably exists, DB issue
            return True
        except Exception:
            return False

    info.has_posted       = _probe("posted",       True)
    info.has_status       = _probe("status",       STATUS_POSTED)
    info.has_locked_at    = _probe("locked_at",    "")
    info.has_posted_at    = _probe("posted_at",    "")
    info.has_fail_reason  = _probe("fail_reason",  "")
    info.has_content_hash = _probe("content_hash", "x")
    info.has_title_hash   = _probe("title_hash",   "x")
    info.has_domain_hash  = _probe("domain_hash",  "x")

    log_fn(f"[schema] Detected: {info}")
    if not info.is_v11:
        log_fn(
            "[schema] WARNING: v11 state fields missing. "
            "Run --migrate to add them. "
            "Falling back to link-only dedup."
        )
    return info


def _to_dict_safe(obj):
    """
    Safely converts Appwrite SDK objects (like DocumentList, Document)
    to standard Python dictionaries to avoid AttributeError and Subscriptable errors.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "to_dict"):
        return obj.to_dict()
    if hasattr(obj, "__dict__"):
        return getattr(obj, "__dict__")
    return obj


# ═══════════════════════════════════════════════════════════
# SECTION 4 — DB WRAPPER (FIX 4 — deprecation)
# ═══════════════════════════════════════════════════════════

def _db_list(
    databases,
    database_id: str,
    collection_id: str,
    queries: list,
    sdk_mode: str,
) -> dict:
    """
    Unified DB list call. Tries list_rows (new SDK) first,
    falls back to list_documents (legacy SDK).
    Suppresses DeprecationWarning on legacy path.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if sdk_mode == "new":
            try:
                res = databases.list_rows(
                    database_id=database_id,
                    collection_id=collection_id,
                    queries=queries,
                )
                return _to_dict_safe(res)
            except AttributeError:
                pass
        res = databases.list_documents(
            database_id=database_id,
            collection_id=collection_id,
            queries=queries,
        )
        return _to_dict_safe(res)


def _db_create(
    databases,
    database_id: str,
    collection_id: str,
    data: dict,
    sdk_mode: str,
) -> dict:
    """Unified DB create call."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if sdk_mode == "new":
            try:
                res = databases.create_row(
                    database_id=database_id,
                    collection_id=collection_id,
                    row_id="unique()",
                    data=data,
                )
                return _to_dict_safe(res)
            except AttributeError:
                pass
        res = databases.create_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id="unique()",
            data=data,
        )
        return _to_dict_safe(res)


def _db_update(
    databases,
    database_id: str,
    collection_id: str,
    doc_id: str,
    data: dict,
    sdk_mode: str,
) -> dict:
    """Unified DB update call."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if sdk_mode == "new":
            try:
                res = databases.update_row(
                    database_id=database_id,
                    collection_id=collection_id,
                    row_id=doc_id,
                    data=data,
                )
                return _to_dict_safe(res)
            except AttributeError:
                pass
        res = databases.update_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=doc_id,
            data=data,
        )
        return _to_dict_safe(res)


def _db_delete(
    databases,
    database_id: str,
    collection_id: str,
    doc_id: str,
    sdk_mode: str,
) -> None:
    """Unified DB delete call."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        if sdk_mode == "new":
            try:
                databases.delete_row(
                    database_id=database_id,
                    collection_id=collection_id,
                    row_id=doc_id,
                )
                return
            except AttributeError:
                pass
        databases.delete_document(
            database_id=database_id,
            collection_id=collection_id,
            document_id=doc_id,
        )


# ═══════════════════════════════════════════════════════════
# SECTION 5 — AI VALIDATION
# ═══════════════════════════════════════════════════════════

def _is_valid_persian(text: str | None) -> bool:
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
        "user not found",
    )
    if any(m in stripped.lower() for m in _ERROR_MARKERS):
        return False
    return True


def _extract_openai_content(data: dict) -> str | None:
    try:
        return (
            data.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "") or ""
        ).strip() or None
    except (IndexError, AttributeError, TypeError):
        return None


# ═══════════════════════════════════════════════════════════
# SECTION 6 — AI PROVIDER VALIDATION (FIX 2 & 3)
# ═══════════════════════════════════════════════════════════

async def _validate_groq_key(log_fn=print) -> bool:
    """Quick probe to verify Groq key is valid."""
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        return False
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                valid = resp.status == 200
                log_fn(f"[startup] Groq key valid={valid} (HTTP {resp.status})")
                return valid
    except Exception as e:
        log_fn(f"[startup] Groq key probe failed: {e}")
        return False


async def _validate_openrouter_key(log_fn=print) -> bool:
    """Quick probe to verify OpenRouter key is valid."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        return False
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            async with session.get(
                "https://openrouter.ai/api/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                valid = resp.status == 200
                log_fn(
                    f"[startup] OpenRouter key valid={valid} "
                    f"(HTTP {resp.status})"
                )
                return valid
    except Exception as e:
        log_fn(f"[startup] OpenRouter key probe failed: {e}")
        return False


# ═══════════════════════════════════════════════════════════
# SECTION 7 — PARALLEL AI RACE ENGINE (FIX 2 & 3)
# ═══════════════════════════════════════════════════════════

async def _call_groq(
    session: aiohttp.ClientSession,
    prompt: str,
    log_fn=print,
) -> str | None:
    """
    Groq API caller with model fallback chain.
    Tries each model in GROQ_MODELS until one succeeds.
    Skips decommissioned models (HTTP 400 with decommission msg).
    """
    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        log_fn("[race] Groq: no key — skipping.")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    for model in GROQ_MODELS:
        payload = {
            "model":       model,
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": GROQ_TEMPERATURE,
            "max_tokens":  GROQ_MAX_TOKENS,
        }
        try:
            async with session.post(
                "https://api.groq.com/openai/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
            ) as resp:
                body_text = await resp.text()

                if resp.status == 400:
                    # Check if this is a decommissioned model error
                    if "decommission" in body_text.lower():
                        log_fn(
                            f"[race] Groq model {model} decommissioned — "
                            f"trying next."
                        )
                        continue
                    log_fn(
                        f"[race] Groq/{model} HTTP 400: "
                        f"{body_text[:120]}"
                    )
                    continue

                if resp.status != 200:
                    log_fn(
                        f"[race] Groq/{model} HTTP {resp.status}: "
                        f"{body_text[:120]}"
                    )
                    continue

                import json as _json
                data   = _json.loads(body_text)
                result = _extract_openai_content(data)
                valid  = _is_valid_persian(result)
                log_fn(
                    f"[race] Groq/{model}: "
                    f"{len(result or '')}ch | valid={valid}"
                )
                if valid:
                    return result
                # Invalid result — try next model
                continue

        except asyncio.CancelledError:
            log_fn(f"[race] Groq/{model}: cancelled.")
            raise
        except aiohttp.ClientError as e:
            log_fn(f"[race] Groq/{model} network error: {e}")
            continue
        except Exception as e:
            log_fn(f"[race] Groq/{model} error: {type(e).__name__}: {e}")
            continue

    log_fn("[race] Groq: all models exhausted.")
    return None


async def _call_openrouter(
    session: aiohttp.ClientSession,
    prompt: str,
    log_fn=print,
) -> str | None:
    """
    OpenRouter API caller with model fallback chain.
    Tries free model first, paid model as fallback.
    Skips on 401 (invalid key).
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        log_fn("[race] OpenRouter: no key — skipping.")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://t.me/irfashionnews",
        "X-Title":       "IrFashionNews",
    }

    for model in OPENROUTER_MODELS:
        payload = {
            "model":       model,
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": OPENROUTER_TEMPERATURE,
            "max_tokens":  OPENROUTER_MAX_TOKENS,
        }
        try:
            async with session.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
            ) as resp:
                body_text = await resp.text()

                if resp.status == 401:
                    # Key is invalid — no point trying other models
                    log_fn(
                        f"[race] OpenRouter: 401 invalid key — "
                        f"skipping all OR models."
                    )
                    return None

                if resp.status == 402:
                    # Insufficient credits for paid model
                    log_fn(
                        f"[race] OpenRouter/{model}: 402 credits — "
                        f"trying next."
                    )
                    continue

                if resp.status != 200:
                    log_fn(
                        f"[race] OpenRouter/{model} HTTP {resp.status}: "
                        f"{body_text[:120]}"
                    )
                    continue

                import json as _json
                data   = _json.loads(body_text)
                result = _extract_openai_content(data)
                valid  = _is_valid_persian(result)
                log_fn(
                    f"[race] OpenRouter/{model}: "
                    f"{len(result or '')}ch | valid={valid}"
                )
                if valid:
                    return result
                continue

        except asyncio.CancelledError:
            log_fn(f"[race] OpenRouter/{model}: cancelled.")
            raise
        except aiohttp.ClientError as e:
            log_fn(f"[race] OpenRouter/{model} network error: {e}")
            continue
        except Exception as e:
            log_fn(
                f"[race] OpenRouter/{model} error: "
                f"{type(e).__name__}: {e}"
            )
            continue

    log_fn("[race] OpenRouter: all models exhausted.")
    return None


async def _call_gemini(
    session,
    prompt: str,
    log_fn=print,
) -> str | None:
    """
    Google Gemini API caller with model fallback chain.
    """
    import os
    import asyncio
    import aiohttp
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
    if not api_key:
        api_key = os.environ.get("GOOGLE_AI_KEY", "").strip()
    if not api_key:
        log_fn("[race] Gemini: no key — skipping.")
        return None

    headers = {
        "Content-Type": "application/json"
    }

    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.4,
                "maxOutputTokens": 700,
            }
        }
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
            ) as resp:
                body_text = await resp.text()

                if resp.status != 200:
                    log_fn(
                        f"[race] Gemini/{model} HTTP {resp.status}: "
                        f"{body_text[:120]}"
                    )
                    continue

                import json as _json
                data = _json.loads(body_text)
                try:
                    result = data['candidates'][0]['content']['parts'][0]['text']
                except (KeyError, IndexError):
                    log_fn(f"[race] Gemini/{model}: failed to parse structure.")
                    continue

                valid = _is_valid_persian(result)
                log_fn(
                    f"[race] Gemini/{model}: "
                    f"{len(result or '')}ch | valid={valid}"
                )
                if valid:
                    return result
                continue

        except asyncio.CancelledError:
            log_fn(f"[race] Gemini/{model}: cancelled.")
            raise
        except aiohttp.ClientError as e:
            log_fn(f"[race] Gemini/{model} network error: {e}")
            continue
        except Exception as e:
            log_fn(f"[race] Gemini/{model} error: {type(e).__name__}: {e}")
            continue

    log_fn("[race] Gemini: all models exhausted.")
    return None


async def _parallel_ai_race(
    prompt: str,
    race_timeout: int = AI_RACE_TIMEOUT,
    log_fn=print,
) -> str | None:
    """
    Priority-based AI Dispatch Engine (v13.0)
    First tries Google Gemini (absolute priority).
    Only if Gemini fails or has no key, falls back to concurrent race of Groq & OpenRouter.
    """
    if not prompt or not prompt.strip():
        return None

    connector = aiohttp.TCPConnector(limit=10, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        # STEP 1: Google Gemini (Highest Priority)
        log_fn("[ai] → Gemini (Priority 1) dispatching...")
        try:
            gemini_res = await _call_gemini(session, prompt, log_fn)
            if _is_valid_persian(gemini_res):
                log_fn("[ai] ✓ Gemini Succeeded (Priority 1) — skipping fallbacks.")
                return gemini_res
            else:
                log_fn("[ai] ✗ Gemini failed/invalid. Trying fallbacks (Groq + OpenRouter)...")
        except Exception as e:
            log_fn(f"[ai] ✗ Gemini exception: {type(e).__name__}: {e}. Trying fallbacks...")

        # STEP 2: Fallback Concurrent Race (Groq vs OpenRouter)
        log_fn("[ai] → Running fallback parallel race (Groq vs OpenRouter)...")
        result_queue: asyncio.Queue[str | None] = asyncio.Queue()
        providers = [
            ("Groq",       _call_groq),
            ("OpenRouter", _call_openrouter),
        ]
        total = len(providers)

        async def _worker(name: str, caller_fn):
            try:
                result = await caller_fn(session, prompt, log_fn)
                await result_queue.put(result)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_fn(f"[race] _worker({name}) unhandled error: {e}")
                await result_queue.put(None)

        tasks: list[asyncio.Task] = [
            asyncio.create_task(
                _worker(name, fn),
                name=f"race_{name.lower()}",
            )
            for name, fn in providers
        ]

        winner:     str | None = None
        none_count: int        = 0

        try:
            async with asyncio.timeout(race_timeout):
                while none_count < total:
                    result = await result_queue.get()
                    if _is_valid_persian(result):
                        winner = result
                        log_fn(f"[race] ✓ Fallback Winner: {len(winner)}ch.")
                        break
                    else:
                        none_count += 1
                        log_fn(f"[race] ✗ Fallback Invalid ({none_count}/{total}).")
        except TimeoutError:
            log_fn(f"[race] ✗ Fallback race timed out after {race_timeout}s.")
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        return winner


async def _run_three_races(
    body_prompt: str,
    title_prompt: str,
    tip_prompt: str,
    log_fn=print,
) -> tuple[str | None, str | None, str | None]:
    """Run body + title + tip races concurrently."""
    log_fn("[ai] Starting 3 concurrent AI races...")
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
        log_fn("[ai] Outer race timeout.")
        return None, None, None

    body_fa  = results[0] if isinstance(results[0], str) else None
    title_fa = results[1] if isinstance(results[1], str) else None
    tip_fa   = results[2] if isinstance(results[2], str) else None

    log_fn(
        f"[ai] body={len(body_fa or '')}ch | "
        f"title={len(title_fa or '')}ch | "
        f"tip={len(tip_fa or '')}ch"
    )
    return body_fa, title_fa, tip_fa


# ═══════════════════════════════════════════════════════════
# SECTION 8 — CAPTION BUILDER
# ═══════════════════════════════════════════════════════════

def _build_mehrjameh_caption(
    title_fa: str,
    body_fa: str,
    tip_fa: str,
    hashtags: list[str],
    category: str,
) -> str:
    """
    Mehrjameh editorial caption.
    Format:
      <b>عنوان</b>
      ─────────────
      مد و فشن ایرانی

      خلاصه خبر

      💡 نکته استایلی

      EMOJI  کانال مد و فشن ایرانی

      #hashtags
    """
    def _esc(t: str) -> str:
        return (
            t.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
        )

    CATEGORY_EMOJI = {
        "runway": "👗", "brand": "🏷️", "business": "📊",
        "beauty": "💄", "sustainability": "♻️", "celebrity": "⭐",
        "trend": "🔥", "general": "🌐",
    }
    emoji     = CATEGORY_EMOJI.get(category, "🌐")
    hash_line = " ".join(hashtags)

    header    = f"<b>{_esc(title_fa.strip())}</b>"
    sep       = "─────────────\nمد و فشن ایرانی"
    tip_block = f"💡 {_esc(tip_fa.strip())}" if tip_fa and tip_fa.strip() else ""
    footer    = f"{emoji}  <i>کانال مد و فشن ایرانی</i>"

    # Calculate body budget
    fixed_parts = [header, sep]
    if tip_block:
        fixed_parts.append(tip_block)
    fixed_parts.append(footer)
    if hash_line:
        fixed_parts.append(hash_line)

    separators  = len(fixed_parts) * 2
    fixed_len   = sum(len(p) for p in fixed_parts) + separators
    body_budget = CAPTION_MAX - fixed_len - 4

    safe_body = _esc(body_fa.strip())
    if body_budget <= 10:
        safe_body = ""
        header    = f"<b>{_esc(title_fa.strip())[:80]}</b>"
    elif len(safe_body) > body_budget:
        safe_body = safe_body[:body_budget - 1] + "…"

    parts = [header, sep]
    if safe_body:
        parts.append(safe_body)
    if tip_block:
        parts.append(tip_block)
    parts.append(footer)
    if hash_line:
        parts.append(hash_line)

    caption = "\n\n".join(parts)
    if len(caption) > CAPTION_MAX:
        caption = caption[:CAPTION_MAX - 1] + "…"
    return caption


# ═══════════════════════════════════════════════════════════
# SECTION 9 — MAIN ENTRY POINT
# ═══════════════════════════════════════════════════════════

def _format_unified_caption_safety(
    raw_caption: str,
    hashtags: list[str],
    category: str,
) -> str:
    """
    Cleans up the raw unified caption from the LLM, resolves Markdown blocks,
    ensures proper footer and appends hashtags safely.
    """
    import re
    text = raw_caption.strip()

    # Strip code block markdown if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:html)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    
    text = text.strip()

    # Convert simple ** markdown to <b> tags if any leaked
    parts = text.split("**")
    if len(parts) > 1:
        new_text = []
        for idx, part in enumerate(parts):
            if idx % 2 == 1:
                new_text.append(f"<b>{part}</b>")
            else:
                new_text.append(part)
        text = "".join(new_text)

    # Let's ensure the categories and footer are in good shape
    CATEGORY_EMOJI = {
        "runway": "👗", "brand": "🏷️", "business": "📊",
        "beauty": "💄", "sustainability": "♻️", "celebrity": "⭐",
        "trend": "🔥", "general": "🌐",
    }
    emoji = CATEGORY_EMOJI.get(category, "🌐")

    # If the footer is not already in the text, let's append it
    footer_pattern = "@irfashionnews"
    if footer_pattern not in text:
        text += f"\n\n{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>"

    # Append hashtags if any, and if they aren't already there
    if hashtags:
        hash_line = " ".join(hashtags)
        if not any(tag in text for part in hashtags for tag in [part, part.lower()]):
            text += f"\n\n{hash_line}"

    # Enforce strict maximum length limit for photo captions in Telegram (1024 characters)
    if len(text) > 1020:
        text = text[:1015] + "…"

    return text


async def main(event=None, context=None):
    log   = context.log   if context and hasattr(context, "log")   else print
    error = context.error if context and hasattr(context, "error") else print

    log("═══ FashionBot v13.0 started ═══")

    loop       = asyncio.get_running_loop()
    start_time = loop.time()

    def elapsed() -> str:
        return f"{loop.time() - start_time:.1f}"

    # ── Environment ──
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

    # ── Clients ──
    bot       = Bot(token=token)
    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)
    sdk_mode  = "new" if hasattr(databases, "list_rows") else "legacy"
    log(f"SDK mode: {sdk_mode}")

    # ── Startup: schema detection + key validation ──────────
    log(f"[{elapsed()}s] Detecting schema and validating AI keys...")

    schema, groq_ok, or_ok = await asyncio.gather(
        loop.run_in_executor(
            None, _detect_schema,
            databases, database_id, COLLECTION_ID, sdk_mode, log,
        ),
        _validate_groq_key(log),
        _validate_openrouter_key(log),
    )

    log(
        f"[{elapsed()}s] Schema={schema} | "
        f"Groq={'✓' if groq_ok else '✗'} | "
        f"OpenRouter={'✓' if or_ok else '✗'}"
    )

    if not groq_ok and not or_ok:
        error(
            "No working AI providers. "
            "Check GROQ_API_KEY and OPENROUTER_API_KEY."
        )
        return {
            "status": "error",
            "reason": "no_ai_providers",
        }

    now            = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=ARTICLE_AGE_HOURS)
    current_hour   = now.hour
    is_peak        = current_hour in PEAK_HOURS_UTC
    log(
        f"UTC={current_hour}h | "
        f"Peak={'YES' if is_peak else 'no'}"
    )

    # Load posted-only titles for fuzzy dedup
    recent_titles = _load_recent_titles_posted_only(
        databases, database_id, COLLECTION_ID,
        sdk_mode, FUZZY_LOOKBACK_COUNT, schema, log,
    )
    log(f"[{elapsed()}s] {len(recent_titles)} posted titles loaded.")

    # ════════════════════════════════
    # PHASE 1 — RSS SCAN
    # ════════════════════════════════
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
                schema=schema,
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

    # ════════════════════════════════
    # PHASE 2 — LIGHT DEDUP
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 2: Light dedup...")
    is_dup, dup_reason = _light_duplicate_check(
        databases, database_id, COLLECTION_ID,
        link, content_hash, title_hash, sdk_mode, schema, log,
    )
    if is_dup:
        log(f"[{elapsed()}s] Confirmed dup ({dup_reason}). Skip.")
        return {"status": "success", "posted": False, "reason": dup_reason}

    # ════════════════════════════════
    # PHASE 3 — PARALLEL SCRAPE
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 3: Scraping...")
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
        error(f"[{elapsed()}s] Thin content ({len(content)}ch).")
        return {"status": "skipped", "reason": "thin_content", "posted": False}

    # ════════════════════════════════
    # ════════════════════════════════
    # PHASE 4 — UNIFIED AI GENERATION
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 4: Generating unified premium HTML caption...")
    unified_prompt = _PROMPT_UNIFIED.format(
        title=title[:500],
        input_text=content[:3000],
        category=category,
    )
    caption_raw = await _parallel_ai_race(unified_prompt, AI_RACE_TIMEOUT, log)

    if not caption_raw:
        error(f"[{elapsed()}s] AI Generation failed.")
        return {
            "status": "error",
            "reason": "translation_failed",
            "posted": False,
        }

    # ════════════════════════════════
    # PHASE 5 — FORMAT & SAFETY WRAP
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 5: Formatting and appending hashtags...")
    combined_for_tags = f"{title} {desc} {content[:500]}"
    hashtags = _extract_hashtags_from_text(combined_for_tags)
    
    caption = _format_unified_caption_safety(caption_raw, hashtags, category)
    log(f"[{elapsed()}s] Caption={len(caption)}ch")

    # PHASE 6 — SOFT LOCK WRITE
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 6: Soft lock...")
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
        schema=schema,
        title_hash=title_hash,
        content_hash=content_hash,
        category=category,
        trend_score=score,
        post_hour=current_hour,
        domain_hash=domain_hash,
        log_fn=log,
    )

    if not lock_acquired:
        error(f"[{elapsed()}s] Lock failed ({lock_result}).")
        return {"status": "skipped", "reason": lock_result, "posted": False}

    doc_id = lock_result
    log(f"[{elapsed()}s] Lock acquired. doc_id={doc_id}")

    # ════════════════════════════════
    # PHASE 7 — POST TO TELEGRAM
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 7: Posting...")
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
        error(f"[{elapsed()}s] Telegram: {e}")

    # ════════════════════════════════
    # PHASE 8 — UPDATE DB STATE
    # ════════════════════════════════
    if schema.is_v11:
        if posted:
            _mark_posted(
                databases, database_id, COLLECTION_ID,
                doc_id, sdk_mode, log,
            )
            log(f"[{elapsed()}s] DB → posted=true ✓")
        else:
            _mark_failed(
                databases, database_id, COLLECTION_ID,
                doc_id, sdk_mode,
                reason=post_error or "telegram_failed",
                log_fn=log,
            )
            error(f"[{elapsed()}s] DB → status=failed")
    else:
        log(
            f"[{elapsed()}s] Schema missing v11 fields — "
            f"skipping status update. Run --migrate."
        )

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
        f"═══ v13.0 done in {elapsed()}s | "
        f"{'POSTED ✓' if posted else 'FAILED ✗'} ═══"
    )
    return result


# ═══════════════════════════════════════════════════════════
# SECTION 10 — FEED SCANNING & CANDIDATE SELECTION
# ═══════════════════════════════════════════════════════════

async def _find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, schema, now,
    recent_titles, is_peak, log_fn=print,
):
    loop  = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(
            None, _fetch_feed, url, time_threshold, log_fn
        )
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

    log_fn("[feed] Top 5:")
    for c in all_candidates[:5]:
        log_fn(
            f"  [{c['score']:>3}] [{c['category']:<14}] "
            f"{c['title'][:58]}"
        )

    recent_domain_hashes = _load_recent_domain_hashes(
        databases, database_id, collection_id, sdk_mode, schema, log_fn
    )
    seen_domains: set[str] = set()

    for c in all_candidates:
        link         = c["link"]
        title        = c["title"]
        feed_url     = c["feed_url"]
        domain       = _get_domain(link)
        content_hash = _make_content_hash(title)
        title_hash   = _make_title_hash(title, feed_url)
        domain_hash  = _make_domain_hash(domain)

        # L1: Exact URL
        r = _query_field_safe(
            databases, database_id, collection_id,
            "link", link[:DB_LINK_MAX], sdk_mode, schema, log_fn,
        )
        if r is True:
            log_fn(f"[SKIP] L1: {title[:58]}")
            continue

        # L2: Content hash (if field exists)
        if schema.has_content_hash:
            r = _query_field_safe(
                databases, database_id, collection_id,
                "content_hash", content_hash, sdk_mode, schema, log_fn,
            )
            if r is True:
                log_fn(f"[SKIP] L2: {title[:58]}")
                continue

        # L2b: Title hash (if field exists)
        if schema.has_title_hash:
            r = _query_field_safe(
                databases, database_id, collection_id,
                "title_hash", title_hash, sdk_mode, schema, log_fn,
            )
            if r is True:
                log_fn(f"[SKIP] L2b: {title[:58]}")
                continue

        # L3: Fuzzy
        is_fuzz, matched, fuzz_score = _fuzzy_duplicate(
            title, recent_titles
        )
        if is_fuzz:
            log_fn(
                f"[SKIP] L3 fuzzy={fuzz_score:.2f}: "
                f"{title[:40]} ≈ {(matched or '')[:30]}"
            )
            continue

        # L4b: Domain informational
        if domain_hash in recent_domain_hashes:
            log_fn(f"[INFO] L4b: domain {domain} seen recently.")

        # L4a: One domain per run
        if domain in seen_domains:
            log_fn(f"[SKIP] L4a domain/run: {title[:58]}")
            continue

        seen_domains.add(domain)
        log_fn(f"[PASS] fuzz={fuzz_score:.2f}: {title[:58]}")
        return c

    log_fn("[feed] All candidates exhausted.")
    return None


def _fetch_feed(
    feed_url: str,
    time_threshold: datetime,
    log_fn=print,
) -> list:
    import socket
    try:
        old = socket.getdefaulttimeout()
        socket.setdefaulttimeout(FEED_FETCH_TIMEOUT)
        feed = feedparser.parse(feed_url)
        socket.setdefaulttimeout(old)
    except Exception as e:
        log_fn(f"[feed] feedparser error ({feed_url[:45]}): {e}")
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
            "title": title, "link": link,
            "description": desc, "feed_url": feed_url,
            "pub_date": pub_date, "entry": entry,
            "score": 0, "category": "general",
        })
    return candidates


def _score_article(
    candidate: dict, now: datetime, is_peak: bool = False
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
# SECTION 11 — SCHEMA-ADAPTIVE DEDUPLICATION (FIX 1)
# ═══════════════════════════════════════════════════════════

def _query_field_safe(
    databases,
    database_id: str,
    collection_id: str,
    field: str,
    value: str,
    sdk_mode: str,
    schema: SchemaInfo,
    log_fn=print,
) -> bool | None:
    """
    Query field=value, optionally filtered by posted=true.

    If schema.has_posted → adds posted=True filter (v11 mode).
    If schema missing posted → queries field only (legacy mode,
    more conservative — may skip already-posted articles).

    Returns True (found), False (not found), None (DB error=safe).
    """
    try:
        if schema.has_posted:
            queries = [
                Query.equal(field, value),
                Query.equal("posted", True),
                Query.limit(1),
            ]
        else:
            # Legacy mode: no posted field — query field only
            queries = [
                Query.equal(field, value),
                Query.limit(1),
            ]
        r = _db_list(databases, database_id, collection_id, queries, sdk_mode)
        return r["total"] > 0
    except AppwriteException as e:
        msg = str(e.message).lower()
        if "attribute not found" in msg:
            log_fn(f"[dedup] Field '{field}' not in schema — treating as safe.")
            return False
        log_fn(f"[dedup] _query_field_safe ({field}): {e.message}")
        return None
    except Exception as e:
        log_fn(f"[dedup] _query_field_safe ({field}): {e}")
        return None


def _light_duplicate_check(
    databases,
    database_id: str,
    collection_id: str,
    link: str,
    content_hash: str,
    title_hash: str,
    sdk_mode: str,
    schema: SchemaInfo,
    log_fn=print,
) -> tuple[bool, str]:
    """
    Pre-AI duplicate check.
    v11 schema: blocks posted=true only.
    Legacy schema: blocks any existing link match.
    """
    # Always check link
    r = _query_field_safe(
        databases, database_id, collection_id,
        "link", link[:DB_LINK_MAX], sdk_mode, schema, log_fn,
    )
    if r is True:
        return True, "dup_link"

    # Check content_hash only if field exists
    if schema.has_content_hash:
        r = _query_field_safe(
            databases, database_id, collection_id,
            "content_hash", content_hash, sdk_mode, schema, log_fn,
        )
        if r is True:
            return True, "dup_content_hash"

    # Check title_hash only if field exists
    if schema.has_title_hash:
        r = _query_field_safe(
            databases, database_id, collection_id,
            "title_hash", title_hash, sdk_mode, schema, log_fn,
        )
        if r is True:
            return True, "dup_title_hash"

    return False, ""


def _load_recent_titles_posted_only(
    databases,
    database_id: str,
    collection_id: str,
    sdk_mode: str,
    limit: int,
    schema: SchemaInfo,
    log_fn=print,
) -> list:
    """
    Load recent titles for fuzzy matching.
    v11 schema: posted=true only.
    Legacy schema: all recent records (no posted filter).
    """
    try:
        if schema.has_posted:
            queries = [
                Query.equal("posted", True),
                Query.limit(limit),
                Query.order_desc("$createdAt"),
            ]
        else:
            queries = [
                Query.limit(limit),
                Query.order_desc("$createdAt"),
            ]
        r    = _db_list(databases, database_id, collection_id, queries, sdk_mode)
        docs = r.get("documents", r.get("rows", []))
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
    schema: SchemaInfo,
    log_fn=print,
) -> set:
    cutoff     = datetime.now(timezone.utc) - timedelta(hours=DOMAIN_DEDUP_HOURS)
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    try:
        queries = [
            Query.greater_than("$createdAt", cutoff_str),
            Query.limit(200),
        ]
        if schema.has_posted:
            queries.append(Query.equal("posted", True))
        r    = _db_list(databases, database_id, collection_id, queries, sdk_mode)
        docs = r.get("documents", r.get("rows", []))
        return {d["domain_hash"] for d in docs if d.get("domain_hash")}
    except Exception as e:
        log_fn(f"[dedup] _load_recent_domain_hashes: {e}")
        return set()


# ═══════════════════════════════════════════════════════════
# SECTION 12 — SOFT LOCK & STATE TRANSITIONS
# ═══════════════════════════════════════════════════════════

def _write_soft_lock(
    databases, database_id, collection_id,
    link, title, feed_url, pub_date, source_type,
    sdk_mode, schema: SchemaInfo,
    title_hash, content_hash, category,
    trend_score, post_hour, domain_hash,
    log_fn=print,
) -> tuple[bool, str]:
    """
    Acquire distributed soft lock.
    Adapts payload based on schema fields available.
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
            log_fn("[lock] Already posted — duplicate.")
            return False, "already_posted"

        if existing_status == STATUS_LOCKED and locked_at_str:
            try:
                locked_at = datetime.fromisoformat(
                    locked_at_str.replace("Z", "+00:00")
                )
                age = (now - locked_at).total_seconds()
                if age < LOCK_TTL_SECONDS:
                    log_fn(f"[lock] Active lock (age={age:.0f}s). Skip.")
                    return False, "active_lock"
                else:
                    log_fn(f"[lock] Stale lock (age={age:.0f}s). Recovering.")
                    _delete_record(
                        databases, database_id, collection_id,
                        existing_doc_id, sdk_mode, log_fn,
                    )
            except Exception as e:
                log_fn(f"[lock] TTL parse: {e}. Deleting stale.")
                _delete_record(
                    databases, database_id, collection_id,
                    existing_doc_id, sdk_mode, log_fn,
                )
        elif existing_status == STATUS_FAILED:
            log_fn("[lock] Failed → retry. Deleting old.")
            _delete_record(
                databases, database_id, collection_id,
                existing_doc_id, sdk_mode, log_fn,
            )
        else:
            log_fn(f"[lock] Unknown status '{existing_status}' → stale.")
            _delete_record(
                databases, database_id, collection_id,
                existing_doc_id, sdk_mode, log_fn,
            )

    if pub_date.tzinfo is None:
        pub_date = pub_date.replace(tzinfo=timezone.utc)

    # Build payload — only include fields that exist in schema
    payload: dict = {
        "link":        link[:DB_LINK_MAX],
        "title":       title[:DB_TITLE_MAX],
        "published_at": pub_date.strftime("%Y-%m-%dT%H:%M:%S.000+00:00"),
        "feed_url":    feed_url[:DB_FEED_URL_MAX],
        "source_type": source_type[:DB_SOURCE_TYPE_MAX],
        "category":    category[:DB_CATEGORY_MAX],
        "trend_score": int(trend_score),
        "post_hour":   int(post_hour),
    }

    if schema.has_content_hash:
        payload["content_hash"] = content_hash[:DB_HASH_MAX]
    if schema.has_title_hash:
        payload["title_hash"] = title_hash[:DB_HASH_MAX]
    if schema.has_domain_hash:
        payload["domain_hash"] = domain_hash[:DB_DOMAIN_HASH_MAX]

    # v11 state fields
    if schema.has_status:
        payload["status"] = STATUS_LOCKED
    if schema.has_posted:
        payload["posted"] = False
    if schema.has_locked_at:
        payload["locked_at"] = now_iso
    if schema.has_posted_at:
        payload["posted_at"] = ""
    if schema.has_fail_reason:
        payload["fail_reason"] = ""

    try:
        doc    = _db_create(databases, database_id, collection_id, payload, sdk_mode)
        doc_id = doc.get("$id") or doc.get("id", "")
        log_fn(f"[lock] ✓ Lock acquired. doc_id={doc_id}")
        return True, doc_id
    except AppwriteException as e:
        msg = str(e.message).lower()
        if "already exists" in msg or e.code in (409, 400):
            log_fn("[lock] Race condition — another instance won.")
            return False, "race_lost"
        log_fn(f"[lock] DB error: {e.message}")
        return False, f"db_error: {e.message}"
    except Exception as e:
        log_fn(f"[lock] Error: {e}")
        return False, f"error: {e}"


def _mark_posted(
    databases, database_id, collection_id,
    doc_id, sdk_mode, log_fn=print,
) -> bool:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    return _update_record(
        databases, database_id, collection_id, doc_id, sdk_mode,
        {"status": STATUS_POSTED, "posted": True, "posted_at": now_iso},
        log_fn,
    )


def _mark_failed(
    databases, database_id, collection_id,
    doc_id, sdk_mode, reason, log_fn=print,
) -> bool:
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
    databases, database_id, collection_id,
    doc_id, sdk_mode, fields, log_fn=print,
) -> bool:
    try:
        _db_update(databases, database_id, collection_id, doc_id, fields, sdk_mode)
        log_fn(f"[db] {doc_id} → {list(fields.keys())}")
        return True
    except Exception as e:
        log_fn(f"[db] Update failed ({doc_id}): {e}")
        return False


def _get_existing_record(
    databases, database_id, collection_id,
    link, sdk_mode, log_fn=print,
) -> dict | None:
    try:
        r    = _db_list(
            databases, database_id, collection_id,
            [Query.equal("link", link[:DB_LINK_MAX]), Query.limit(1)],
            sdk_mode,
        )
        docs = r.get("documents", r.get("rows", []))
        return docs[0] if docs else None
    except Exception as e:
        log_fn(f"[db] _get_existing_record: {e}")
        return None


def _delete_record(
    databases, database_id, collection_id,
    doc_id, sdk_mode, log_fn=print,
) -> None:
    try:
        _db_delete(databases, database_id, collection_id, doc_id, sdk_mode)
        log_fn(f"[db] Deleted: {doc_id}")
    except Exception as e:
        log_fn(f"[db] Delete failed ({doc_id}): {e}")


# ═══════════════════════════════════════════════════════════
# SECTION 13 — HASH & FUZZY UTILITIES
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
    title = re.sub(r"[^a-z0-9\s]", " ", title.lower())
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
    incoming = _normalize_tokens(title)
    best     = 0.0
    match    = None
    for stored_title, stored_tokens in recent_titles:
        s = _jaccard(incoming, stored_tokens)
        if s > best:
            best  = s
            match = stored_title
    if best >= FUZZY_SIMILARITY_THRESHOLD:
        return True, match, best
    return False, None, best

def _get_domain(url: str) -> str:
    try:
        parts = urlparse(url).netloc.replace("www.", "").split(".")
        return ".".join(parts[-2:]) if len(parts) >= 2 else url[:30]
    except Exception:
        return url[:30]


# ═══════════════════════════════════════════════════════════
# SECTION 14 — SCRAPING
# ═══════════════════════════════════════════════════════════

def _select_content(
    scraped_text: str | None, description: str, title: str,
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

    log_fn(f"[scrape] Images: {len(images)}")
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
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
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
# SECTION 15 — TELEGRAM POSTING
# ═══════════════════════════════════════════════════════════

async def _post_to_telegram(
    bot: Bot, chat_id: str, caption: str,
    image_urls: list, log_fn=print,
) -> bool:
    posted = False

    if len(image_urls) >= 2:
        try:
            media_group = []
            for idx, url in enumerate(image_urls[:MAX_IMAGES]):
                if idx == 0:
                    media_group.append(InputMediaPhoto(media=url, caption=caption, parse_mode="HTML"))
                else:
                    media_group.append(InputMediaPhoto(media=url))
                    
            await bot.send_media_group(
                chat_id=chat_id, media=media_group,
                disable_notification=True,
            )
            log_fn(f"[tg] Album sent with embedded caption. Images={len(media_group)}")
            posted = True
        except Exception as e:
            log_fn(f"[tg] Album with caption failed: {str(e)[:120]}. Falling back to single photo...")
            if image_urls:
                try:
                    await bot.send_photo(
                        chat_id=chat_id, photo=image_urls[0],
                        caption=caption, parse_mode="HTML",
                        disable_notification=True,
                    )
                    log_fn("[tg] Single photo fallback with caption succeeded.")
                    posted = True
                except Exception as e2:
                    log_fn(f"[tg] Single photo fallback with caption failed: {str(e2)[:80]}")
                    
    elif len(image_urls) == 1:
        try:
            await bot.send_photo(
                chat_id=chat_id, photo=image_urls[0],
                caption=caption, parse_mode="HTML",
                disable_notification=True,
            )
            log_fn("[tg] Single photo sent with embedded caption.")
            posted = True
        except Exception as e:
            log_fn(f"[tg] Single photo with caption failed: {str(e)[:120]}")
            
    if not posted:
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=caption,
                parse_mode="HTML",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                disable_notification=True,
            )
            log_fn("[tg] Sent standalone text caption.")
            posted = True
        except Exception as e:
            log_fn(f"[tg] Standalone text caption failed: {str(e)[:120]}")
            
    return posted


def _run_migrate():
    """
    Add v11 schema fields to the Appwrite collection.
    Fields added: status, posted, locked_at, posted_at, fail_reason.
    Existing fields are not modified.
    """
    print("[migrate] Starting schema migration...")

    aw_client = Client()
    aw_client.set_endpoint(
        os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
    )
    aw_client.set_project(os.environ.get("APPWRITE_PROJECT_ID", ""))
    aw_client.set_key(os.environ.get("APPWRITE_API_KEY", ""))

    from appwrite.services.databases import Databases as _Databases
    databases  = _Databases(aw_client)
    db_id      = os.environ.get("APPWRITE_DATABASE_ID", "")
    col_id     = COLLECTION_ID

    # Field definitions: (key, type, required, default, extra_kwargs)
    FIELDS_TO_ADD = [
        ("status",      "string",  False, STATUS_LOCKED, {"size": 20}),
        ("posted",      "boolean", False, False,         {}),
        ("locked_at",   "string",  False, "",            {"size": 50}),
        ("posted_at",   "string",  False, "",            {"size": 50}),
        ("fail_reason", "string",  False, "",            {"size": 500}),
    ]

    for field_key, field_type, required, default, extra in FIELDS_TO_ADD:
        try:
            if field_type == "string":
                size = extra.get("size", 255)
                databases.create_string_attribute(
                    database_id=db_id,
                    collection_id=col_id,
                    key=field_key,
                    size=size,
                    required=required,
                    default=default,
                )
            elif field_type == "boolean":
                databases.create_boolean_attribute(
                    database_id=db_id,
                    collection_id=col_id,
                    key=field_key,
                    required=required,
                    default=default,
                )
            print(f"[migrate] ✓ Added field: {field_key} ({field_type})")
        except AppwriteException as e:
            if "already exists" in str(e.message).lower():
                print(f"[migrate] ℹ Field already exists: {field_key}")
            else:
                print(f"[migrate] ✗ Failed to add {field_key}: {e.message}")
        except Exception as e:
            print(f"[migrate] ✗ Error adding {field_key}: {e}")

    print("[migrate] Done. Wait ~30s for Appwrite to index new fields.")
    print("[migrate] Then run --cleanup to clear unposted records.")


# ═══════════════════════════════════════════════════════════
# SECTION 17 — CLEANUP UTILITY
# ═══════════════════════════════════════════════════════════

def _run_cleanup():
    """Delete all records where posted != true."""
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
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
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
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", DeprecationWarning)
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

    print(f"[cleanup] Deleted={deleted} | Kept={kept}")


# ═══════════════════════════════════════════════════════════
# LOCAL ENTRY POINT
# ═══════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if "--migrate" in sys.argv:
        # Step 1: Add v11 fields to Appwrite collection
        _run_migrate()

    elif "--cleanup" in sys.argv:
        # Step 2: Clear unposted records (run after --migrate)
        _run_cleanup()

    elif len(sys.argv) > 1 and sys.argv[1].startswith("http"):
        # Test single URL
        url = sys.argv[1]
        print(f"[LOCAL] Testing: {url}")

        async def _test():
            content  = _scrape_text(url) or url[:500]
            body_p   = _PROMPT_BODY.format(input_text=content[:3000])
            title_p  = _PROMPT_TITLE.format(input_text=url[:200])
            tip_p    = _PROMPT_TIP.format(input_text=content[:1500])
            b, t, tip = await _run_three_races(body_p, title_p, tip_p)
            hashtags  = _extract_hashtags_from_text(content[:500])
            caption   = _build_mehrjameh_caption(
                t or "عنوان", b or "متن", tip or "",
                hashtags, "general",
            )
            print(f"\n── CAPTION ({len(caption)}ch) ──\n{caption}\n")

        asyncio.run(_test())

    else:
        asyncio.run(main())
