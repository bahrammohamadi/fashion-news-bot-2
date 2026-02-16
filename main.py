# main_fashion_gapgpt_final.py - با GapGPT + کلید جدید + فقط خارجی + ۱-۲ پست + با عکس

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
GAPGPT_API_KEY = os.environ.get('GAPGPT_API_KEY')  # کلید جدیدت اینجا
APPWRITE_ENDPOINT = os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1')
APPWRITE_PROJECT_ID = os.environ.get('APPWRITE_PROJECT_ID')
APPWRITE_API_KEY = os.environ.get('APPWRITE_API_KEY')
APPWRITE_DATABASE_ID = os.environ.get('APPWRITE_DATABASE_ID')
COLLECTION_ID = 'history'

MAX_POSTS_PER_RUN = 2
CHECK_DAYS = 4
MAX_TEXT_LENGTH = 420

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

# ====================== ترجمه و فرمت با GapGPT ======================
async def translate_and_format(client, title, raw_text):
    # پرامپت ترکیبی (ترجمه + بازنویسی فشن همزمان)
    prompt = f"""
عنوان خبر: {title}
متن انگلیسی: {raw_text[:1200]}

به فارسی روان، حرفه‌ای و جذاب برای کانال مد ترجمه و بازنویسی کن.
- تیتر را جذاب و کوتاه نگه دار
- متن را ۳-۶ خطی، شیک و خلاصه بنویس
- فقط محتوای اصلی خبر را نگه دار
- بدون جمله اضافه، تبلیغ، ایموجی یا لینک

خروجی فقط متن پست نهایی باشه (تیتر + متن).
"""

    try:
        resp = await client.chat.completions.create(
            model="gpt-4o-mini",  # مدل قوی و ارزان در GapGPT
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=600
        )
        final_text = resp.choices[0].message.content.strip()
        if not final_text or len(final_text) < 50:
            raise Exception("پاسخ نامعتبر")
        return final_text
    except Exception as e:
        print(f"[GAPGPT ERROR] {str(e)[:100]} - fallback")
        return f"**{title}**\n\n{clean_html(raw_text)[:380]}...\n(ترجمه موقت - خطا)"

# ====================== توابع کمکی ======================
def clean_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    return soup.get_text(separator=' ').strip()

def get_image_from_rss(entry):
    if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
        return entry.enclosure.href
    if 'media_content' in entry:
        for media in entry.media_content:
            if media.get('medium') == 'image' and media.get('url'):
                return media.get('url')
    return None

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

    if not all([TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, GAPGPT_API_KEY, APPWRITE_PROJECT_ID, APPWRITE_API_KEY, APPWRITE_DATABASE_ID]):
        print("[ERROR] متغیرهای محیطی ناقص")
        return {"status": "error"}

    bot = Bot(token=TELEGRAM_BOT_TOKEN)

    gapgpt_client = AsyncOpenAI(
        api_key=GAPGPT_API_KEY,
        base_url="https://api.gapgpt.app/v1"  # endpoint اصلی GapGPT
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
                raw_content = (entry.get('summary') or entry.get('description') or '')[:1200]

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

                # ترجمه و فرمت با GapGPT
                final_text = await translate_and_format(gapgpt_client, title, clean_text)

                image_url = get_image_from_rss(entry)
                if not image_url:
                    image_url = await extract_og_image(link)

                try:
                    if image_url:
                        await bot.send_photo(
                            chat_id=TELEGRAM_CHANNEL_ID,
                            photo=image_url,
                            caption=final_text,
                            parse_mode='HTML',
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
                    print(f"[SUCCESS] پست شد: {title[:60]}")

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
