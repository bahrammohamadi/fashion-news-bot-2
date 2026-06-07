# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    13.0 — Unified Creative AI + Embedded Photo Captioning + Dynamic News Formatting
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# FIXES FROM v11.0 & v12.0:
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
#   FIX 5 — APPWRITE OBJECT TO DICT BUG:
#     Problem: 'DocumentList' object has no attribute 'get' or 'not subscriptable'
#     Solution:
#       - Added _to_dict_safe helper to convert all Appwrite SDK response models
#         to python dicts, fixing all database query/locking crashes.
#
#   FIX 6 — PRIORITY GOOGLE GEMINI FLOW:
#     Problem: Rate limit errors on Groq and OpenRouter keys.
#     Solution:
#       - Google Gemini added as Priority 1 (highest priority).
#       - Falls back to Groq/OpenRouter only if Gemini fails or has no key.
#       - Added support for GEMINI_API_KEY, GOOGLE_API_KEY, and GOOGLE_AI_KEY.
#
#   FIX 7 — EMBEDDED CAPTION POSTING:
#     Problem: Images and caption sent separately.
#     Solution:
#       - Captions are now safely embedded inside the first photo of the album
#         or the single photo, as a single unified Telegram post!
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

CATEGORY_KEYWORDS = {
    "runway": [
        "runway", "couture", "show", "collection", "haute", "fashion week",
        "spring 2026", "fall 2026", "parade", "catwalk", "front row",
    ],
    "brand": [
        "gucci", "chanel", "prada", "balenciaga", "louis vuitton", "dior",
        "hermes", "saint laurent", "celine", "loewe", "jacquemus", "versace",
        "bottega veneta", "maison margiela", "miu miu", "fendi", "valentino",
    ],
    "business": [
        "acquisition", "revenue", "ceo", "shares", "lvmh", "kering", "stocks",
        "bof", "market", "sales", "report", "growth", "industry", "appointed",
    ],
    "beauty": [
        "makeup", "cosmetics", "skincare", "lipstick", "perfume", "fragrance",
        "eyeshadow", "beauty trend", "manicure", "hair", "dermatology",
    ],
    "sustainability": [
        "sustainable", "recycled", "eco-friendly", "organic cotton", "vegan",
        "circular", "ethical", "upcycled", "greenwashing", "consignment",
    ],
    "celebrity": [
        "gala", "red carpet", "met gala", "oscars", "zendaya", "hadid",
        "rihanna", "kardashian", "ambassador", "spotted wearing", "style file",
    ],
    "trend": [
        "how to style", "must-have", "aesthetic", "coquette", "mob wife",
        "minimalism", "chic", "wardrobe", "staple", "how to wear", "it-bag",
    ],
}

RSS_FEEDS = [
    "https://www.vogue.com/feed/rss",
    "https://www.harpersbazaar.com/rss/fashion.xml",
    "https://www.elle.com/rss/fashion.xml",
    "https://www.refinery29.com/rss.xml",
    "https://www.whowhatwear.com/rss",
    "https://www.gq.com/feed/style/rss",
    "https://www.cosmopolitan.com/rss/fashion.xml",
    "https://www.instyle.com/rss/fashion.xml",
    "https://www.marieclaire.com/rss/fashion.xml",
    "https://www.vanityfair.com/feed/style/rss",
    "https://www.allure.com/feed/fashion/rss",
    "https://www.teenvogue.com/feed/rss",
    "https://www.highsnobiety.com/feed/",
    "https://hypebeast.com/feed",
    "https://www.wmagazine.com/feed/rss",
    "https://www.lofficielusa.com/rss",
    "https://thecut.com/feed/index.xml",
    "https://www.fashionista.com/.rss/full/",
    "https://www.grailed.com/drycleanonly/feed",
    "https://wwd.com/feed/",
]

TITLE_STOP_WORDS = {
    "the", "a", "an", "and", "but", "or", "for", "nor", "on", "at",
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
            self.has_posted and self.has_status and
            self.has_locked_at and self.has_posted_at and
            self.has_fail_reason
        )

    def __repr__(self):
        return (
            f"SchemaInfo(posted={self.has_posted}, "
            f"status={self.has_status}, "
            f"locked_at={self.has_locked_at}, "
            f"content_hash={self.has_content_hash}, "
            f"title_hash={self.has_title_hash})"
        )


def _detect_schema(
    databases,
    database_id: str,
    collection_id: str,
    log_fn=print,
) -> SchemaInfo:
    """Queries Appwrite collection attributes to map capabilities."""
    info = SchemaInfo()
    try:
        # Appwrite SDK doesn't expose list_attributes natively in some versions.
        # We perform a safe lightweight query to detect existing fields.
        # If the query fails with "attribute not found", that field is absent.
        
        # Link always exists (V10 core)
        info.has_posted      = _test_attribute(databases, database_id, collection_id, "posted")
        info.has_status      = _test_attribute(databases, database_id, collection_id, "status")
        info.has_locked_at   = _test_attribute(databases, database_id, collection_id, "locked_at")
        info.has_posted_at   = _test_attribute(databases, database_id, collection_id, "posted_at")
        info.has_fail_reason = _test_attribute(databases, database_id, collection_id, "fail_reason")
        
        info.has_content_hash = _test_attribute(databases, database_id, collection_id, "content_hash")
        info.has_title_hash   = _test_attribute(databases, database_id, collection_id, "title_hash")
        info.has_domain_hash  = _test_attribute(databases, database_id, collection_id, "domain_hash")

    except Exception as e:
        log_fn(f"[schema] Error detecting: {e}. Defaulting to V10.")
        # Default V10: only has link, title, feed_url, source_type, created_at

    log_fn(f"[schema] Detected: {info}")
    if not info.is_v11:
        log_fn(
            "[schema] WARNING: v11 state fields missing. "
            "Run --migrate to add them. "
            "Falling back to link-only dedup."
        )
    return info


def _test_attribute(databases, db_id: str, coll_id: str, key: str) -> bool:
    """Tests if an attribute can be queried without throwing a 400 error."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            # Query with a dummy value.
            # If the attribute does NOT exist, Appwrite throws 400 AppwriteException.
            # If it exists but is empty, it returns 200 with an empty list.
            databases.list_documents(
                database_id=db_id,
                collection_id=coll_id,
                queries=[Query.equal(key, "dummy_probe_value_safe_to_ignore")],
            )
        return True
    except AppwriteException as e:
        if e.code == 400: # Attribute not found
            return False
        return True # Any other error (unauthorized etc) means the field likely exists
    except Exception:
        return False


def _detect_schema_fields(
    databases,
    database_id: str,
    collection_id: str,
    log_fn=print,
) -> SchemaInfo:
    """Fallback alias."""
    return _detect_schema(databases, database_id, collection_id, log_fn)


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
    # Support instances that have a dictionary structure or __dict__
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
# SECTION 5 — CORE HELPERS
# ═══════════════════════════════════════════════════════════

def _clean_title(title: str) -> str:
    """Normalizes titles to match common format."""
    t = title.strip().lower()
    t = re.sub(r"[^\w\s-]", "", t)
    return re.sub(r"\s+", " ", t)


def _make_content_hash(text: str) -> str:
    """Sha256 hash of normalized text."""
    normalized = re.sub(r"[^\w]", "", text.lower())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _make_title_hash(title: str, feed_url: str) -> str:
    """Sha256 hash of title and feed url."""
    normalized = _clean_title(title)
    combined = f"{normalized}|{feed_url.strip().lower()}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def _make_domain_hash(domain: str) -> str:
    """Sha256 hash of domain."""
    return hashlib.sha256(domain.strip().lower().encode("utf-8")).hexdigest()


def _get_domain(url: str) -> str:
    """Gets the domain from a URL."""
    try:
        parsed = urlparse(url)
        return parsed.netloc.lower()
    except Exception:
        return ""


def _query_field_safe(
    databases, db_id: str, coll_id: str,
    field: str, value: str, has_field: bool, sdk_mode: str,
) -> dict | None:
    """Performs query only if the schema field is detected as active."""
    if not has_field:
        return None
    try:
        res = _db_list(
            databases, db_id, coll_id,
            [Query.equal(field, value)], sdk_mode,
        )
        return res
    except Exception:
        return None


def _detect_sdk_mode(databases, database_id: str, collection_id: str) -> str:
    """Determines if Appwrite SDK uses tablesDB/list_rows or databases/list_documents."""
    try:
        # Check if list_rows exists on databases service
        if hasattr(databases, "list_rows"):
            return "new"
    except Exception:
        pass
    return "legacy"


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
                if not valid:
                    log_fn(
                        f"[startup] Groq key validation failed. "
                        f"HTTP={resp.status}"
                    )
                return valid
    except Exception as e:
        log_fn(f"[startup] Groq key validation error: {e}")
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
                if not valid:
                    log_fn(
                        f"[startup] OpenRouter key validation failed. "
                        f"HTTP={resp.status}"
                    )
                return valid
    except Exception as e:
        log_fn(f"[startup] OpenRouter key validation error: {e}")
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
    Upgraded Luxury Caption Builder (v12.0)
    Format:
      ✦ <b>عنوان</b>
      ─── ⚜ ───
      روایت امروز مد و استایل

      خلاصه خبر

      💡 <b>فرمولوژی استایل:</b>
      نکته استایلی بومی‌سازی شده

      EMOJI @irfashionnews | مجله مد و زیبایی

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

    header    = f"✦ <b>{_esc(title_fa.strip())}</b>"
    sep       = "─── ⚜ ───\n<i>روایت امروز مد و استایل</i>"
    tip_block = f"💡 <b>فرمولوژی استایل:</b>\n{_esc(tip_fa.strip())}" if tip_fa and tip_fa.strip() else ""
    footer    = f"{emoji} <i>@irfashionnews | مجله مد و زیبایی</i>"

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
        header    = f"✦ <b>{_esc(title_fa.strip())[:80]}</b>"
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
# SECTION 9 — PERSIAN VALIDATION & CONTENT EXTRACTION
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
                .get("content")
        )
    except (IndexError, KeyError, AttributeError):
        return None


# ═══════════════════════════════════════════════════════════
# SECTION 10 — FUZZY SIMILARITY & DEDUP ENGINE
# ═══════════════════════════════════════════════════════════

def _levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions  = curr_row[j] + 1
            substitutes = prev_row[j] + (0 if c1 == c2 else 1)
            curr_row.append(min(insertions, deletions, substitutes))
        prev_row = curr_row
        
    return prev_row[-1]


def _fuzzy_similarity(s1: str, s2: str) -> float:
    """Calculates Levenshtein-based similarity ratio."""
    normalized1 = _clean_title(s1)
    normalized2 = _clean_title(s2)
    max_len = max(len(normalized1), len(normalized2))
    if max_len == 0:
        return 1.0
    dist = _levenshtein_distance(normalized1, normalized2)
    return 1.0 - (dist / max_len)


def _load_recent_titles_posted_only(
    databases, db_id: str, coll_id: str,
    sdk_mode: str, count: int = 150,
    schema=None, log_fn=print,
) -> list[str]:
    """
    Loads normalized titles of successfully posted articles.
    If schema.has_posted is True, we filter by posted=True.
    Otherwise, we pull last N records as best effort.
    """
    try:
        queries = [
            Query.order_desc("created_at"),
            Query.limit(count),
        ]
        # v11 schema: blocks posted=true only.
        # Fallback schema: pulls last N of any status.
        if schema and schema.has_status:
            queries.append(Query.equal("status", STATUS_POSTED))
        elif schema and schema.has_posted:
            queries.append(Query.equal("posted", True))

        res = _db_list(databases, db_id, coll_id, queries, sdk_mode)
        docs = res.get("documents", []) if isinstance(res, dict) else []
        titles = []
        for doc in docs:
            # Handle list_rows vs list_documents object model compatibility
            doc_dict = _to_dict_safe(doc)
            title_val = doc_dict.get("title") if isinstance(doc_dict, dict) else None
            if title_val:
                titles.append(_clean_title(title_val))
        return titles
    except Exception as e:
        log_fn(f"[dedup] *load*recent_titles: {e}")
        return []


def _load_recent_domain_hashes(
    databases, db_id: str, coll_id: str,
    sdk_mode: str, hours: int = DOMAIN_DEDUP_HOURS,
    schema=None, log_fn=print,
) -> set[str]:
    """Loads domain hashes posted in the last X hours to prevent flood of same site."""
    if not schema or not schema.has_domain_hash:
        return set()
    try:
        threshold = datetime.now(timezone.utc) - timedelta(hours=hours)
        threshold_iso = threshold.isoformat().replace("+00:00", "Z")
        
        queries = [
            Query.greater_than_equal("created_at", threshold_iso),
            Query.limit(100),
        ]
        if schema.has_status:
            queries.append(Query.equal("status", STATUS_POSTED))
        elif schema.has_posted:
            queries.append(Query.equal("posted", True))

        res = _db_list(databases, db_id, coll_id, queries, sdk_mode)
        docs = res.get("documents", []) if isinstance(res, dict) else []
        hashes = set()
        for doc in docs:
            doc_dict = _to_dict_safe(doc)
            dh_val = doc_dict.get("domain_hash") if isinstance(doc_dict, dict) else None
            if dh_val:
                hashes.add(dh_val)
        return hashes
    except Exception as e:
        log_fn(f"[dedup] *load*recent_domain_hashes: {e}")
        return set()


def _light_duplicate_check(
    databases, db_id: str, coll_id: str,
    link: str, content_hash: str, title_hash: str,
    sdk_mode: str, schema=None, log_fn=print,
) -> tuple[bool, str]:
    """
    Performs O(1) query-based duplicate checks.
    Returns (is_duplicate, reason).
    """
    # 1. Exact URL check (always exists)
    try:
        res = _db_list(
            databases, db_id, coll_id,
            [Query.equal("link", link[:DB_LINK_MAX])],
            sdk_mode,
        )
        docs = res.get("documents", []) if isinstance(res, dict) else []
        if docs:
            return True, "link_exact"
    except Exception as e:
        log_fn(f"[dedup] *query*field_safe (link): {e}")

    # 2. Content Hash check (if exists)
    if schema and schema.has_content_hash:
        try:
            res = _query_field_safe(
                databases, db_id, coll_id, "content_hash",
                content_hash, schema.has_content_hash, sdk_mode,
            )
            docs = res.get("documents", []) if isinstance(res, dict) else []
            if docs:
                return True, "content_hash"
        except Exception as e:
            log_fn(f"[dedup] *query*field_safe (content_hash): {e}")

    # 3. Title Hash check (if exists)
    if schema and schema.has_title_hash:
        try:
            res = _query_field_safe(
                databases, db_id, coll_id, "title_hash",
                title_hash, schema.has_title_hash, sdk_mode,
            )
            docs = res.get("documents", []) if isinstance(res, dict) else []
            if docs:
                return True, "title_hash"
        except Exception as e:
            log_fn(f"[dedup] *query*field_safe (title_hash): {e}")

    return False, ""


# ═══════════════════════════════════════════════════════════
# SECTION 11 — SCORING & CATEGORIZATION
# ═══════════════════════════════════════════════════════════

def _categorize_and_score(title: str, is_peak: bool) -> tuple[str, int]:
    """
    Evaluates feed title, determines best category and assigns score.
    Returns (category, score).
    """
    clean_t = _clean_title(title)
    words   = set(clean_t.split())
    
    cat_hits = {cat: 0 for cat in CATEGORY_KEYWORDS}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if " " in kw:
                if kw in clean_t:
                    cat_hits[cat] += 4
            else:
                if kw in words:
                    cat_hits[cat] += 2

    best_cat = "general"
    max_hits = 0
    for cat, hits in cat_hits.items():
        if hits > max_hits:
            max_hits = hits
            best_cat = cat

    # Base score
    score = 50
    
    # Keyword bonuses
    score += min(max_hits * 10, 40)
    
    # Peak hour bonus
    if is_peak:
        score += PEAK_HOUR_BONUS
        
    # Recency/Source boosts (Fashion capital names)
    capitals = {"paris", "milan", "london", "york", "tokyo"}
    if any(cap in words for cap in capitals):
        score += 10
        
    return best_cat, min(score, 100)


# ═══════════════════════════════════════════════════════════
# SECTION 12 — RSS FEED SCANNER & IMAGE EXTRACTOR
# ═══════════════════════════════════════════════════════════

def _extract_rss_image(entry) -> str | None:
    """Attempts to find image inside feed entry using multiple standard nodes."""
    try:
        # 1. Media content
        if "media_content" in entry:
            for item in entry["media_content"]:
                if "url" in item and item.get("medium") == "image":
                    return item["url"]
                if "url" in item and "image" in item.get("type", ""):
                    return item["url"]
            # Fallback to first item if it has url
            if entry["media_content"] and "url" in entry["media_content"][0]:
                return entry["media_content"][0]["url"]

        # 2. Links
        if "links" in entry:
            for link in entry["links"]:
                if "image" in link.get("type", ""):
                    return link["href"]

        # 3. Media thumbnail
        if "media_thumbnail" in entry and entry["media_thumbnail"]:
            return entry["media_thumbnail"][0]["url"]

        # 4. Enclosures
        if "enclosures" in entry:
            for enc in entry["enclosures"]:
                if "image" in enc.get("type", ""):
                    return enc["href"]

        # 5. HTML content parsing (fallback)
        html_content = ""
        if "content" in entry:
            html_content = entry["content"][0]["value"]
        elif "summary" in entry:
            html_content = entry["summary"]

        if html_content:
            soup = BeautifulSoup(html_content, "html.parser")
            img  = soup.find("img")
            if img and img.get("src"):
                return img["src"]

    except Exception:
        pass
    return None


async def _find_best_candidate(
    feeds: list[str],
    databases,
    database_id: str,
    collection_id: str,
    time_threshold: datetime,
    sdk_mode: str,
    schema=None,
    now: datetime = None,
    recent_titles: list[str] = None,
    is_peak: bool = False,
    log_fn=print,
) -> dict | None:
    """Scans all feeds, filters by age & deduplicates, returns highest scored candidate."""
    if now is None:
        now = datetime.now(timezone.utc)
    if recent_titles is None:
        recent_titles = []

    recent_domains = _load_recent_domain_hashes(
        databases, database_id, collection_id, sdk_mode,
        DOMAIN_DEDUP_HOURS, schema, log_fn,
    )

    candidates = []
    
    # 1. Parse and extract concurrently to avoid delays
    async def _fetch_feed(session: aiohttp.ClientSession, url: str) -> list:
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=FEED_FETCH_TIMEOUT),
            ) as resp:
                text = await resp.text()
                parsed = feedparser.parse(text)
                return parsed.entries
        except Exception as e:
            log_fn(f"[feed] Error parsing {url[:40]}: {e}")
            return []

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks   = [asyncio.create_task(_fetch_feed(session, f)) for f in feeds]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for feed_idx, entries in enumerate(results):
            if not isinstance(entries, list):
                continue
            feed_url = feeds[feed_idx]
            
            for entry in entries:
                title = entry.get("title", "")
                link  = entry.get("link", "")
                
                if not title or not link:
                    continue

                # ── Age check ──
                pub_parsed = entry.get("published_parsed")
                if pub_parsed:
                    pub_dt = datetime(*pub_parsed[:6], tzinfo=timezone.utc)
                else:
                    pub_dt = now

                if pub_dt < time_threshold:
                    continue

                # ── Deduplication checks ──
                norm_title = _clean_title(title)
                
                # Check exact link memory
                # (Query-based is done in Phase 2, this is local memory fuzzy)
                if any(_fuzzy_similarity(norm_title, rt) > FUZZY_SIMILARITY_THRESHOLD for rt in recent_titles):
                    continue

                # Domain limit check (max 1 post per domain per X hours)
                domain = _get_domain(link)
                if domain:
                    dh = _make_domain_hash(domain)
                    if dh in recent_domains:
                        continue

                # ── Scoring ──
                category, score = _categorize_and_score(title, is_peak)
                description = entry.get("summary", "") or entry.get("description", "")
                
                candidates.append({
                    "title":       title,
                    "link":        link,
                    "description": description,
                    "feed_url":    feed_url,
                    "pub_date":    pub_dt.isoformat().replace("+00:00", "Z"),
                    "entry":       entry,
                    "score":       score,
                    "category":    category,
                })

    if not candidates:
        return None

    # Sort descending by score
    candidates.sort(key=lambda x: x["score"], reverse=True)
    
    # Fuzzy filter within top list to make sure we get a safe item
    for cand in candidates:
        # Check exact link again against DB
        try:
            # Query db for current link
            res = _db_list(
                databases, database_id, collection_id,
                [Query.equal("link", cand["link"][:DB_LINK_MAX])],
                sdk_mode,
            )
            docs = res.get("documents", []) if isinstance(res, dict) else []
            if docs:
                continue # exact match, skip
        except Exception:
            pass

        # Candidate is safe and has the highest score
        log_fn(
            f"[PASS] fuzz=0.00: "
            f"{cand['title'][:65]}"
        )
        return cand

    return None


# ═══════════════════════════════════════════════════════════
# SECTION 13 — PARALLEL FULL TEXT SCRAPER & IMAGE SELECTOR
# ═══════════════════════════════════════════════════════════

def _scrape_text(url: str, log_fn=print) -> str | None:
    """Scrapes full text from article to provide dense context to LLM."""
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=SCRAPE_TIMEOUT)
        if resp.status_code != 200:
            return None
            
        soup = BeautifulSoup(resp.text, "lxml")
        
        # Strip script and style elements
        for element in soup(["script", "style", "header", "footer", "nav", "aside"]):
            element.decompose()
            
        # Target main content holders
        body_text = ""
        article = soup.find("article")
        if article:
            body_text = article.get_text(separator=" ")
        else:
            # Fallback to general content divs
            divs = soup.find_all(
                "div",
                class_=re.compile(
                    r"article|body|content|entry|post|story|text",
                    re.IGNORECASE,
                ),
            )
            if divs:
                # Find largest text container
                largest_div = max(divs, key=lambda d: len(d.get_text()))
                body_text = largest_div.get_text(separator=" ")
            else:
                body_text = soup.get_text(separator=" ")

        # Normalize text
        lines = (line.strip() for line in body_text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = "\n".join(chunk for chunk in chunks if chunk)
        
        # Substring to maximum limit
        return text[:MAX_SCRAPED_CHARS]
    except Exception as e:
        log_fn(f"[scrape] Scraper error: {e}")
        return None


def _scrape_images(url: str, rss_entry, log_fn=print) -> list:
    """
    Finds and ranks all available images in the article.
    Puts feed image at top. Finds high-res candidates inside page.
    """
    images = []
    
    # 1. Start with feed image as absolute high-fidelity priority
    rss_img = _extract_rss_image(rss_entry)
    if rss_img:
        images.append(rss_img)

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=SCRAPE_TIMEOUT)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            
            # Find og:image (high fidelity)
            og_img = soup.find("meta", property="og:image")
            if og_img and og_img.get("content"):
                url_og = og_img["content"]
                if url_og not in images:
                    images.append(url_og)

            # Find twitter:image
            tw_img = soup.find("meta", name="twitter:image")
            if tw_img and tw_img.get("content"):
                url_tw = tw_img["content"]
                if url_tw not in images:
                    images.append(url_tw)

            # Extract in-body images, filter small assets (icons, trackers)
            for img in soup.find_all("img"):
                src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
                if not src:
                    continue
                
                # Resolve relative url
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    parsed = urlparse(url)
                    src = f"https://{parsed.netloc}{src}"

                if not src.startswith("http"):
                    continue

                # Filter obvious tracking or layout images
                if any(x in src.lower() for x in ["logo", "icon", "avatar", "sprite", "pixel", "tracker", "spacer"]):
                    continue

                # Filter low resolution placeholders
                width  = img.get("width", "")
                height = img.get("height", "")
                try:
                    if width and int(width) < 150:
                        continue
                    if height and int(height) < 150:
                        continue
                except ValueError:
                    pass

                if src not in images:
                    images.append(src)

    except Exception as e:
        log_fn(f"[scrape] Image scraper error: {e}")

    # Remove duplicates preserving order
    seen = set()
    unique_images = []
    for img in images:
        if img not in seen:
            seen.add(img)
            unique_images.append(img)

    log_fn(f"[scrape] Images: {len(unique_images)}")
    return unique_images


def _select_content(scraped: str | None, desc: str, title: str) -> str:
    """Returns most granular text content available, falling back cleanly."""
    if scraped and len(scraped.strip()) > MIN_CONTENT_CHARS:
        return scraped.strip()
    if desc and len(desc.strip()) > 50:
        # Strip HTML if present in RSS description
        soup = BeautifulSoup(desc, "html.parser")
        text = soup.get_text()
        if len(text.strip()) > 50:
            return text.strip()[:MAX_RSS_CHARS]
    return f"{title}. Full article available on page."


# ═══════════════════════════════════════════════════════════
# SECTION 14 — SOFT LOCK MANAGEMENT
# ═══════════════════════════════════════════════════════════

def _write_soft_lock(
    databases, database_id: str, collection_id: str,
    link: str, title: str, feed_url: str, pub_date: str,
    source_type: str, sdk_mode: str, schema: SchemaInfo,
    title_hash: str, content_hash: str, category: str,
    trend_score: int, post_hour: int, domain_hash: str,
    log_fn=print,
) -> tuple[bool, str]:
    """
    Creates a temporary locked record in the Appwrite database.
    Ensures that parallel cloud instances do not post the same link.
    """
    data = {
        "link":         link[:DB_LINK_MAX],
        "title":        title[:DB_TITLE_MAX],
        "feed_url":     feed_url[:DB_FEED_URL_MAX],
        "source_type":  source_type[:DB_SOURCE_TYPE_MAX],
        "created_at":   datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    # Add v11 fields if present
    if schema.has_status:
        data["status"] = STATUS_LOCKED
    if schema.has_posted:
        data["posted"] = False
    if schema.has_locked_at:
        data["locked_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if schema.has_posted_at:
        data["posted_at"] = ""
    if schema.has_fail_reason:
        data["fail_reason"] = ""
        
    # Hash fields
    if schema.has_content_hash:
        data["content_hash"] = content_hash[:DB_HASH_MAX]
    if schema.has_title_hash:
        data["title_hash"] = title_hash[:DB_HASH_MAX]
    if schema.has_domain_hash:
        data["domain_hash"] = domain_hash[:DB_DOMAIN_HASH_MAX]

    try:
        res = _db_create(databases, database_id, collection_id, data, sdk_mode)
        # Handle list_rows response vs list_documents object model compatibility
        doc_dict = _to_dict_safe(res)
        doc_id = doc_dict.get("$id") if isinstance(doc_dict, dict) else None
        if not doc_id:
            # Fallback for older SDK object modes
            doc_id = getattr(res, "id", None) or getattr(res, "$id", None)
            
        if doc_id:
            return True, doc_id
        return False, "failed_to_retrieve_id"
    except Exception as e:
        log_fn(f"[lock] Error: {e}")
        return False, str(e)


def _update_posted_status(
    databases, database_id: str, collection_id: str,
    doc_id: str, status: str, schema: SchemaInfo,
    sdk_mode: str, fail_reason: str = "",
) -> None:
    """Updates the state of a soft-locked document once completed/failed."""
    data = {}
    if schema.has_status:
        data["status"] = status
    if schema.has_posted:
        data["posted"] = (status == STATUS_POSTED)
    if schema.has_posted_at and status == STATUS_POSTED:
        data["posted_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if schema.has_fail_reason and status == STATUS_FAILED:
        data["fail_reason"] = fail_reason[:DB_REASON_MAX]

    if not data:
        # Schema does not support state fields. If failed, we delete the record so it can be retried.
        if status == STATUS_FAILED:
            try:
                _db_delete(databases, database_id, collection_id, doc_id, sdk_mode)
            except Exception:
                pass
        return

    try:
        _db_update(databases, database_id, collection_id, doc_id, data, sdk_mode)
    except Exception:
        pass


def _clean_stale_locks(
    databases, database_id: str, collection_id: str,
    sdk_mode: str, schema: SchemaInfo, log_fn=print,
) -> None:
    """Cleans up locked items that timed out or failed to post cleanly."""
    if not schema.has_status and not schema.has_posted:
        return
    try:
        # Stale locks are older than 10 mins
        stale_threshold = datetime.now(timezone.utc) - timedelta(seconds=LOCK_TTL_SECONDS)
        stale_threshold_iso = stale_threshold.isoformat().replace("+00:00", "Z")

        # Query all items in locked state
        queries = [
            Query.less_than("created_at", stale_threshold_iso),
            Query.limit(100),
        ]
        if schema.has_status:
            queries.append(Query.equal("status", STATUS_LOCKED))
        elif schema.has_posted:
            queries.append(Query.equal("posted", False))

        res = _db_list(databases, database_id, collection_id, queries, sdk_mode)
        docs = res.get("documents", []) if isinstance(res, dict) else []
        for doc in docs:
            doc_dict = _to_dict_safe(doc)
            doc_id = doc_dict.get("$id") if isinstance(doc_dict, dict) else None
            if doc_id:
                log_fn(f"[lock] Releasing stale lock {doc_id}...")
                _update_posted_status(
                    databases, database_id, collection_id,
                    doc_id, STATUS_FAILED, schema, sdk_mode, "stale_lock_ttl_timeout",
                )
    except Exception as e:
        log_fn(f"[lock] Error cleaning stale locks: {e}")


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


def _extract_hashtags_from_text(text: str, limit: int = 4) -> list[str]:
    """
    Extracts category keywords from English text and converts
    them to premium Persian hashtags.
    """
    t = text.lower()
    mapping = {
        "runway":          "#ران_وی #کت_واک",
        "couture":         "#اوت_کوتور #کوتور",
        "collection":      "#مجموعه_جدید #کلکسیون",
        "show":            "#فشن_شو",
        "gucci":           "#گوچی #Gucci",
        "chanel":          "#شنل #Chanel",
        "prada":           "#پرادا #Prada",
        "balenciaga":      "#بالنسیاگا #Balenciaga",
        "louis vuitton":   "#لویی_ویتون #LouisVuitton",
        "dior":            "#دیور #Dior",
        "hermes":          "#هرمس #Hermes",
        "saint laurent":   "#سن_لوران #SaintLaurent",
        "celine":          "#سلین #Celine",
        "loewe":           "#لوئوه #Loewe",
        "jacquemus":       "#ژاکموس #Jacquemus",
        "makeup":          "#میکاپ #آرایش",
        "skincare":        "#پوست #مراقبت_پوست",
        "perfume":         "#عطر #ادکلن",
        "sustainable":     "#مد_پایدار #محیط_زیست",
        "recycled":        "#مد_پایدار #بازیافت",
        "gala":            "#گالا #جشنواره",
        "red carpet":      "#رد_کارپت #فرش_قرمز",
        "met gala":        "#مت_گالا #MetGala",
        "how to style":    "#نکته_استایل #راهنمای_استایل",
        "aesthetic":       "#آستتیک #زیبایی_شناسی",
        "minimalism":      "#مینیمالیسم #ساده_گرایی",
    }
    
    extracted = []
    for eng, fa_tags in mapping.items():
        if eng in t:
            for tag in fa_tags.split():
                if tag not in extracted:
                    extracted.append(tag)
                    
    # Fallback default hashtags if none matched
    if not extracted:
         extracted = ["#مد #استایل #ترند_فصل #فشن"]
         
    return extracted[:limit]


# ═══════════════════════════════════════════════════════════
# SECTION 16 — SCHEMA MIGRATION UTILITY
#
# Adds v11 schema fields to Appwrite collection (Run once if fields missing)
# Usage: python main.py --migrate
# ═══════════════════════════════════════════════════════════

def _migrate_schema(databases, db_id: str, coll_id: str, log_fn=print) -> None:
    """Creates missing v11 attributes in the Appwrite database schema."""
    log_fn("[migration] Starting schema migration...")
    attributes = [
        ("posted",      "boolean", False),
        ("status",      "string",  False, 20),
        ("locked_at",   "string",  False, 40),
        ("posted_at",   "string",  False, 40),
        ("fail_reason", "string",  False, 500),
        ("content_hash", "string",  False, 100),
        ("title_hash",   "string",  False, 100),
        ("domain_hash",  "string",  False, 100),
    ]

    for attr in attributes:
        name = attr[0]
        kind = attr[1]
        req  = attr[2]
        try:
            log_fn(f"[migration] Adding '{name}' ({kind})...")
            if kind == "boolean":
                databases.create_boolean_attribute(
                    database_id=db_id,
                    collection_id=coll_id,
                    key=name,
                    required=req,
                )
            elif kind == "string":
                size = attr[3]
                databases.create_string_attribute(
                    database_id=db_id,
                    collection_id=coll_id,
                    key=name,
                    size=size,
                    required=req,
                )
            log_fn(f"[migration] Attribute '{name}' created. Wait 5s...")
            import time
            import sys
            # Safe sleep inside Appwrite / local execution to prevent rate limit
            time.sleep(5)
        except AppwriteException as e:
            if e.code == 409: # Already exists
                log_fn(f"[migration] Attribute '{name}' already exists. OK.")
            else:
                log_fn(f"[migration] Error creating attribute '{name}': {e}")
        except Exception as e:
            log_fn(f"[migration] Error creating attribute '{name}': {e}")

    log_fn("[migration] Migration completed successfully.")


def _cleanup_history(databases, db_id: str, coll_id: str, log_fn=print) -> None:
    """Removes failed or stale records from history collection to save space."""
    log_fn("[cleanup] Scanning for failed or unposted stale records...")
    try:
        # Query unposted and failed records older than 48 hours
        threshold = datetime.now(timezone.utc) - timedelta(hours=48)
        threshold_iso = threshold.isoformat().replace("+00:00", "Z")

        # In legacy mode we cannot safely query filters on missing status fields, so we pull last 100 and clean.
        result = databases.list_documents(
            database_id=db_id,
            collection_id=coll_id,
            queries=[
                Query.less_than("created_at", threshold_iso),
                Query.limit(100),
            ]
        )
        docs = result.get("documents", [])
        cleaned = 0
        for doc in docs:
            # Clean up if not posted or if status is failed
            status = doc.get("status") or ""
            posted = doc.get("posted") or False
            doc_id = doc.get("$id")
            
            if status == STATUS_FAILED or (not posted and status != STATUS_POSTED):
                log_fn(f"[cleanup] Deleting stale doc {doc_id}...")
                databases.delete_document(
                    database_id=db_id,
                    collection_id=coll_id,
                    document_id=doc_id,
                )
                cleaned += 1
        log_fn(f"[cleanup] Finished. Cleaned {cleaned} documents.")
    except Exception as e:
        log_fn(f"[cleanup] Error executing cleanup: {e}")


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

    # ════════════════════════════════
    # SETUP & CONFIG
    # ════════════════════════════════
    database_id = os.environ.get("APPWRITE_DATABASE_ID", "").strip()
    token       = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id     = os.environ.get("TELEGRAM_CHANNEL_ID", "").strip()

    if not database_id or not token or not chat_id:
        error(
            "Missing critical variables. Ensure TELEGRAM_BOT_TOKEN, "
            "TELEGRAM_CHANNEL_ID, and APPWRITE_DATABASE_ID are set."
        )
        return {
            "status": "error",
            "reason": "missing_env_variables",
        }

    # Initialize Appwrite client
    client = Client()
    # If executed locally or in context, configure endpoint
    endpoint = os.environ.get("APPWRITE_ENDPOINT", "").strip()
    project  = os.environ.get("APPWRITE_PROJECT_ID", "").strip()
    api_key  = os.environ.get("APPWRITE_API_KEY", "").strip()

    if endpoint:
        client.set_endpoint(endpoint)
    if project:
        client.set_project(project)
    if api_key:
        client.set_key(api_key)

    databases = Databases(client)
    sdk_mode  = _detect_sdk_mode(databases, database_id, COLLECTION_ID)
    log(f"SDK mode: {sdk_mode}")

    # Local event parameters for one-time CLI actions
    is_migration = False
    is_cleanup   = False
    if event and isinstance(event, dict) and "params" in event:
        params       = event.get("params", {})
        is_migration = params.get("migrate") or params.get("migration") or False
        is_cleanup   = params.get("cleanup") or params.get("clear") or False

    # CLI arguments for local terminal executes
    import sys
    if "--migrate" in sys.argv:
        is_migration = True
    if "--cleanup" in sys.argv:
        is_cleanup = True

    if is_migration:
        _migrate_schema(databases, database_id, COLLECTION_ID, log)
        return {"status": "success", "action": "migration"}

    if is_cleanup:
        _cleanup_history(databases, database_id, COLLECTION_ID, log)
        return {"status": "success", "action": "cleanup"}

    # Time tracking
    start_time = datetime.now()
    def elapsed() -> str:
        return f"{(datetime.now() - start_time).total_seconds():.1f}"

    # Verify keys & schema capabilities at start to speed up future cycles
    log(f"[{elapsed()}s] Detecting schema and validating AI keys...")
    schema, groq_ok, or_ok = await asyncio.gather(
        loop.run_in_executor(
            None, _detect_schema,
            databases, database_id, COLLECTION_ID, log,
        ),
        _validate_groq_key(log),
        _validate_openrouter_key(log),
    )

    log(
        f"[{elapsed()}s] Schema={schema} | "
        f"Groq={'✓' if groq_ok else '✗'} | "
        f"OpenRouter={'✓' if or_ok else '✗'}"
    )

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

    # Clean stale locks from previous broken processes (over LOCK_TTL_SECONDS)
    _clean_stale_locks(databases, database_id, COLLECTION_ID, sdk_mode, schema, log)

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
    # PHASE 4 — UNIFIED AI GENERATION
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 4: Generating unified premium HTML caption...")
    CATEGORY_EMOJI = {
        "runway": "👗", "brand": "🏷️", "business": "📊",
        "beauty": "💄", "sustainability": "♻️", "celebrity": "⭐",
        "trend": "🔥", "general": "🌐",
    }
    emoji = CATEGORY_EMOJI.get(category, "🌐")
    unified_prompt = _PROMPT_UNIFIED.format(
        title=title[:500],
        input_text=content[:3000],
        category=category,
        emoji=emoji,
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

    # ════════════════════════════════
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
    # PHASE 7 — FUZZY DEDUP CHECK
    # ════════════════════════════════
    # Double check title similarity again prior to posting
    log(f"[{elapsed()}s] Phase 7: Fuzzy dedup...")
    recent_titles_v2 = _load_recent_titles_posted_only(
        databases, database_id, COLLECTION_ID,
        sdk_mode, FUZZY_LOOKBACK_COUNT, schema, log,
    )
    
    is_fuzzy_dup = False
    match_title  = ""
    norm_title   = _clean_title(title)
    
    for rt in recent_titles_v2:
        sim = _fuzzy_similarity(norm_title, rt)
        if sim > FUZZY_SIMILARITY_THRESHOLD:
            is_fuzzy_dup = True
            match_title  = rt
            break

    if is_fuzzy_dup:
        error(
            f"[{elapsed()}s] Fuzzy duplicate found during lock step! "
            f"Matches: '{match_title[:50]}'. Releasing lock."
        )
        _update_posted_status(
            databases, database_id, COLLECTION_ID,
            doc_id, STATUS_FAILED, schema, sdk_mode, "fuzzy_duplicate_race_loss",
        )
        return {
            "status": "success",
            "posted": False,
            "reason": "fuzzy_duplicate_race_loss",
        }

    # ════════════════════════════════
    # PHASE 8 — POST TO TELEGRAM
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 8: Posting to channel {chat_id[:10]}...")
    bot = Bot(token=token)
    
    try:
        post_success = await asyncio.wait_for(
            _post_to_telegram(bot, chat_id, caption, image_urls, log),
            timeout=TELEGRAM_TIMEOUT,
        )
    except asyncio.TimeoutError:
        error(f"[{elapsed()}s] Telegram post timed out.")
        post_success = False

    if post_success:
        log(f"[{elapsed()}s] ✓ Successfully posted!")
        _update_posted_status(
            databases, database_id, COLLECTION_ID,
            doc_id, STATUS_POSTED, schema, sdk_mode,
        )
        posted = True
    else:
        error(f"[{elapsed()}s] ✗ Telegram post failed.")
        _update_posted_status(
            databases, database_id, COLLECTION_ID,
            doc_id, STATUS_FAILED, schema, sdk_mode, "telegram_posting_timeout_or_error",
        )
        posted = False

    log(
        f"═══ v13.0 done in {elapsed()}s | "
        f"posted={posted} | score={score} ═══"
    )
    return {
        "status": "success",
        "posted": posted,
        "score":  score,
    }


# Global loop tracker for on-demand local debug runs
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

if __name__ == "__main__":
    asyncio.run(main())
