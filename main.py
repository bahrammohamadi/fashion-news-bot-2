# ============================================================
# Function 1: International Fashion Poster
# Project:    @irfashionnews — FashionBotProject
# Version:    13.2 — Master Universal Thematic & Telegram Engagement Edition
# Runtime:    python-3.12 / Appwrite Cloud Functions
# Timeout:    120 seconds
#
# NEW IN v13.0 (Domain Architect Overhaul):
#   - Core Apparel Focus: Absolute priority for blouses (شومیز), pants (شلوار), skirts (دامن), coats/jackets (کت).
#   - Tracked Brands Expanded: Added premier fashion brands (Armani, Celine, Fendi, Hermes, Jacquemus, Max Mara, Moncler, etc.).
#   - Exquisite Combined Prompts: Single combined prompt structure to guarantee flawless translation AND high-end editorial summary.
#   - Strict General News Throttling: Enforced 12-hour throttling on general industry news to prevent channel spam.
#
# NEW IN v12.2:
#   - FIX: duplicate post/caption bug (album + fallback both sent).
#     * No retry after TimedOut/RetryAfter (message likely delivered)
#     * Image URLs pre-validated so the album rarely fails at all
#     * Larger HTTP timeouts on the Bot client
#   - Product-first selection: new brand products always outrank
#     general news; weak general news (score < MIN_NEWS_SCORE) is skipped.
#   - New env vars: PRODUCT_FIRST (default 1), MIN_NEWS_SCORE (default 70)
#
# NEW IN v12.1:
#   - Enhanced Persian grammar (نیم‌فاصله، ویراستاری)
#   - Improved prompts with نمونه‌های کامل
#   - Better image handling (normalize + dedup)
#   - Stronger celebrity filter
#   - Fixed deprecation warnings
#   - Improved brand scoring
#
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
import logging

# Deep suppress all warnings to avoid Appwrite Native Log interference
warnings.filterwarnings("ignore")
logging.getLogger("requests").setLevel(logging.CRITICAL)
logging.getLogger("urllib3").setLevel(logging.CRITICAL)
def _no_warn(*args, **kwargs): pass
warnings.warn = _no_warn
import feedparser
import aiohttp
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from telegram import Bot, InputMediaPhoto, LinkPreviewOptions, InputPollOption
from telegram.error import TimedOut, RetryAfter, BadRequest
from telegram.request import HTTPXRequest
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query

warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", message=".*urllib3.*")
warnings.filterwarnings("ignore", message=".*chardet.*")
try:
    from requests.packages.urllib3.exceptions import InsecureRequestWarning
    warnings.simplefilter('ignore', InsecureRequestWarning)
except ImportError:
    pass


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
GROQ_MAX_TOKENS  = 900
GROQ_TEMPERATURE = 0.3

# ── OpenRouter (FIX 3) ──
# Free model tried first, paid model as fallback.
OPENROUTER_MODELS = [
    "google/gemini-2.5-flash",                  # Google Gemini 2.5 via OpenRouter (highly robust, bypasses Google 403 blocks!)
    "meta-llama/llama-3.3-70b-instruct",       # Flagship Llama-3.3 70B (exquisite translation quality)
    "meta-llama/llama-3.1-8b-instruct:free",  # free fallback 8B
    "mistralai/mistral-7b-instruct:free",      # mistral free fallback
]
OPENROUTER_MAX_TOKENS  = 900
OPENROUTER_TEMPERATURE = 0.3

# ── Google Gemini ──
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-pro",
]

# ── GitHub Models (Azure AI Inference) ──
GITHUB_MODELS = [
    "gpt-4o",
    "meta-llama-3.3-70b-instruct",
    "gpt-4o-mini",
    "cohere-command-r-plus",
]
GITHUB_MAX_TOKENS  = 900
GITHUB_TEMPERATURE = 0.3

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
SCORE_CORE_APPAREL       = 75
SCORE_TRACKED_BRAND_CORE = 50

# ── Fashion Intelligence Agent - Tracked Entities (v12) ──
TRACKED_BRANDS = {
    "zara", "h&m", "hm", "uniqlo", "mango", "cos", "massimo dutti",
    "nike", "adidas", "puma", "new balance",
    "louis vuitton", "dior", "chanel", "gucci", "prada", "miu miu",
    "saint laurent", "loewe", "coach", "armani", "burberry", "celine",
    "fendi", "givenchy", "hermes", "versace", "bottega veneta", "jacquemus",
    "max mara", "moncler", "ralph lauren", "calvin klein", "tommy hilfiger",
    "stella mccartney", "balenciaga", "valentino", "off-white"
}
TRACKED_MEDIA = {
    "vogue business", "business of fashion", "bof",
    "wwd", "fashion network", "hypebeast", "highsnobiety"
}
PROMPT_MODE = os.environ.get("PROMPT_MODE", "intelligence")  # intelligence | magazine

# ── v12.2: Product-first strategy ──
# PRODUCT_FIRST=1  → always prefer new-product/brand-launch articles;
#                    general news is only posted when no product candidate exists.
# MIN_NEWS_SCORE   → general (non-product) news below this score is skipped
#                    entirely, so the channel isn't flooded with random news.
PRODUCT_FIRST  = os.environ.get("PRODUCT_FIRST", "1").strip() not in ("0", "false", "no")
MIN_NEWS_SCORE = int(os.environ.get("MIN_NEWS_SCORE", "85"))
SCORE_PRODUCT_LAUNCH = 40   # was 15 — strong boost for product launches

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
    "https://www.voguebusiness.com/feed",
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
    "https://hypebeast.com/feed",
    "https://ww.fashionnetwork.com/feed/",
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
# SECTION 2 — AI PROMPT TEMPLATES (v13.2 - EDITORIAL & THEMATIC OPTIMIZED)
# ═══════════════════════════════════════════════════════════

_PROMPT_UNIFIED = '''تو یک استراتژیست ارشد محتوا، توسعه‌دهنده هوشمند مد و سردبیر خلاق مجله لوکس «مهرجامه» (@irfashionnews) در ایران هستی.
وظیفه تو تولید یک پست کامل، متمایز و بسیار جذاب تلگرامی با فرمت HTML بر اساس خبر یا معرفی محصول انگلیسی زیر است تا تعامل مخاطبان ایرانی را به حداکثر برساند.

**فضای فصلی و مناسبتی کنونی کانال:**
{occasion}

**اولویت‌های مطلق محتوا (Core Focus):**
بیشتر محصولات و توجه کانال ما روی **شومیز (blouse)**، **شلوار (pants/trousers)**، **کت و کتونی/بلیزر (coat/jacket/blazer)** و **دامن (skirt)** از برندهای مطرح است. حتماً در خروجی خود روی این ۴ دسته محصول مانور ویژه بده و جذابیت‌های طراحی و استایل آن‌ها را توصیف کن.

**ساختار پرامپت ترکیبی حرفه‌ای (ترجمه دقیق + ویرایش editorial):**
۱. **ترجمه وفادارانه و سلیس:** اصل خبر و نکات فنی را به دقیق‌ترین و روان‌ترین شکل ممکن به فارسی برگردان. از ترجمه ماشینی، تحت‌اللفظی و جملات سنگین یا گنگ به‌شدت اجتناب کن. اصطلاحات تخصصی مد را به بهترین معادل فارسی تبدیل کن یا با املای صحیح لاتین بنویس.
۲. **خلاصه‌سازی حرفه‌ای:** اصل پیام و جذاب‌ترین بخش‌های خبر یا کالکشن را گلچین و خلاصه کن تا در حوصله مخاطب شبکه اجتماعی (تلگرام) بگنجد و زیاده‌گویی نشود.

**قوانین ساختاری و ویراستاری فارسی (سخت‌گیرانه):**
- نیم‌فاصله‌ها را با دقت کامل رعایت کن (می‌شود، برندهای، طراحی‌شده، شومیزهای، کالکشن‌های، می‌پوشد).
- افعال را کامل و کتابی/رسمی بنویس (است، می‌باشد) و از لحن محاوره‌ای یا عامیانه دوری کن.
- نام برندها (مانند Zara, Dior, Chanel, Gucci) حتماً با الفبای لاتین نوشته شوند.
- از «گیومه فارسی» برای نقل‌قول‌ها استفاده کن و تمام اعداد را فارسی بنویس (مانند ۱۰، ۲۰۲۶، ۱۰۰).
- خروجی باید مستقیماً با کدهای HTML تلگرام تگ‌گذاری شده باشد (فقط تگ‌های مجاز <b> و <i>) و هیچ‌گونه تگ اضافه (مانند ```html) نداشته باشد.
- طول کل متن خروجی باید به شدت کنترل شود: **زیر ۱۰۲۰ کاراکتر** (ترجیحاً حداکثر ۸۵۰ کاراکتر) تا در کپشن عکس تلگرام جا شود و قطع نشود.

💎 قالب نهایی تلگرام:
✨ <b>[تیتر جذاب، کوتاه و شیک فارسی با یک ایموجی متناسب]</b>

[دو الی سه جمله ترجمه دقیق و خلاصه editorial (body) بسیار روان و داستان‌گونه از اصل خبر یا معرفی محصول]

🧥 <b>جزئیات طراحی و نوآوری:</b>
[یک پاراگراف کوتاه یا چند بولت‌پوینت مینیمال درباره متریال، برش‌ها یا نکات خاص طراحی به‌ویژه شومیز، شلوار، دامن یا کت]

💡 <b>نکته استایلی (Tip):</b>
[یک پیشنهاد شیک و الهام‌بخش برای ست کردن این آیتم‌ها در استایل روزمره یا رسمی برای مخاطب ایرانی]

{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

خبر انگلیسی:
عنوان: {title}
محتوا: {input_text}'''

_PROMPT_INTELLIGENCE_NEWS = '''تو یک استراتژیست ارشد محتوا، توسعه‌دهنده هوشمند مد و سردبیر خلاق مجله لوکس «مهرجامه» (@irfashionnews) هستی.
ماموریت تو تبدیل متن انگلیسی خبر زیر به یک پست تلگرامی بسیار حرفه‌ای، جذاب، باکیفیت و متمایز به زبان فارسی است تا تعامل مخاطبان ایرانی را حداکثر کند.

**فضای فصلی و مناسبتی کنونی کانال:**
{occasion}

**اولویت‌های مطلق محتوا (Core Focus):**
کانال ما به‌طور ویژه روی **شومیز (blouse)**، **شلوار (pants/trousers)**، **کت و کتونی/بلیزر (coat/jacket/blazer)** و **دامن (skirt)** از برندهای معتبر جهانی تمرکز دارد. 
اگر در متن این خبر یا رویداد عمومی (runway, trend, sustainability, business, celebrity) اشاره‌ای به این آیتم‌ها، ترندهای مرتبط با آن‌ها یا کالکشن‌های جدیدشان شده است، حتماً آن را در کانون توجه تحلیلی خود قرار بده.

**ساختار پرامپت ترکیبی حرفه‌ای (ترجمه دقیق + ویرایش editorial):**
۱. **ترجمه دقیق + روان‌سازی و ویرایش Editorial:** اصل خبر، رویدادهای تجاری/هنری، تحولات برندها و نقل‌قول‌ها را به‌صورت کاملاً دقیق ترجمه کن. به هیچ‌وجه از ترجمه تحت‌اللفظی یا جملات نامفهوم ماشینی استفاده نکن. جملات باید اقتدار و اصالت یک مجله رده‌بالای مد را بازتاب دهند.
۲. **خلاصه‌سازی و چکیده‌نویسی:** بخش‌های حاشیه‌ای و طولانی خبر را حذف کن و مغز متفکر و پیام اصلی خبر را در قالبی خلاصه، کوبنده و مناسب برای دنبال‌کنندگان کانال تلگرام ارائه بده.

**قوانین ویراستاری فارسی (سخت‌گیرانه):**
- رعایت دقیق نیم‌فاصله‌ها (می‌شود، برندهای، می‌پوشد، شرکت‌های، تحولات، ترندهای، کالکشن‌های).
- نگارش رسمی و کامل افعال (است، می‌باشد، اعلام کرد) و اجتناب از واژگان محاوره‌ای.
- درج نام برندها (مانند Zara, Dior, Chanel, Gucci) و اصطلاحات تخصصی با الفبای لاتین.
- استفاده از «گیومه فارسی» و درج اعداد به‌صورت فارسی (مانند ۱۰، ۲۰۲۶، ۵۰).
- خروجی باید مستقیماً با کدهای HTML تلگرام تگ‌گذاری شده باشد (فقط تگ‌های مجاز <b> و <i>) و هیچ‌گونه تگ اضافه (مانند ```html) نداشته باشد.

**ساختار بصری و قالب نهایی تلگرام:**
✨ <b>[تیتر خبری جذاب، کوبنده و کوتاه فارسی با یک ایموجی متناسب]</b>

[دو الی سه جمله ترجمه دقیق و خلاصه editorial (body) سلیس و داستان‌گونه که پیام اصلی خبر را به زیبایی روایت می‌کند]

<b>ابعاد و تحلیل خبر:</b>
[یک پاراگراف کوتاه یا چند بولت‌پوینت تحلیلی درباره پیامدهای این خبر برای ترندها، بازار یا صنعت مد (به‌ویژه شومیز، شلوار، دامن یا کت)]

💡 <b>نکته استایلی / ترند (Tip):</b>
[یک نکته کاربردی، الهام‌بخش یا نتیجه‌گیری جالب برای دنبال‌کنندگان کانال]

<b>منبع:</b> {source}
{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

**کنترل طول متن:**
- طول متن نهایی حتماً باید **زیر ۱۰۲۰ کاراکتر** (ترجیحاً حداکثر ۸۵۰ کاراکتر) باشد تا در کپشن تصاویر تلگرام جا شود و خوانا بماند.

متن انگلیسی خبر:
عنوان: {title}
محتوا: {input_text}'''

_PROMPT_INTELLIGENCE_PRODUCT = '''تو یک استراتژیست ارشد محتوا، توسعه‌دهنده هوشمند مد و سردبیر خلاق مجله لوکس «مهرجامه» (@irfashionnews) هستی.
ماموریت تو تبدیل متن انگلیسی زیر (معرفی محصول یا کالکشن جدید) به یک کپشن تلگرامی بسیار حرفه‌ای، جذاب، باکیفیت و متمایز به زبان فارسی است تا تعامل مخاطبان ایرانی را به حداکثر برساند.

**فضای فصلی و مناسبتی کنونی کانال:**
{occasion}

**اولویت‌های مطلق محتوا (Core Focus):**
محصولات اصلی ما **شومیز (blouse)**، **شلوار (pants/trousers)**، **کت و کتونی/بلیزر (coat/jacket/blazer)** و **دامن (skirt)** هستند. اگر آیتم‌هایی از این ۴ دسته در خبر/کالکشن وجود دارد، بالاترین اولویت و توجه را به آن‌ها اختصاص بده و جزئیات برش، پارچه و استایل آن‌ها را باوقار و لوکس برجسته کن.

**ساختار پرامپت ترکیبی حرفه‌ای (ترجمه دقیق + ویرایش editorial):**
۱. **ترجمه دقیق + روان‌سازی و ویرایش Editorial:** متن را به‌طور دقیق و وفادارانه به فارسی برگردان و با لحن لوکس، شیک و اقتدار یک مجله معتبر مد ویرایش کن. از ترجمه تحت‌اللفظی یا جملات ماشینی بپرهیز. کلمات تخصصی مد را به بهترین معادل فارسی تبدیل کن یا با املای لاتین بنویس.
۲. **رعایت قوانین ویراستاری:** نیم‌فاصله‌ها را کاملاً و با وسواس رعایت کن (می‌شود، برندهای، می‌پوشد، طراحی‌شده، شومیزهای، کالکشن‌های). تمام اعداد فارسی باشند (مانند ۱۰، ۲۰۲۶، ۱۰۰) و از «گیومه فارسی» استفاده شود.

**قالب نهایی تلگرام (فقط تگ‌های HTML شامل <b> و <i>):**
خروجی باید دقیقاً شامل ساختار زیر با ایموجی‌های مینیمال باشد و بدون هیچ تگ اضافه (مثل ```html) تولید شود:

✨ <b>[یک تیتر جذاب، تبلیغاتی، کوتاه و شیک فارسی با یک ایموجی متناسب]</b>

[دو الی سه جمله ترجمه دقیق و خلاصه editorial (body) بسیار روان و جذاب که ماهیت کالکشن، نوآوری برند و محصولات جدید را روایت می‌کند]

<b>جزئیات طراحی و پارچه:</b>
[یک پاراگراف کوتاه یا چند بولت‌پوینت شیک درباره برش‌ها، پالت رنگی یا متریال؛ با تاکید ویژه بر شومیز، شلوار، دامن یا کت]

💡 <b>نکته استایلی (Tip):</b>
[یک پیشنهاد الهام‌بخش، حرفه‌ای و کاربردی برای ست کردن این آیتم‌ها در استایل روزمره یا رسمی برای مخاطب ایرانی]

<b>منبع:</b> {source}
{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

**کنترل طول متن:**
- متن نهایی حتماً باید **زیر ۱۰۲۰ کاراکتر** (ترجیحاً حداکثر ۸۵۰ کاراکتر) باشد تا در کپشن آلبوم تصاویر تلگرام به‌خوبی نمایش داده شود و خوانا بماند.

متن انگلیسی خبر/محصول:
عنوان: {title}
محتوا: {input_text}'''

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
    if hasattr(obj, "to_map"):
        return obj.to_map()
    if hasattr(obj, "documents") and hasattr(obj, "total"):
        return {
            "total": obj.total,
            "documents": [_to_dict_safe(d) for d in obj.documents]
        }
    if hasattr(obj, "__dict__"):
        d = getattr(obj, "__dict__")
        if d: return d
    
    # fallback for objects that use properties but have no dict
    res = {}
    for key in dir(obj):
        if not key.startswith("_") and not callable(getattr(obj, key)):
            val = getattr(obj, key)
            if isinstance(val, list):
                res[key] = [_to_dict_safe(v) for v in val]
            else:
                res[key] = val
    return res


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
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        warnings.filterwarnings("ignore", category=DeprecationWarning, module="appwrite")
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
# SECTION 6 — AI PROVIDER VALIDATION (v13.1)
# ═══════════════════════════════════════════════════════════

async def _validate_groq_key(log_fn=print) -> bool:
    keys = [k for k in [os.environ.get("GROQ_API_KEY", "").strip(), os.environ.get("GROQ_API_KEY2", "").strip()] if k]
    log_fn(f"[startup] Groq available keys: {len(keys)}")
    return len(keys) > 0


async def _validate_openrouter_key(log_fn=print) -> bool:
    keys = [k for k in [os.environ.get("OPENROUTER_API_KEY", "").strip(), os.environ.get("OPENROUTER_API_KEY2", "").strip()] if k]
    log_fn(f"[startup] OpenRouter available keys: {len(keys)}")
    return len(keys) > 0


async def _validate_github_key(log_fn=print) -> bool:
    api_key = os.environ.get("GITHUB_API_KEY_4", "").strip()
    log_fn(f"[startup] GitHub Models key configured: {bool(api_key)}")
    return bool(api_key)


async def _validate_gemini_key(log_fn=print) -> bool:
    api_key = (os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GOOGLE_AI_KEY", "").strip())
    log_fn(f"[startup] Google Gemini key configured: {bool(api_key)}")
    return bool(api_key)


# ═══════════════════════════════════════════════════════════
# SECTION 7 — MASTER PARALLEL AI RACE ENGINE (v13.1)
# ═══════════════════════════════════════════════════════════

async def _call_github(session: aiohttp.ClientSession, prompt: str, log_fn=print) -> str | None:
    api_key = os.environ.get("GITHUB_API_KEY_4", "").strip()
    if not api_key:
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    
    for model in GITHUB_MODELS:
        payload = {
            "model":       model,
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": GITHUB_TEMPERATURE,
            "max_tokens":  GITHUB_MAX_TOKENS,
        }
        try:
            async with session.post(
                "https://models.inference.ai.azure.com/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
            ) as resp:
                body_text = await resp.text()
                if resp.status != 200:
                    log_fn(f"[race] GitHub/{model} HTTP {resp.status}: {body_text[:120]}")
                    continue
                    
                import json as _json
                data   = _json.loads(body_text)
                result = _extract_openai_content(data)
                valid  = _is_valid_persian(result)
                log_fn(f"[race] GitHub/{model}: {len(result or '')}ch | valid={valid}")
                if valid:
                    return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_fn(f"[race] GitHub/{model} error: {type(e).__name__}: {e}")
            continue
            
    return None


async def _call_groq(session: aiohttp.ClientSession, prompt: str, log_fn=print) -> str | None:
    keys = [k for k in [os.environ.get("GROQ_API_KEY", "").strip(), os.environ.get("GROQ_API_KEY2", "").strip()] if k]
    if not keys:
        return None

    for idx, api_key in enumerate(keys):
        key_name = "GROQ_API_KEY" if idx == 0 else f"GROQ_API_KEY{idx+1}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        }
        log_fn(f"[race] Groq: trying key {key_name}...")

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

                    if resp.status == 400 and "decommission" in body_text.lower():
                        continue
                    if resp.status != 200:
                        log_fn(f"[race] Groq/{model} ({key_name}) HTTP {resp.status}: {body_text[:120]}")
                        continue

                    import json as _json
                    data   = _json.loads(body_text)
                    result = _extract_openai_content(data)
                    valid  = _is_valid_persian(result)
                    log_fn(f"[race] Groq/{model} ({key_name}): {len(result or '')}ch | valid={valid}")
                    if valid:
                        return result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_fn(f"[race] Groq/{model} ({key_name}) error: {type(e).__name__}: {e}")
                continue

    return None


async def _call_openrouter(session: aiohttp.ClientSession, prompt: str, log_fn=print) -> str | None:
    keys = [k for k in [os.environ.get("OPENROUTER_API_KEY", "").strip(), os.environ.get("OPENROUTER_API_KEY2", "").strip()] if k]
    if not keys:
        return None

    for idx, api_key in enumerate(keys):
        key_name = "OPENROUTER_API_KEY" if idx == 0 else f"OPENROUTER_API_KEY{idx+1}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://t.me/irfashionnews",
            "X-Title":       "IrFashionNews",
        }
        log_fn(f"[race] OpenRouter: trying key {key_name}...")

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
                        log_fn(f"[race] OpenRouter ({key_name}): 401 invalid key — skipping this key.")
                        break  # inner loop break to try next API key
                    if resp.status == 402:
                        continue
                    if resp.status != 200:
                        log_fn(f"[race] OpenRouter/{model} ({key_name}) HTTP {resp.status}: {body_text[:120]}")
                        continue

                    import json as _json
                    data   = _json.loads(body_text)
                    result = _extract_openai_content(data)
                    valid  = _is_valid_persian(result)
                    log_fn(f"[race] OpenRouter/{model} ({key_name}): {len(result or '')}ch | valid={valid}")
                    if valid:
                        return result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_fn(f"[race] OpenRouter/{model} ({key_name}) error: {type(e).__name__}: {e}")
                continue

    return None


async def _call_gemini(session, prompt: str, log_fn=print) -> str | None:
    api_key = (os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GOOGLE_AI_KEY", "").strip())
    if not api_key:
        return None

    headers = {"Content-Type": "application/json"}

    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 900}
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
                    log_fn(f"[race] Gemini/{model} HTTP {resp.status}: {body_text[:120]}")
                    continue

                import json as _json
                data = _json.loads(body_text)
                try:
                    result = data['candidates'][0]['content']['parts'][0]['text']
                except (KeyError, IndexError):
                    continue

                valid = _is_valid_persian(result)
                log_fn(f"[race] Gemini/{model}: {len(result or '')}ch | valid={valid}")
                if valid:
                    return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_fn(f"[race] Gemini/{model} error: {type(e).__name__}: {e}")
            continue

    return None


async def _parallel_ai_race(prompt: str, race_timeout: int = AI_RACE_TIMEOUT, log_fn=print) -> str | None:
    """
    Master Multi-Provider Full Parallel Race Engine (v13.1)
    Simultaneously fires requests across GitHub Models, Google Gemini, Groq, and OpenRouter.
    The absolute first provider to return a valid Persian translation/summary wins instantly,
    and all other pending calls are cancelled.
    """
    if not prompt or not prompt.strip():
        return None

    log_fn("[ai] 🚀 Launching master multi-provider parallel AI race (GitHub vs Gemini vs Groq vs OpenRouter)...")
    connector = aiohttp.TCPConnector(limit=16, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        result_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        
        providers = [
            ("GitHub",     _call_github),
            ("Gemini",     _call_gemini),
            ("Groq",       _call_groq),
            ("OpenRouter", _call_openrouter),
        ]
        total = len(providers)

        async def _worker(name: str, caller_fn):
            try:
                res = await caller_fn(session, prompt, log_fn)
                if res and _is_valid_persian(res):
                    await result_queue.put((name, res))
                else:
                    await result_queue.put((name, ""))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_fn(f"[race] _worker({name}) error: {e}")
                await result_queue.put((name, ""))

        tasks: list[asyncio.Task] = [
            asyncio.create_task(_worker(name, fn), name=f"race_{name.lower()}")
            for name, fn in providers
        ]

        winner: str | None = None
        finished_count: int = 0

        try:
            async with asyncio.timeout(race_timeout):
                while finished_count < total:
                    name, res = await result_queue.get()
                    if res and _is_valid_persian(res):
                        winner = res
                        log_fn(f"[race] 🏆 Fully Parallel Race Winner: {name} ({len(winner)}ch)! Cancelling pending competitors.")
                        break
                    else:
                        finished_count += 1
                        log_fn(f"[race] ✗ Competitor {name} yielded no valid result ({finished_count}/{total}).")
        except TimeoutError:
            log_fn(f"[race] ✗ Master parallel AI race timed out after {race_timeout}s.")
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

    # v12.1: Apply Persian normalization
    text = _normalize_persian_text(text)

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
        text += f"\n\n{emoji} <i>@irfashionnews | دیده‌بان مد</i>"

    # Append hashtags if any, and if they aren't already there
    if hashtags:
        hash_line = " ".join(hashtags)
        if not any(tag in text for part in hashtags for tag in [part, part.lower()]):
            text += f"\n\n{hash_line}"

    # Enforce strict maximum length limit for photo captions in Telegram (1024 characters)
    # Telegram max caption is 1024.
    if len(text) > 1020:
        # Try to find the last double newline before 980 chars
        safe_cut = text.rfind("\n\n", 0, 980)
        if safe_cut == -1:
            # Fallback to last single newline
            safe_cut = text.rfind("\n", 0, 980)
        if safe_cut == -1:
            # Fallback to last space
            safe_cut = text.rfind(" ", 0, 980)
        if safe_cut == -1:
            safe_cut = 980
            
        text = text[:safe_cut] + "…"
        
    # Final safety check: use BeautifulSoup to auto-close any dangling HTML tags
    # so Telegram doesn't crash on "unexpected end tag"
    try:
        from bs4 import BeautifulSoup
        # Replacing linebreaks to preserve them since BS4 might eat them in some contexts
        text = text.replace("\n", "<br>")
        soup = BeautifulSoup(text, 'html.parser')
        text = str(soup).replace("<br>", "\n").replace("<br/>", "\n")
    except Exception:
        pass

    return text


# ═══════════════════════════════════════════════════════════
# NEW: FASHION CALENDAR STRATEGIST (Iranian & Global Occasions & Thematic Engine)
# ═══════════════════════════════════════════════════════════

class FashionCalendarStrategist:
    """
    A powerful Python strategist that calculates Iranian (Jalali/Shamsi) dates,
    identifies national/global occasions (Norooz, Yalda, Met Gala, Fashion Weeks),
    determines seasonal thematic focus, and establishes optimal posting frequencies.
    """
    def __init__(self, now_utc: datetime):
        self.now_ir = now_utc + timedelta(hours=3, minutes=30)
        self.gy     = self.now_ir.year
        self.gm     = self.now_ir.month
        self.gd     = self.now_ir.day
        self.jy, self.jm, self.jd = self._gregorian_to_jalali(self.gy, self.gm, self.gd)

    @staticmethod
    def _gregorian_to_jalali(gy, gm, gd):
        g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
        gy2   = gy if gm > 2 else gy - 1
        days  = (355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1])
        jy    = -1595 + (33 * (days // 12053))
        days %= 12053
        jy   += 4 * (days // 1461)
        days %= 1461
        if days > 365:
            jy  += (days - 1) // 365
            days = (days - 1) % 365
        if days < 186:
            jm = 1 + (days // 31)
            jd = 1 + (days % 31)
        else:
            jm = 7 + ((days - 186) // 30)
            jd = 1 + ((days - 186) % 30)
        return jy, jm, jd

    def get_daily_strategy(self) -> dict:
        occasion_name        = ""
        thematic_focus       = ""
        thematic_keywords    = []
        target_posts_per_day = 4  # Default regular posting frequency (3-5 posts daily)

        # 1. Iranian (Jalali) Occasions & Seasonal Shifts
        if self.jm == 12 and self.jd >= 15:
            occasion_name        = "پیشواز نوروز و خرید بهاری"
            thematic_focus       = "کالکشن‌های عیدانه، استایل بهاری، شومیزهای مجلسی و کت و شلوارهای شیک"
            thematic_keywords    = ["spring", "norooz", "new collection", "blouse", "suit", "بهار", "عید", "شومیز", "کت"]
            target_posts_per_day = 6  # 3-6 posts daily during peak shopping season
        elif self.jm == 1 and self.jd <= 13:
            occasion_name        = "عید نوروز و دید و بازدید"
            thematic_focus       = "استایل شیک و باوقار نوروزی، ترکیب رنگ‌های شاد بهاری، دامن و شومیز"
            thematic_keywords    = ["spring", "floral", "bright", "skirt", "blouse", "نوروز", "بهار", "دامن"]
            target_posts_per_day = 5
        elif self.jm == 9 and self.jd >= 25:
            occasion_name        = "شب یلدا و آغاز زمستان"
            thematic_focus       = "استایل یلدایی، پالت رنگی اصیل (زرشکی، قرمز، سبز)، پالتوهای گرم و پشمی"
            thematic_keywords    = ["red", "green", "velvet", "coat", "winter", "یلدا", "قرمز", "پالتو", "زرشکی"]
            target_posts_per_day = 6
        elif self.jm in (4, 5, 6):
            occasion_name        = "فصل تابستان و پوشاک خنک"
            thematic_focus       = "استایل خنک تابستانی، لباس‌های لینن و پنبه‌ای، شلوار و شومیزهای راحت و روشن"
            thematic_keywords    = ["summer", "linen", "cotton", "light", "pants", "blouse", "تابستان", "خنک", "لینن"]
        elif self.jm in (7, 8):
            occasion_name        = "فصل پاییز و لایه‌لایه‌پوشی"
            thematic_focus       = "استایل پاییزی، ترنچ کت، کت‌های ساختاریافته، رنگ‌های نود و گرم (کرم، قهوه‌ای، آجری)"
            thematic_keywords    = ["autumn", "fall", "trench", "coat", "blazer", "layering", "پاییز", "ترنچ کت", "کت"]
        elif self.jm in (10, 11):
            occasion_name        = "فصل زمستان و پالتوهای لوکس"
            thematic_focus       = "پالتوهای لوکس پشمی، کاپشن‌های شیک، کت و شلوارهای گرم و باوقار"
            thematic_keywords    = ["winter", "wool", "overcoat", "suit", "jacket", "زمستان", "پالتو", "پشمی"]

        # 2. Global Gregorian Events
        if (self.gm == 9 and self.gd >= 5) or (self.gm == 10 and self.gd <= 5):
            occasion_name += " | هفته‌های مد جهانی (Fashion Weeks SS)"
            thematic_keywords.extend(["runway", "fashion week", "ss26", "catwalk", "collection"])
            target_posts_per_day = 6
        elif (self.gm == 2 and self.gd >= 10) or (self.gm == 3 and self.gd <= 10):
            occasion_name += " | هفته‌های مد جهانی (Fashion Weeks FW)"
            thematic_keywords.extend(["runway", "fashion week", "fw26", "catwalk", "collection"])
            target_posts_per_day = 6
        elif self.gm == 5 and 1 <= self.gd <= 7 and self.now_ir.weekday() == 0:
            occasion_name     = "مراسم مت گالا (Met Gala)"
            thematic_focus    = "تحلیل استایل‌های فرش قرمز مت گالا، طراحی‌های آوانگارد و شاهکارهای هنری"
            thematic_keywords.extend(["met gala", "red carpet", "couture", "vogue"])
            target_posts_per_day = 6
        elif self.gm == 8 and self.gd == 21:
            occasion_name     = "روز جهانی مد (World Fashion Day)"
            thematic_focus    = "گرامیداشت اصالت طراحی، تاریخچه خانه‌های مد لوکس و شاهکارهای ماندگار"
            thematic_keywords.extend(["luxury", "iconic", "designer", "vogue"])
            target_posts_per_day = 5
        elif (self.gm == 12 and self.gd >= 20) or (self.gm == 1 and self.gd <= 5):
            occasion_name += " | تعطیلات سال نو میلادی و Resort"
            thematic_keywords.extend(["holiday", "resort", "sparkle", "festive", "party"])
        elif self.gm == 2 and 10 <= self.gd <= 14:
            occasion_name     = "روز ولنتاین (Valentine's)"
            thematic_focus    = "استایل‌های رمانتیک، پالت رنگی قرمز و صورتی، لباس‌های مجلسی و اکسسوری‌های خاص"
            thematic_keywords.extend(["valentine", "red", "pink", "romantic", "gift", "ولنتاین"])

        return {
            "occasion_name":        occasion_name or "عادی (رصد روزانه بازار مد)",
            "thematic_focus":       thematic_focus or "تمرکز بر جدیدترین شومیز، شلوار، دامن و کت‌ها از برندهای معتبر",
            "thematic_keywords":    thematic_keywords,
            "target_posts_per_day": target_posts_per_day,
        }


async def main(event=None, context=None):
    log   = context.log   if context and hasattr(context, "log")   else print
    error = context.error if context and hasattr(context, "error") else print

    log("═══ FashionBot v13.2 Universal Thematic & Engagement Agent started ═══")

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
    # v12.2 FIX: generous timeouts so send_media_group doesn't raise a
    # client-side TimedOut while Telegram has actually delivered the album
    # (this was causing duplicate posts: album + single-photo fallback).
    bot = Bot(
        token=token,
        request=HTTPXRequest(
            connect_timeout=20.0,
            read_timeout=60.0,
            write_timeout=60.0,
            pool_timeout=20.0,
        ),
    )
    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)
    sdk_mode  = "new" if hasattr(databases, "list_rows") else "legacy"
    log(f"SDK mode: {sdk_mode}")

    # ── Startup: schema detection + key validation ──────────
    log(f"[{elapsed()}s] Detecting schema and validating AI keys...")

    schema, groq_ok, or_ok, github_ok, gemini_ok = await asyncio.gather(
        loop.run_in_executor(
            None, _detect_schema,
            databases, database_id, COLLECTION_ID, sdk_mode, log,
        ),
        _validate_groq_key(log),
        _validate_openrouter_key(log),
        _validate_github_key(log),
        _validate_gemini_key(log),
    )

    log(
        f"[{elapsed()}s] Schema={schema} | "
        f"Groq={'✓' if groq_ok else '✗'} | "
        f"OpenRouter={'✓' if or_ok else '✗'} | "
        f"GitHub={'✓' if github_ok else '✗'} | "
        f"Gemini={'✓' if gemini_ok else '✗'}"
    )

    if not any([groq_ok, or_ok, github_ok, gemini_ok]):
        error("No working AI providers found. Please verify your API keys in Appwrite.")
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

    # ═══════════════════════════════════════════════════════════
    # v13.2 UNIVERSAL CONTENT STRATEGIST & ENGAGEMENT ENGINE
    # ═══════════════════════════════════════════════════════════
    strategist   = FashionCalendarStrategist(now)
    cal_strategy = strategist.get_daily_strategy()
    log(f"📅 Universal Strategist: Occasion='{cal_strategy['occasion_name']}' | Recommended Limit={cal_strategy['target_posts_per_day']} posts/day")

    occasion_context = (
        f"مناسبت و تم فصلی کنونی: {cal_strategy['occasion_name']} — {cal_strategy['thematic_focus']}"
        if cal_strategy['occasion_name'] != "عادی (رصد روزانه بازار مد)"
        else "تم روزانه: تمرکز بر جدیدترین شومیز، شلوار، دامن و کت‌ها از برندهای معتبر"
    )

    now_ir          = now + timedelta(hours=3, minutes=30)
    current_hour_ir = now_ir.hour
    
    post_type = "news"
    
    # 1. Morning Greeting Check (8-10 AM IRT)
    if 8 <= current_hour_ir <= 10:
        try:
            r_morning = _db_list(
                databases, database_id, COLLECTION_ID,
                [Query.equal("category", "morning"), Query.greater_than("$createdAt", (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")), Query.limit(1)],
                sdk_mode
            )
            if r_morning.get("total", 0) == 0:
                post_type = "morning"
        except Exception:
            try:
                r_fallback = _db_list(databases, database_id, COLLECTION_ID, [Query.limit(30)], sdk_mode)
                recent_docs = r_fallback.get("documents", r_fallback.get("rows", []))
                cutoff = now - timedelta(hours=12)
                has_recent_morning = False
                for d in recent_docs:
                    if d.get("category") == "morning":
                        cat_time = d.get("$createdAt") or d.get("pub_date") or d.get("posted_at")
                        if cat_time:
                            cat_dt = datetime.fromisoformat(cat_time.replace("Z", "+00:00"))
                            if cat_dt > cutoff:
                                has_recent_morning = True
                                break
                        else:
                            has_recent_morning = True
                if not has_recent_morning:
                    post_type = "morning"
            except Exception:
                pass
            
    # 2. Universal Poll / Quiz Engagement Check ("روزانه یا چند روز یکبار")
    # Tries to post if no poll/quiz was posted in the last 28 hours, or with a 25% chance in active hours (14-22)
    if post_type == "news" and 14 <= current_hour_ir <= 22:
        try:
            r_poll = _db_list(
                databases, database_id, COLLECTION_ID,
                [Query.equal("category", "poll"), Query.greater_than("$createdAt", (now - timedelta(hours=28)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")), Query.limit(1)],
                sdk_mode
            )
            if r_poll.get("total", 0) == 0 or random.random() < 0.25:
                post_type = "poll"
        except Exception:
            try:
                r_fallback = _db_list(databases, database_id, COLLECTION_ID, [Query.limit(50)], sdk_mode)
                recent_docs = r_fallback.get("documents", r_fallback.get("rows", []))
                cutoff = now - timedelta(hours=28)
                has_recent_poll = False
                for d in recent_docs:
                    if d.get("category") == "poll":
                        cat_time = d.get("$createdAt") or d.get("pub_date") or d.get("posted_at")
                        if cat_time:
                            cat_dt = datetime.fromisoformat(cat_time.replace("Z", "+00:00"))
                            if cat_dt > cutoff:
                                has_recent_poll = True
                                break
                        else:
                            has_recent_poll = True
                if not has_recent_poll or random.random() < 0.25:
                    post_type = "poll"
            except Exception:
                pass

    log(f"[{elapsed()}s] Strategist decided post_type: {post_type}")

    if post_type == "morning":
        caption_raw = await _parallel_ai_race(_PROMPT_MORNING_VIBES, AI_RACE_TIMEOUT, log)
        if caption_raw:
            caption = _format_unified_caption_safety(caption_raw, [], "general")
            morning_pic = random.choice([
                "https://images.unsplash.com/photo-1495474472207-47e534d490b2?q=80&w=1024&auto=format&fit=crop",
                "https://images.unsplash.com/photo-1509319117193-57bab727e09d?q=80&w=1024&auto=format&fit=crop",
                "https://images.unsplash.com/photo-1445205170230-053b83016050?q=80&w=1024&auto=format&fit=crop"
            ])
            try:
                await bot.send_photo(chat_id=chat_id, photo=morning_pic, caption=caption, parse_mode="HTML")
                payload = {
                    "link":     f"morning://{now_ir.strftime('%Y-%m-%d')}",
                    "title":    "Morning Vibes",
                    "category": "morning",
                }
                if schema.has_posted: payload["posted"] = True
                if schema.has_status: payload["status"] = STATUS_POSTED
                _db_create(databases, database_id, COLLECTION_ID, payload, sdk_mode)
                log(f"[{elapsed()}s] ☀️ Morning post sent successfully.")
                return {"status": "success", "type": "morning"}
            except Exception as e:
                log(f"[{elapsed()}s] Morning post failed: {e}")
        post_type = "news"

    elif post_type == "poll":
        poll_prompt = _PROMPT_POLL_GENERATOR.format(occasion=occasion_context)
        poll_raw    = await _parallel_ai_race(poll_prompt, AI_RACE_TIMEOUT, log)
        if poll_raw:
            try:
                import json
                poll_text = poll_raw.strip()
                if poll_text.startswith("```"):
                    poll_text = re.sub(r"^```(?:json)?\s*", "", poll_text, flags=re.IGNORECASE)
                    poll_text = re.sub(r"\s*```$", "", poll_text)
                poll_data = json.loads(poll_text)
                
                poll_type = poll_data.get("type", "regular").lower()
                q         = poll_data.get("question", "در یک قرار کاری مهم، کدام آیتم را ترجیح می‌دهید؟")[:300]
                opts      = poll_data.get("options", ["ترنچ کت کلاسیک کرم", "مانتو کتی ساختاریافته", "کت و شلوار اورسایز"])[:10]
                
                correct_id  = None
                explanation = None
                if poll_type == "quiz":
                    correct_id  = poll_data.get("correct_option_id")
                    explanation = poll_data.get("explanation")
                    if correct_id is None or not isinstance(correct_id, int) or correct_id >= len(opts):
                        poll_type = "regular"

                if poll_type == "quiz":
                    await bot.send_poll(
                        chat_id=chat_id,
                        question=q,
                        options=opts,
                        type="quiz",
                        correct_option_id=correct_id,
                        explanation=explanation[:200] if explanation else None,
                    )
                else:
                    await bot.send_poll(
                        chat_id=chat_id,
                        question=q,
                        options=opts,
                        type="regular",
                    )
                
                payload = {
                    "link":     f"poll://{int(now.timestamp())}",
                    "title":    q[:250],
                    "category": "poll",
                }
                if schema.has_posted: payload["posted"] = True
                if schema.has_status: payload["status"] = STATUS_POSTED
                _db_create(databases, database_id, COLLECTION_ID, payload, sdk_mode)
                log(f"[{elapsed()}s] 📊 Poll/Quiz sent successfully: {q[:65]}")
                return {"status": "success", "type": "poll"}
            except Exception as e:
                log(f"[{elapsed()}s] Poll/Quiz post failed: {e}")
        post_type = "news"

    # ═══════════════════════════════════════════════════════════


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
                cal_strategy=cal_strategy,
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
    # ════════════════════════════════
    # PHASE 2.5 — SOFT LOCK WRITE
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 2.5: Soft lock...")
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
        _mark_failed(databases, database_id, COLLECTION_ID, doc_id, sdk_mode, schema, reason="thin_content", log_fn=log)
        return {"status": "skipped", "reason": "thin_content", "posted": False}

    # ════════════════════════════════
    # ════════════════════════════════
    # PHASE 4 — UNIFIED AI GENERATION
    # ════════════════════════════════
    log(f"[{elapsed()}s] Phase 4: Generating caption (mode={PROMPT_MODE})...")
    CATEGORY_EMOJI = {
        "runway": "👗", "brand": "🏷️", "business": "📊",
        "beauty": "💄", "sustainability": "♻️", "celebrity": "⭐",
        "trend": "🔥", "general": "🌐",
    }
    emoji = CATEGORY_EMOJI.get(category, "🌐")
    
    # v12: Intelligence Agent mode
    if PROMPT_MODE == "intelligence":
        is_product = _is_product_launch(title, content)
        if is_product:
            prompt = _PROMPT_INTELLIGENCE_PRODUCT.format(
                title=title[:500],
                input_text=content[:3000],
                source=link,
                emoji=emoji,
                occasion=occasion_context,
            )
            log(f"[{elapsed()}s] Using PRODUCT prompt")
        else:
            prompt = _PROMPT_INTELLIGENCE_NEWS.format(
                title=title[:500],
                input_text=content[:3000],
                source=link,
                emoji=emoji,
                occasion=occasion_context,
            )
            log(f"[{elapsed()}s] Using NEWS prompt")
    else:
        # legacy magazine mode
        prompt = _PROMPT_UNIFIED.format(
            title=title[:500],
            input_text=content[:3000],
            category=category,
            emoji=emoji,
            occasion=occasion_context,
        )
    
    caption_raw = await _parallel_ai_race(prompt, AI_RACE_TIMEOUT, log)

    if not caption_raw:
        error(f"[{elapsed()}s] AI Generation failed.")
        _mark_failed(databases, database_id, COLLECTION_ID, doc_id, sdk_mode, schema, reason="ai_failed", log_fn=log)
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
    if posted:
        _mark_posted(
            databases, database_id, COLLECTION_ID,
            doc_id, sdk_mode, schema, log,
        )
        log(f"[{elapsed()}s] DB → posted=true ✓")
    else:
        _mark_failed(
            databases, database_id, COLLECTION_ID,
            doc_id, sdk_mode, schema,
            reason=post_error or "telegram_failed",
            log_fn=log,
        )
        error(f"[{elapsed()}s] DB → status=failed")
        # Legacy mode: if it failed, delete it so it can be retried
        if not schema.has_status and not schema.has_posted:
            log(f"[{elapsed()}s] Legacy mode: deleting failed lock so it can be retried...")
            _delete_record(databases, database_id, COLLECTION_ID, doc_id, sdk_mode, log)

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
        f"═══ v12.1 done in {elapsed()}s | "
        f"{'POSTED ✓' if posted else 'FAILED ✗'} ═══"
    )
    return result


# ═══════════════════════════════════════════════════════════
# SECTION 10 — FEED SCANNING & CANDIDATE SELECTION
# ═══════════════════════════════════════════════════════════

async def _find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, schema, now,
    recent_titles, is_peak, cal_strategy, log_fn=print,
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
            continue
        if result:
            all_candidates.extend(result)

    log_fn(f"[feed] {len(all_candidates)} articles collected.")
    if not all_candidates:
        return None

    for c in all_candidates:
        c["score"]           = _score_article(c, now, is_peak, cal_strategy, log_fn)
        c["category"]        = _detect_category(c["title"], c["description"])
        c["is_product"]      = _is_product_launch(c["title"], c["description"])
        c["is_core_apparel"] = _is_core_apparel(c["title"], c["description"])

    # ── v13.2: CORE-PRODUCT-FIRST strategy ──
    # Core apparel (Blouse, Pants, Skirt, Coat) from brands outranks other products,
    # which outrank general news. Enforces dynamic post throttling based on seasonal strategy.
    if PRODUCT_FIRST:
        all_candidates.sort(
            key=lambda x: (x["is_core_apparel"], x["is_product"], x["score"]), reverse=True
        )
        n_core     = sum(1 for c in all_candidates if c["is_core_apparel"])
        n_products = sum(1 for c in all_candidates if c["is_product"])
        log_fn(
            f"[feed] Core-Product-first ON: {n_core} core apparel / {n_products} total products / "
            f"{len(all_candidates)} total."
        )
        
        # Enforce News and General Throttling
        if n_products == 0:
            before = len(all_candidates)
            strict_min_score = max(MIN_NEWS_SCORE, 85)
            all_candidates = [
                c for c in all_candidates if c["score"] >= strict_min_score
            ]
            if not all_candidates:
                log_fn("[feed] Nothing strong enough to post. Skipping this run.")
                return None

            try:
                r_recent = _db_list(
                    databases, database_id, collection_id,
                    [Query.limit(20)], sdk_mode
                )
                recent_docs = r_recent.get("documents", r_recent.get("rows", []))
                
                # Dynamic post throttling based on recommended calendar daily limit (3-6 posts daily)
                target_daily      = cal_strategy.get("target_posts_per_day", 4)
                cutoff_any_hours  = max(2.5, 24.0 / target_daily)
                cutoff_news_hours = 12  # Strict 12 hour throttling on general news
                
                skip_news = False
                for d in recent_docs:
                    if not d.get("posted", True) or d.get("status") == "failed":
                        continue
                    
                    cat      = d.get("category", "")
                    cat_time = d.get("$createdAt") or d.get("pub_date") or d.get("posted_at")
                    if not cat_time: continue
                    
                    try:
                        dt = datetime.fromisoformat(cat_time.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        diff_hours = (now - dt).total_seconds() / 3600.0
                        
                        if diff_hours < cutoff_any_hours:
                            log_fn(f"[feed] Throttle: Another post was made {diff_hours:.1f}h ago. Skipping general news.")
                            skip_news = True
                            break
                        if cat not in ("brand", "runway", "morning", "poll") and diff_hours < cutoff_news_hours:
                            log_fn(f"[feed] Throttle: General news was posted {diff_hours:.1f}h ago. Skipping general news.")
                            skip_news = True
                            break
                    except Exception:
                        pass
                        
                if skip_news:
                    return None
            except Exception as e:
                log_fn(f"[feed] Throttle check warning: {e}")
    else:
        all_candidates.sort(key=lambda x: x["score"], reverse=True)

    log_fn("[feed] Top 5:")
    for c in all_candidates[:5]:
        marker = '🧥' if c.get('is_core_apparel') else ('🛍️' if c['is_product'] else '  ')
        log_fn(
            f"  [{c['score']:>3}] [{c['category']:<14}] "
            f"{marker} {c['title'][:58]}"
        )

    recent_domain_hashes = _load_recent_domain_hashes(
        databases, database_id, collection_id, sdk_mode, schema, log_fn
    )
    seen_domains: set[str] = set()

    for c in all_candidates:
        # v12.2: even when products exist but are all duplicates,
        # don't fall back to weak general news.
        if PRODUCT_FIRST and not c.get("is_product") and c["score"] < MIN_NEWS_SCORE:
            log_fn(f"[SKIP] weak news (score={c['score']}<{MIN_NEWS_SCORE}): {c['title'][:50]}")
            continue

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
    candidate: dict, now: datetime, is_peak: bool = False, cal_strategy: dict = None, log_fn=print
) -> int:
    score     = 0
    age_hours = (now - candidate["pub_date"]).total_seconds() / 3600
    title_lower = candidate["title"].lower()
    desc_lower  = candidate["description"].lower()
    combined  = (title_lower + " " + desc_lower)

    # 1. Filter out highly local US/UK discount store news & irrelevant topics
    unwanted_keywords = [
        "walmart", "target store", "kohls", "tj maxx", "marshalls", "kohl's",
        "retail sales", "store closing", "malls closing", "bankruptcy", "nordstrom rack",
        "layoffs", "strike", "amazon warehouse", "retailer", "earnings report", "target's",
        # v12: filter celebrity gossip
        "kardashian dating", "breakup", "divorce", "spotted with", "paparazzi",
        "gossip", "rumor", "affair", "pregnant", "baby bump",
        "tony awards", "oscar", "grammy", "red carpet exclusive", "celebrity spotted"
    ]
    if any(ukw in combined for ukw in unwanted_keywords):
        return 0  # discard immediately by scoring 0

    # 2. Recency scoring
    if age_hours <= 3:
        score += SCORE_RECENCY_MAX
    elif age_hours <= ARTICLE_AGE_HOURS:
        ratio  = 1 - (age_hours - 3) / (ARTICLE_AGE_HOURS - 3)
        score += int(SCORE_RECENCY_MAX * ratio)

    # 3. Product Launch & Brand releases boost (v13.0)
    product_keywords = [
        "sneaker", "handbag", "it-bag", "perfume", "drop", "capsule collection",
        "collaboration", "collab", "limited edition", "watches", "sneakers",
        "fragrance", "jewelry", "bag", "accessory", "accessories",
        "launches", "unveils", "debuts", "drops", "introduces",
        "new collection", "capsule", "lookbook", "apparel", "ready-to-wear", "rtw",
    ]
    if any(pkw in title_lower for pkw in product_keywords):
        score += SCORE_PRODUCT_LAUNCH

    # 3.5 Core Apparel Boost (v13.0 Domain Architect Enhancement: Blouses, Pants, Skirts, Coats)
    core_apparel_keywords = [
        "blouse", "blouses", "shirt", "shirts", "top", "tops", "tunic", "button-down",
        "pants", "trousers", "jeans", "slacks", "shorts", "chinos", "cargos", "leggings",
        "skirt", "skirts", "miniskirt", "midiskirt", "maxiskirt",
        "coat", "coats", "jacket", "jackets", "blazer", "blazers", "suit", "suits",
        "trench coat", "outerwear", "overcoat", "parka", "overcoats", "trench",
    ]
    if any(cak in title_lower or cak in desc_lower for cak in core_apparel_keywords):
        score += SCORE_CORE_APPAREL
        if any(b in combined for b in TRACKED_BRANDS):
            score += SCORE_TRACKED_BRAND_CORE

    # 4. General trend keywords
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

    # v12: boost tracked brands
    is_tracked_brand = any(brand in combined for brand in TRACKED_BRANDS)
    if is_tracked_brand:
        score += 25
    else:
        # Penalize non-tracked brands slightly
        score = max(0, score - 10)
    
    # v12: boost tracked media sources
    if any(media in candidate["feed_url"].lower() for media in ["voguebusiness", "businessoffashion", "wwd", "hypebeast", "highsnobiety", "fashionnetwork"]):
        score += 10

    # 3.8 Thematic Occasion Bonus (v13.2)
    if cal_strategy and cal_strategy.get("thematic_keywords"):
        if any(tkw in combined for tkw in cal_strategy["thematic_keywords"]):
            score += 60

    # Avoid hard cap so we can differentiate between excellent articles
    return score


def _detect_category(title: str, description: str) -> str:
    combined = (title + " " + description).lower()
    # v12.1: Check brands first for better accuracy
    if any(brand in combined for brand in TRACKED_BRANDS):
        # Check if it's about collection/runway specifically
        if any(kw in combined for kw in ["runway", "fashion week", "couture", "show"]):
            return "runway"
        return "brand"
    
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


def _is_core_apparel(title: str, content: str) -> bool:
    """Detect if article is specifically about our core target products (v13):
    Blouses (شومیز), Pants/Trousers (شلوار), Skirts (دامن), or Coats/Jackets/Blazers (کت)."""
    text = (title + " " + content[:300]).lower()
    core_apparel_signals = [
        "blouse", "blouses", "shirt", "shirts", "top", "tops", "tunic",
        "pants", "trousers", "jeans", "slacks", "chinos", "cargos", "shorts",
        "skirt", "skirts", "miniskirt", "midiskirt", "maxiskirt",
        "coat", "coats", "jacket", "jackets", "blazer", "blazers", "suit", "suits",
        "trench coat", "outerwear", "overcoat", "parka", "trench"
    ]
    brand_hit = any(b in text for b in TRACKED_BRANDS)
    apparel_hit = any(signal in text for signal in core_apparel_signals)
    return brand_hit and apparel_hit


def _is_product_launch(title: str, content: str) -> bool:
    """Detect if article is about new product launch (v13)."""
    text = (title + " " + content[:300]).lower()
    product_signals = [
        "launches", "unveils", "debuts", "drops", "introduces",
        "new collection", "capsule", "limited edition",
        "sneaker", "handbag", "bag", "perfume", "fragrance",
        "collaboration", "collab", "lookbook", "apparel", "ready-to-wear", "rtw"
    ]
    brand_hit = any(b in text for b in TRACKED_BRANDS)
    signal_hit = any(s in text for s in product_signals) or _is_core_apparel(title, content)
    return brand_hit and signal_hit


def _normalize_persian_text(text: str) -> str:
    """Apply Persian grammar and orthography fixes (v12.1)."""
    if not text:
        return text
    
    # Fix common spacing issues
    replacements = {
        ' می ': ' می‌',
        ' نمی ': ' نمی‌',
        ' ها ': '‌ها ',
        ' های ': '‌های ',
        ' تر ': '‌تر ',
        ' ترین ': '‌ترین ',
        ' ام ': '‌ام ',
        ' ات ': '‌ات ',
        ' اش ': '‌اش ',
        ' ای ': '‌ای ',
        '0': '۰', '1': '۱', '2': '۲', '3': '۳', '4': '۴',
        '5': '۵', '6': '۶', '7': '۷', '8': '۸', '9': '۹',
        'ي': 'ی', 'ك': 'ک',
    }
    
    for old, new in replacements.items():
        text = text.replace(old, new)
    
    # Fix double spaces (but preserve newlines)
    text = re.sub(r'[ \t]+', ' ', text)
    # Fix punctuation spacing
    text = re.sub(r'[ \t]+([،؛:.!؟])', r'\1', text)
    
    return text.strip()


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
        # We should NOT filter by posted=True here. 
        # If an article is locked, failed, or posted, we want to treat it as duplicate
        # so we don't pick it again and get stuck in infinite retry loops.
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
        
        # Try fetching with order_desc
        try:
            r = _db_list(databases, database_id, collection_id, queries, sdk_mode)
        except Exception:
            # Fallback for old Appwrite instances or missing composite index
            queries = [Query.limit(limit)]
            if schema.has_posted:
                queries.append(Query.equal("posted", True))
            try:
                r = _db_list(databases, database_id, collection_id, queries, sdk_mode)
            except Exception:
                # Absolute fallback
                queries = [Query.limit(limit)]
                r = _db_list(databases, database_id, collection_id, queries, sdk_mode)
            
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
        try:
            r = _db_list(databases, database_id, collection_id, queries, sdk_mode)
        except Exception:
            queries = [Query.limit(200)]
            if schema.has_posted:
                queries.append(Query.equal("posted", True))
            r = _db_list(databases, database_id, collection_id, queries, sdk_mode)
            
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
            if not schema.has_status and not schema.has_posted:
                log_fn("[lock] Legacy record found. Treating as already posted.")
                return False, "already_posted_legacy"
            
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
    doc_id, sdk_mode, schema: SchemaInfo, log_fn=print,
) -> bool:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")
    fields = {}
    if schema.has_status: fields["status"] = STATUS_POSTED
    if schema.has_posted: fields["posted"] = True
    if schema.has_posted_at: fields["posted_at"] = now_iso
    
    if not fields:
        return True
        
    return _update_record(
        databases, database_id, collection_id, doc_id, sdk_mode,
        fields,
        log_fn,
    )


def _mark_failed(
    databases, database_id, collection_id,
    doc_id, sdk_mode, schema: SchemaInfo, reason: str, log_fn=print,
) -> bool:
    fields = {}
    if schema.has_status: fields["status"] = STATUS_FAILED
    if schema.has_posted: fields["posted"] = False
    if schema.has_fail_reason: fields["fail_reason"] = reason[:DB_REASON_MAX]
    
    if not fields:
        return True
        
    return _update_record(
        databases, database_id, collection_id, doc_id, sdk_mode,
        fields, log_fn,
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


def _normalize_image_url(url: str) -> str:
    """
    Removes common resizing parameters from CDN image URLs to get the original,
    highest resolution image. Helps in deduplicating same images of different sizes.
    """
    if not url:
        return ""
    try:
        # Shopify CDN resize parameters
        url = re.sub(r'_(?:[0-9]+x[0-9]*|[0-9]*x[0-9]+|small|medium|large|grande|master)\.(jpe?g|png|webp)', r'.\1', url, flags=re.I)
        # Vogue / Condé Nast CDN resize parameters
        url = re.sub(r'/(?:w_[0-9]+,h_[0-9]+,c_limit|w_[0-9]+,c_limit|h_[0-9]+,c_limit|w_[0-9]+|h_[0-9]+)/', r'/', url, flags=re.I)
        # general width/height query parameters
        parsed = urlparse(url)
        query_parts = []
        if parsed.query:
            for part in parsed.query.split("&"):
                if not any(k in part.lower() for k in ["width", "height", "w", "h", "resize", "size"]):
                    query_parts.append(part)
        new_query = "&".join(query_parts)
        url = parsed._replace(query=new_query).geturl()
    except Exception:
        pass
    return url


def _parse_srcset(srcset: str) -> str | None:
    """
    Parses a srcset attribute and extracts the absolute highest resolution image URL.
    Example input: 'https://example.com/img_320.jpg 320w, https://example.com/img_1200.jpg 1200w'
    """
    if not srcset:
        return None
    try:
        candidates = []
        for part in srcset.split(","):
            subparts = part.strip().split()
            if not subparts:
                continue
            url = subparts[0]
            width = 0
            if len(subparts) > 1:
                w_str = subparts[1].lower()
                if w_str.endswith("w"):
                    try:
                        width = int(w_str[:-1])
                    except ValueError:
                        pass
                elif w_str.endswith("x"):
                    try:
                        width = int(float(w_str[:-1]) * 1000)
                    except ValueError:
                        pass
            candidates.append((url, width))
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]
    except Exception:
        pass
    # Fallback
    try:
        return srcset.split(",")[0].strip().split(" ")[0]
    except Exception:
        return None


def _scrape_images(url: str, rss_entry, log_fn=print) -> list:
    images: list[str] = []
    seen:   set[str]  = set()

    def _add(img_url: str):
        if not img_url: return
        img_url = img_url.strip()
        # v12: normalize to highest resolution
        img_url = _normalize_image_url(img_url)
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
            src = ""
            srcset = img.get("srcset") or img.get("data-srcset")
            if srcset:
                src = _parse_srcset(srcset)
            if not src:
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
                    src = _parse_srcset(srcset)
                    _add(src)
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

def _probe_image_url(url: str, timeout: int = 6) -> bool:
    """Quickly verify that an image URL is actually fetchable by Telegram.

    Telegram rejects the WHOLE media group if even one URL is bad, which
    previously triggered the single-photo fallback and caused the channel
    to receive the same news twice (album + extra photo with a 2nd caption).
    """
    try:
        r = requests.head(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if r.status_code in (405, 403):  # some CDNs block HEAD
            r = requests.get(
                url, timeout=timeout, stream=True, allow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"},
            )
        if r.status_code != 200:
            return False
        ctype = (r.headers.get("Content-Type") or "").lower()
        if ctype and not ctype.startswith("image/"):
            return False
        # Telegram limit for photos by URL is 5 MB
        clen = r.headers.get("Content-Length")
        if clen and int(clen) > 5 * 1024 * 1024:
            return False
        return True
    except Exception:
        return False


async def _post_to_telegram(
    bot: Bot, chat_id: str, caption: str,
    image_urls: list, log_fn=print,
) -> bool:
    """v12.2 — EXACTLY ONE message (one caption) per news item.

    Strategy:
      1. Normalize + dedup + pre-validate image URLs.
      2. Send ONE media group with the caption embedded on the first photo.
      3. NEVER fall back after a TimedOut/RetryAfter — the album may already
         be delivered, and re-sending caused the duplicate-caption bug.
      4. Only on a definitive BadRequest (Telegram rejected the album) do we
         retry once with fewer/safer images, then once as a single photo.
    """
    if not image_urls:
        return False

    loop = asyncio.get_running_loop()

    # ── 1. Normalize + dedup ──
    normalized, seen = [], set()
    for url in image_urls:
        u = _normalize_image_url(url)
        if u and u not in seen:
            seen.add(u)
            normalized.append(u)

    # ── 1b. Pre-validate URLs in parallel (drop dead/oversized images) ──
    try:
        checks = await asyncio.wait_for(
            asyncio.gather(*[
                loop.run_in_executor(None, _probe_image_url, u)
                for u in normalized[:MAX_IMAGES]
            ]),
            timeout=15,
        )
        valid_urls = [u for u, ok in zip(normalized[:MAX_IMAGES], checks) if ok]
    except asyncio.TimeoutError:
        valid_urls = normalized[:MAX_IMAGES]

    if not valid_urls:
        valid_urls = normalized[:1]  # last resort: try the first one anyway
    log_fn(f"[tg] Images: {len(image_urls)} raw → {len(valid_urls)} valid")

    async def _send_album(urls: list) -> bool:
        media_group = [
            InputMediaPhoto(media=u, caption=caption, parse_mode="HTML")
            if i == 0 else InputMediaPhoto(media=u)
            for i, u in enumerate(urls)
        ]
        await bot.send_media_group(
            chat_id=chat_id, media=media_group,
            disable_notification=True,
        )
        log_fn(f"[tg] Album sent (single caption). Images={len(media_group)}")
        return True

    # ── 2. Attempt 1: full album ──
    try:
        return await _send_album(valid_urls)
    except (TimedOut, RetryAfter) as e:
        # CRITICAL: the request may have actually succeeded server-side.
        # Re-sending here is what produced the duplicated caption/post.
        log_fn(
            f"[tg] {type(e).__name__} on album — assuming delivered, "
            f"NOT retrying to avoid duplicate post."
        )
        return True
    except BadRequest as e:
        log_fn(f"[tg] Album rejected: {str(e)[:120]}. Retrying smaller album...")
    except Exception as e:
        log_fn(f"[tg] Album failed: {str(e)[:120]}. Retrying smaller album...")

    # ── 3. Attempt 2: smaller album (first 3 images) ──
    if len(valid_urls) > 1:
        try:
            return await _send_album(valid_urls[:3])
        except (TimedOut, RetryAfter):
            log_fn("[tg] Timeout on retry — assuming delivered, stopping.")
            return True
        except Exception as e:
            log_fn(f"[tg] Smaller album failed: {str(e)[:100]}")

    # ── 4. Attempt 3: single photo (only reached if NO album was sent) ──
    for single_url in valid_urls:
        try:
            await bot.send_photo(
                chat_id=chat_id, photo=single_url,
                caption=caption, parse_mode="HTML",
                disable_notification=True,
            )
            log_fn("[tg] Single photo with caption sent.")
            return True
        except (TimedOut, RetryAfter):
            log_fn("[tg] Timeout on single photo — assuming delivered.")
            return True
        except Exception as e2:
            log_fn(f"[tg] Single photo failed: {str(e2)[:80]}")

    return False

# ═══════════════════════════════════════════════════════════
# SECTION 13 — CONTENT STRATEGIST & ENGAGEMENT (NEW)
# ═══════════════════════════════════════════════════════════

_PROMPT_MORNING_VIBES = '''تو سردبیر خلاق مجله لوکس مد «مهرجامه» (آی‌آر فشن نیوز) هستی.
وظیفه تو نوشتن یک پست کوتاه، انرژی‌بخش و بسیار شیک برای شروع روز (صبح‌بخیر) است.

مخاطب: علاقه‌مندان به مد، استایل، هنر و لایف‌استایل آوانگارد در ایران.

قوانین:
- متن باید احساس طراوت، زیبایی، قدرت و شیک‌بودن را القا کند.
- از یک نقل قول کوتاه از طراحان بزرگ مد (مثل کوکو شنل، کارل لاگرفلد، دیور و...) یا یک جمله مینیمال لایف‌استایل استفاده کن.
- نیم‌فاصله‌ها کاملاً رعایت شود.
- از ایموجی‌های مینیمال مثل ☕️، 🕊️، ✨، 🤍 استفاده کن.
- طول متن بسیار کوتاه باشد (حداکثر ۴۰۰ کاراکتر).
- تگ‌های مجاز HTML فقط <b> و <i> است.

فرمت خروجی تلگرام:
✨ <b>[یک تیتر کوتاه صبحگاهی و شیک]</b>

[متن اصلی شامل یک جمله زیبای فشن/لایف‌استایل و آرزوی یک روز عالی]

[نقل قول کوتاه با فونت کج (ایتالیک)]

{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>
'''

_PROMPT_POLL_GENERATOR = '''تو یک استراتژیست ارشد محتوا، توسعه‌دهنده هوشمند مد، استایلیست و طراح تعامل مجله لوکس «مهرجامه» (@irfashionnews) هستی.
ماموریت تو تولید یک نظرسنجی (Poll) یا کوییز (Quiz) فشن بسیار جذاب، باکیفیت و تعاملی برای مخاطبان ایرانی کانال تلگرام است تا میزان مشارکت (engagement) را به حداکثر برساند.

**فضای مناسبتی و فصلی کنونی کانال:**
{occasion}

**موضوعات استراتژیک پیشنهادی (یکی را با سلیقه حرفه‌ای خود انتخاب کن):**
۱. **دوراهی استایل (Style Dilemma - Core Apparel Focus):** مقایسه دو آیتم پوشیدنی جذاب (مثلاً: «در یک قرار کاری مهم، ترنچ کت کلاسیک را ترجیح می‌دهید یا مانتو کتی ساختاریافته؟» یا «برای استایل روزمره این فصل، کدام مدل شومیز، شلوار یا دامن را می‌پسندید؟»).
۲. **نبرد برندهای مطرح (Brand Battle):** مقایسه سبک طراحی و هویت برندها (مثلاً Zara در برابر Mango یا Chanel در برابر Dior).
۳. **کوییز تخصصی مد (Fashion Quiz):** یک سوال جذاب و آموزنده درباره تاریخچه مد، اصطلاحات فشن، پارچه‌ها یا ترندها (با تعیین گزینه صحیح و ارائه یک جمله توضیح).

**قوانین خروجی تلگرام:**
- خروجی تو باید **فقط و مستقیماً یک ساختار JSON معتبر و تمیز** باشد و هیچ متن اضافه یا تگ ```json در ابتدا و انتها نداشته باشد.
- حداکثر تعداد گزینه‌ها ۱۰ عدد است (ترجیحاً بین ۳ الی ۴ گزینه شیک، وسوسه‌انگیز و کوتاه).
- در صورت انتخاب حالت quiz، حتماً شماره گزینه صحیح (`correct_option_id` از 0) و یک جمله توضیح آموزنده (`explanation`) ارائه بده. در حالت regular این دو فیلد را null بگذار.

ساختار JSON مورد انتظار:
{
  "type": "regular",  // یا "quiz"
  "question": "[سوال جذاب، کوتاه و چالش‌برانگیز فارسی]",
  "options": [
    "[گزینه اول با یک ایموجی شیک]",
    "[گزینه دوم]",
    "[گزینه سوم]"
  ],
  "correct_option_id": 0,  // در حالت regular برابر null و در حالت quiz شماره ایندکس گزینه صحیح
  "explanation": "[در حالت regular برابر null و در حالت quiz یک جمله توضیح آموزنده و الهام‌بخش]"
}'''
