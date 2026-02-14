from datetime import datetime
import feedparser
import requests
import os
import time
import logging
from typing import Optional

# googletrans به شدت ناپایدار است → پیشنهاد جایگزین: deep_translator یا LibreTranslate
# فعلاً همان را نگه داشتیم ولی با fallback و retry
try:
    from googletrans import Translator
except ImportError:
    Translator = None

# logging مناسب برای Appwrite / سرور
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def safe_translate(translator: Optional[Translator], text: str, dest: str = 'fa') -> str:
    """ترجمه امن با retry و fallback"""
    if not text.strip():
        return ""
    if translator is None:
        return text  # fallback به متن اصلی

    for attempt in range(3):
        try:
            return translator.translate(text, dest=dest).text
        except Exception as e:
            logger.warning(f"ترجمه ناموفق (تلاش {attempt+1}): {e}")
            time.sleep(2 ** attempt)  # exponential backoff

    logger.error(f"ترجمه نهایی ناموفق: {text[:50]}...")
    return text  # در بدترین حالت متن اصلی را برگردان


def send_telegram_message(bot_token: str, chat_id: str, text: str, max_length: int = 4000) -> bool:
    """ارسال پیام با retry و مدیریت طول"""
    if len(text) > max_length:
        text = text[:max_length-20] + "..."

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': text,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }

    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=12)
            r.raise_for_status()
            return True
        except Exception as e:
            logger.warning(f"ارسال ناموفق (تلاش {attempt+1}): {e}")
            time.sleep(2 ** attempt + 0.5)

    logger.error(f"ارسال نهایی ناموفق → chat_id: {chat_id}")
    return False


def main(context=None):
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    channel_id = os.getenv('TELEGRAM_CHANNEL_ID')

    if not bot_token or not channel_id:
        logger.error("متغیرهای محیطی TELEGRAM_BOT_TOKEN یا TELEGRAM_CHANNEL_ID تنظیم نشده‌اند")
        return context.res.empty() if context else None

    translator = None
    try:
        translator = Translator()
    except Exception as e:
        logger.warning(f"نمی‌توان Translator را ساخت: {e}")

    feeds = [
        "https://news.google.com/rss/search?q=%D9%85%D8%AF+%D9%81%D8%B4%D9%86+%D8%A7%D8%B3%D8%AA%D8%A7%DB%8C%D9%84&hl=fa&gl=IR&ceid=IR:fa",
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
        "https://feeds.feedburner.com/fibre2fashion/fashion-news",
        "https://www.fashionista.com/.rss/full"
    ]

    posted_count = 0
    MAX_POSTS_PER_RUN = 5

    for feed_url in feeds:
        if posted_count >= MAX_POSTS_PER_RUN:
            break

        try:
            feed = feedparser.parse(feed_url, sanitize_html=True)
            if feed.bozo:
                logger.warning(f"خطا در پارس فید {feed_url}: {feed.bozo_exception}")
                continue
        except Exception as e:
            logger.error(f"خطا در دریافت فید {feed_url}: {e}")
            continue

        for entry in feed.entries[:4]:  # کمی بیشتر می‌خوانیم تا اگر مشکلی بود جایگزین داشته باشیم
            if posted_count >= MAX_POSTS_PER_RUN:
                break

            title = entry.get('title', '').strip()
            summary = (entry.get('summary') or entry.get('description') or '').strip()
            link = entry.get('link', '')

            if not title or not link:
                continue

            trans_title = safe_translate(translator, title)
            trans_summary = safe_translate(translator, summary[:350]) if summary else ""

            message = f"""📰 <b>خبر روز مد و فشن</b>

{trans_title}

{trans_summary}

🔗 <a href="{link}">ادامه خبر</a>

#مد #فشن #استایل_ایرانی #ترند_فصلی #ایران_مد"""

            if send_telegram_message(bot_token, channel_id, message):
                posted_count += 1
                logger.info(f"پست موفق: {trans_title[:60]}...")
            else:
                logger.warning(f"پست ناموفق: {trans_title[:60]}...")

            time.sleep(4.2)  # کمی بیشتر برای جلوگیری از rate-limit

    logger.info(f"اجرای موفق - {posted_count} خبر ارسال شد")
    
    return context.res.empty() if context else {"status": "ok", "posted": posted_count}
