"""
Fashion News Bot - نسخه حرفه‌ای، ماژولار و بدون API خارجی اضافی
- فقط سایت‌های خارجی مد و فشن
- حداکثر ۱ یا ۲ پست در هر اجرا
- حتماً با عکس (RSS یا og:image)
- چک تکراری با Appwrite
- ترجمه و بازنویسی با GapGPT (base_url = https://api.gapgpt.app/v1)
- لاگینگ حرفه‌ای با فایل چرخشی
- مدیریت کامل خطاها و fallback
- کد خوانا، کامنت‌دار و مقیاس‌پذیر
"""

import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from typing import Optional, Dict, Any

from telegram import Bot
from telegram.error import TelegramError
from bs4 import BeautifulSoup
from openai import AsyncOpenAI, OpenAIError
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query

# ====================== تنظیمات اصلی (Config) ======================
class Config:
    """کلاس تنظیمات مرکزی - همه متغیرها اینجا مدیریت می‌شن"""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_CHANNEL_ID: str = os.environ.get('TELEGRAM_CHANNEL_ID', '')

    # GapGPT (سازگار با OpenAI)
    GAPGPT_API_KEY: str = os.environ.get('GAPGPT_API_KEY', '')
    GAPGPT_BASE_URL: str = "https://api.gapgpt.app/v1"

    # Appwrite
    APPWRITE_ENDPOINT: str = os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1')
    APPWRITE_PROJECT_ID: str = os.environ.get('APPWRITE_PROJECT_ID', '')
    APPWRITE_API_KEY: str = os.environ.get('APPWRITE_API_KEY', '')
    APPWRITE_DATABASE_ID: str = os.environ.get('APPWRITE_DATABASE_ID', '')
    COLLECTION_ID: str = 'history'

    # محدودیت‌ها
    MAX_POSTS_PER_RUN: int = 2
    CHECK_DAYS: int = 4
    MAX_RAW_TEXT_LENGTH: int = 1200
    MAX_FINAL_TEXT_LENGTH: int = 420
    HTTP_TIMEOUT: int = 10
    RETRY_ATTEMPTS: int = 2
    SLEEP_BETWEEN_POSTS: float = 4.0

    # مدل‌های پیشنهادی GapGPT (ارزان و فارسی خوب)
    DEFAULT_MODEL: str = "gpt-4o-mini"

    @classmethod
    def validate(cls) -> bool:
        """بررسی اجباری بودن متغیرهای محیطی"""
        missing = []
        for attr in ['TELEGRAM_BOT_TOKEN', 'TELEGRAM_CHANNEL_ID', 'GAPGPT_API_KEY',
                     'APPWRITE_PROJECT_ID', 'APPWRITE_API_KEY', 'APPWRITE_DATABASE_ID']:
            if not getattr(cls, attr):
                missing.append(attr)
        if missing:
            print(f"[CRITICAL] متغیرهای محیطی ناقص: {', '.join(missing)}")
            return False
        return True


# ====================== لاگینگ حرفه‌ای ======================
def setup_logger() -> logging.Logger:
    """راه‌اندازی لاگر با خروجی کنسول + فایل چرخشی"""
    logger = logging.getLogger('FashionNewsBot')
    logger.setLevel(logging.INFO)

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File handler (چرخشی، حداکثر ۵ مگابایت، ۵ نسخه پشتیبان)
    file_handler = RotatingFileHandler(
        filename='fashion_bot.log',
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding='utf-8'
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


logger = setup_logger()


# ====================== فیدهای RSS (فقط خارجی مد و فشن) ======================
RSS_FEEDS = [
    "https://www.vogue.com/feed/rss",
    "https://wwd.com/feed/",
    "https://www.harpersbazaar.com/rss/fashion.xml",
    "https://fashionista.com/feed",
    "https://www.businessoffashion.com/feed/",
    "https://www.elle.com/rss/fashion.xml",
    "https://www.refinery29.com/rss.xml",
    "https://www.thecut.com/feed",
    "https://www.whowhatwear.com/rss",
    "https://www.instyle.com/rss",
    "https://www.marieclaire.com/rss/fashion/",
    "https://www.glamour.com/rss/fashion",
    "https://www.allure.com/rss",
    "https://nylon.com/feed",
    "https://www.highsnobiety.com/feed/",
    "https://hypebeast.com/feed",
    "https://www.ssense.com/en-us/editorial/rss",
    "https://www.dazeddigital.com/rss",
    "https://i-d.vice.com/en/rss",
    "https://www.papermag.com/rss",
]


# ====================== کلاس کلاینت GapGPT ======================
class GapGPTClient:
    """کلاینت ساده برای GapGPT با retry و fallback"""

    def __init__(self):
        if not Config.GAPGPT_API_KEY:
            raise ValueError("GAPGPT_API_KEY تعریف نشده")
        self.client = AsyncOpenAI(
            api_key=Config.GAPGPT_API_KEY,
            base_url=Config.GAPGPT_BASE_URL
        )

    async def chat_completion(self, messages: list, model: str = Config.DEFAULT_MODEL,
                              temperature: float = 0.7, max_tokens: int = 600) -> Optional[str]:
        """ارسال درخواست چت با retry"""
        for attempt in range(3):
            try:
                resp = await self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens
                )
                content = resp.choices[0].message.content.strip()
                if content and len(content) > 30:
                    return content
                logger.warning(f"پاسخ کوتاه یا نامعتبر در تلاش {attempt+1}")
            except OpenAIError as e:
                logger.error(f"خطا GapGPT (تلاش {attempt+1}): {str(e)[:150]}")
                if attempt < 2:
                    await asyncio.sleep(3)
        return None


# ====================== توابع پردازش محتوا ======================
def clean_html(html: str) -> str:
    """پاک کردن کامل HTML و تبدیل به متن ساده"""
    soup = BeautifulSoup(html, 'html.parser')
    text = soup.get_text(separator=' ', strip=True)
    # حذف فاصله‌های اضافی
    text = ' '.join(text.split())
    return text


def get_image_from_rss(entry) -> Optional[str]:
    """استخراج عکس از RSS اگر وجود داشته باشد"""
    if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
        return entry.enclosure.get('href')
    if 'media_content' in entry:
        for media in entry.media_content:
            if media.get('medium') == 'image' and media.get('url'):
                return media.get('url')
    return None


async def extract_og_image(url: str) -> Optional[str]:
    """استخراج عکس از تگ og:image صفحه خبر"""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        resp = requests.get(url, timeout=10, headers=headers)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, 'html.parser')
        og_tag = soup.find('meta', property='og:image')
        if og_tag and og_tag.get('content'):
            img_url = og_tag['content']
            # اگر لینک نسبی بود، کاملش کن
            if img_url.startswith('/'):
                parsed = urlparse(url)
                img_url = urljoin(f"{parsed.scheme}://{parsed.netloc}", img_url)
            return img_url
        return None
    except Exception as e:
        logger.debug(f"خطا استخراج og:image از {url}: {str(e)}")
        return None


async def translate_and_format(client: GapGPTClient, title: str, raw_text: str) -> str:
    """ترجمه + بازنویسی حرفه‌ای با GapGPT"""
    # متن ورودی برای مدل
    input_text = raw_text[:1000] if len(raw_text) > 1000 else raw_text

    prompt = f"""
عنوان خبر: {title}
متن انگلیسی: {input_text}

به عنوان سردبیر حرفه‌ای مجله مد ایرانی:
1. متن را به فارسی روان و شیک ترجمه کن.
2. به یک پست کوتاه و جذاب برای کانال تلگرام تبدیل کن.
- تیتر را جذاب‌تر و کوتاه‌تر کن اگر لازم بود.
- متن را در ۳ تا ۶ خط خلاصه و حرفه‌ای بنویس.
- فقط محتوای اصلی خبر را نگه دار.
- بدون هیچ جمله تبلیغاتی، توضیح اضافی، ایموجی یا لینک.
- لحن رسمی اما گرم و مناسب مخاطب مد.

خروجی دقیقاً این فرمت باشد:
**تیتر فارسی**

متن فارسی (۳-۶ خط)
"""

    result = await client.chat_completion(
        messages=[{"role": "user", "content": prompt}],
        model="gpt-4o-mini",
        temperature=0.7,
        max_tokens=600
    )

    if result:
        return result

    # Fallback اگر درخواست fail کرد
    logger.warning("ترجمه با GapGPT fail شد - fallback به متن تمیز انگلیسی")
    clean = clean_html(raw_text)
    if len(clean) > MAX_TEXT_LENGTH:
        clean = clean[:MAX_TEXT_LENGTH] + "..."
    return f"**{title}**\n\n{clean}"


# ====================== تابع اصلی ======================
async def main(event=None, context=None):
    """تابع اصلی بات - نقطه ورود اجرا"""
    logger.info("شروع اجرای بات فشن")

    if not Config.validate():
        logger.critical("تنظیمات ناقص - اجرا متوقف شد")
        return {"status": "error", "reason": "missing_env_vars"}

    bot = Bot(token=Config.TELEGRAM_BOT_TOKEN)

    gapgpt_client = GapGPTClient()

    aw_client = Client()
    aw_client.set_endpoint(Config.APPWRITE_ENDPOINT)
    aw_client.set_project(Config.APPWRITE_PROJECT_ID)
    aw_client.set_key(Config.APPWRITE_API_KEY)
    databases = Databases(aw_client)

    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(days=CHECK_DAYS)

    posted_count = 0

    for feed_url in RSS_FEEDS:
        if posted_count >= MAX_POSTS_PER_RUN:
            logger.info(f"حداکثر پست رسیده ({posted_count}/{MAX_POSTS_PER_RUN})")
            break

        logger.debug(f"پردازش فید: {feed_url}")

        try:
            feed = feedparser.parse(feed_url, sanitize_html=True)
            if not feed.entries:
                logger.info(f"فید خالی: {feed_url}")
                continue

            for entry in feed.entries:
                if posted_count >= MAX_POSTS_PER_RUN:
                    break

                published = entry.get('published_parsed') or entry.get('updated_parsed')
                if not published:
                    continue

                pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_date < time_threshold:
                    continue

                title = (entry.title or '').strip()
                link = (entry.link or '').strip()
                if not title or not link:
                    continue

                raw_content = (entry.get('summary') or entry.get('description') or '')[:1200]
                clean_text = clean_html(raw_content)

                # چک تکراری در دیتابیس
                try:
                    existing = databases.list_documents(
                        database_id=Config.APPWRITE_DATABASE_ID,
                        collection_id=Config.COLLECTION_ID,
                        queries=[Query.equal("link", link)]
                    )
                    if existing['total'] > 0:
                        logger.info(f"خبر تکراری رد شد: {title[:60]}")
                        continue
                except AppwriteException as e:
                    logger.warning(f"خطا در چک دیتابیس: {str(e)} - ادامه بدون چک")

                # ترجمه و بازنویسی با GapGPT
                final_text = await translate_and_format(gapgpt_client, title, clean_text)

                # استخراج عکس
                image_url = get_image_from_rss(entry)
                if not image_url:
                    image_url = await extract_og_image(link)

                # ارسال به کانال
                try:
                    if image_url:
                        await bot.send_photo(
                            chat_id=Config.TELEGRAM_CHANNEL_ID,
                            photo=image_url,
                            caption=final_text,
                            parse_mode='HTML',
                            disable_notification=True
                        )
                    else:
                        await bot.send_message(
                            chat_id=Config.TELEGRAM_CHANNEL_ID,
                            text=final_text,
                            parse_mode='HTML',
                            disable_notification=True,
                            disable_web_page_preview=True
                        )

                    posted_count += 1
                    logger.info(f"پست موفق ارسال شد ({posted_count}/{MAX_POSTS_PER_RUN}): {title[:60]}")

                    # ذخیره در دیتابیس
                    try:
                        databases.create_document(
                            database_id=Config.APPWRITE_DATABASE_ID,
                            collection_id=Config.COLLECTION_ID,
                            document_id='unique()',
                            data={
                                'link': link,
                                'title': title[:250],
                                'published_at': now.isoformat(),
                                'feed_url': feed_url,
                                'created_at': now.isoformat()
                            }
                        )
                        logger.debug(f"ذخیره در دیتابیس موفق: {link}")
                    except AppwriteException as save_err:
                        logger.warning(f"خطا در ذخیره دیتابیس: {str(save_err)}")

                    # فاصله بین پست‌ها برای جلوگیری از اسپم
                    if posted_count < MAX_POSTS_PER_RUN:
                        await asyncio.sleep(Config.SLEEP_BETWEEN_POSTS)

                except TelegramError as te:
                    logger.error(f"خطا تلگرام هنگام ارسال: {str(te)}")
                except Exception as send_err:
                    logger.error(f"خطای عمومی ارسال: {str(send_err)}")

        except Exception as feed_err:
            logger.error(f"خطا در پردازش فید {feed_url}: {str(feed_err)}")

    logger.info(f"پایان اجرا - تعداد پست ارسال‌شده: {posted_count}")
    return {"status": "completed", "posted": posted_count}


if __name__ == "__main__":
    asyncio.run(main())
