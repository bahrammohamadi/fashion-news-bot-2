# main_fashion_openrouter_final.py - نهایی، بدون ترجمه مستقیم، فقط استنباط + نکته استایل، ۱ پست، با عکس‌ها

import os
import asyncio
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from telegram import Bot
from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query

# ====================== تنظیمات ======================
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHANNEL_ID = os.environ.get('TELEGRAM_CHANNEL_ID')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
APPWRITE_ENDPOINT = os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1')
APPWRITE_PROJECT_ID = os.environ.get('APPWRITE_PROJECT_ID')
APPWRITE_API_KEY = os.environ.get('APPWRITE_API_KEY')
APPWRITE_DATABASE_ID = os.environ.get('APPWRITE_DATABASE_ID')
COLLECTION_ID = 'history'

MAX_POSTS_PER_RUN = 1
CHECK_DAYS = 4
MAX_RAW_TEXT_LENGTH = 1200
MAX_FINAL_TEXT_LENGTH = 420

# ====================== فیدهای خارجی مد و فشن ======================
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

# ====================== تولید استنباط + نکته استایل ======================
async def extract_insight_and_style(client, title, raw_text):
    prompt = f"""
از این خبر مد و فشن فقط برآیند و استنباط اصلی رو استخراج کن:

عنوان: {title}
متن: {raw_text[:1000]}

خروجی دقیقاً این فرمت باشه:

**تیتر جذاب فارسی**

متن توضیح کوتاه و حرفه‌ای (۳ تا ۶ خط) - فقط جوهره خبر، بدون تبلیغ

نکته استایل: [یک نکته کاربردی و مرتبط ۱–۲ جمله‌ای - با نگاه به مانتو، شال، حجاب، فرهنگ و آب‌وهوای ایران]

بدون هیچ متن اضافه، تبلیغ یا لینک.
"""

    try:
        resp = await client.chat.completions.create(
            model="qwen/qwen2.5-coder-32b-instruct",  # مدل قوی فارسی، بدون لیمیت سخت
            messages=[{"role": "user", "content": prompt}],
            temperature=0.75,
            max_tokens=500
        )
        content = resp.choices[0].message.content.strip()
        if not content or len(content) < 80:
            raise Exception("پاسخ نامعتبر")
        return content
    except Exception as e:
        print(f"[OPENROUTER ERROR] {str(e)[:100]} - fallback")
        clean_fallback = clean_html(raw_text)
        if len(clean_fallback) > MAX_FINAL_TEXT_LENGTH:
            clean_fallback = clean_fallback[:MAX_FINAL_TEXT_LENGTH] + "..."
        return f"**{title}**\n\n{clean_fallback}\n\nنکته استایل: این خبر می‌تونه ایده‌های خوبی برای ترکیب با استایل ایرانی بده."

# ====================== توابع کمکی ======================
def clean_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text(separator=' ', strip=True)

def get_all_images_from_rss(entry):
    images = []
    if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
        images.append(entry.enclosure.get('href'))
    if 'media_content' in entry:
        for media in entry.media_content:
            if media.get('medium') == 'image' and media.get('url'):
                images.append(media.get('url'))
    return images if images else None

async def extract_og_image(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=8, headers=headers)
        if response.status_code != 200:
            return None
        soup = BeautifulSoup(response.text, 'html.parser')
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            return og['content']
        return None
    except:
        return None

# ====================== تابع اصلی ======================
async def main(event=None, context=None):
    print("[INFO] شروع اجرا")

    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, OPENROUTER_API_KEY, APPWRITE_PROJECT_ID, APPWRITE_API_KEY, APPWRITE_DATABASE_ID]):
        print("[ERROR] متغیرهای محیطی ناقص")
        return {"status": "error"}

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    client = AsyncOpenAI(
        api_key=OPENROUTER_API_KEY,
        base_url="https://openrouter.ai/api/v1"
    )

    aw_client = Client()
    aw_client.set_endpoint(APPWRITE_ENDPOINT)
    aw_client.set_project(APPWRITE_PROJECT_ID)
    aw_client.set_key(APPWRITE_API_KEY)
    databases = Databases(aw_client)

    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(days=CHECK_DAYS)

    posted_count = 0

    for feed_url in RSS_FEEDS:
        if posted_count >= MAX_POSTS_PER_RUN:
            break

        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
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

                title = entry.title.strip()
                link = entry.link.strip()
                raw_content = (entry.get('summary') or entry.get('description') or '')[:MAX_RAW_TEXT_LENGTH]

                soup = BeautifulSoup(raw_content, 'html.parser')
                clean_text = soup.get_text(separator=' ').strip()

                # چک تکراری
                try:
                    existing = databases.list_documents(
                        database_id=APPWRITE_DATABASE_ID,
                        collection_id=COLLECTION_ID,
                        queries=[Query.equal("link", link)]
                    )
                    if existing['total'] > 0:
                        continue
                except Exception as e:
                    print(f"[DB WARN] {e}")

                # استنباط + نکته استایل
                final_text = await extract_insight_and_style(client, title, clean_text)

                # استخراج عکس‌ها
                image_urls = get_all_images_from_rss(entry)
                if not image_urls:
                    og_image = await extract_og_image(link)
                    if og_image:
                        image_urls = [og_image]

                try:
                    if image_urls:
                        await bot.send_photo(
                            chat_id=TELEGRAM_CHANNEL_ID,
                            photo=image_urls[0],
                            caption=final_text,
                            parse_mode='HTML',
                            disable_notification=True
                        )
                        for extra_img in image_urls[1:]:
                            await bot.send_photo(
                                chat_id=TELEGRAM_CHANNEL_ID,
                                photo=extra_img,
                                caption="",
                                disable_notification=True
                            )
                    else:
                        await bot.send_message(
                            chat_id=TELEGRAM_CHANNEL_ID,
                            text=final_text,
                            parse_mode='HTML',
                            disable_notification=True
                        )

                    posted_count += 1
                    print(f"[SUCCESS] پست ارسال شد: {title[:60]} - عکس‌ها: {len(image_urls) if image_urls else 0}")

                    try:
                        databases.create_document(
                            database_id=APPWRITE_DATABASE_ID,
                            collection_id=COLLECTION_ID,
                            document_id='unique()',
                            data={
                                'link': link,
                                'title': title[:250],
                                'published_at': now.isoformat(),
                                'feed_url': feed_url
                            }
                        )
                    except Exception as save_err:
                        print(f"[DB SAVE WARN] {save_err}")

                except Exception as send_err:
                    print(f"[SEND ERROR] {send_err}")

        except Exception as feed_err:
            print(f"[FEED ERROR] {feed_url}: {feed_err}")

    print(f"[INFO] پایان اجرا - پست شده: {posted_count}")
    return {"status": "ok", "posted": posted_count}


if __name__ == "__main__":
    asyncio.run(main())