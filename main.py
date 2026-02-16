import os
import asyncio
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from telegram import Bot
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query

async def main(event=None, context=None):
    print("[INFO] اجرای تابع main شروع شد")

    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    appwrite_endpoint = os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1')
    appwrite_project = os.environ.get('APPWRITE_PROJECT_ID')
    appwrite_key = os.environ.get('APPWRITE_API_KEY')
    database_id = os.environ.get('APPWRITE_DATABASE_ID')
    collection_id = 'history'

    if not all([token, chat_id, appwrite_project, appwrite_key, database_id]):
        print("[ERROR] متغیرهای محیطی ناقص!")
        return {"status": "error"}

    bot = Bot(token=token)

    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)

    rss_feeds = [
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
        "https://www.papermag.com/rss",
        "https://www.highsnobiety.com/feed/",
        "https://hypebeast.com/feed",
        "https://www.ssense.com/en-us/editorial/rss",
        "https://www.dazeddigital.com/rss",
        "https://i-d.vice.com/en/rss",
    ]

    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(days=1)   # ۲۴ ساعت اخیر

    posted_count = 0
    max_posts_per_run = 4   # حداکثر ۴ پست در هر اجرا

    for feed_url in rss_feeds:
        if posted_count >= max_posts_per_run:
            break

        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue

            for entry in feed.entries:
                if posted_count >= max_posts_per_run:
                    break

                published = entry.get('published_parsed') or entry.get('updated_parsed')
                if not published:
                    continue
                pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_date < time_threshold:
                    continue

                title = entry.title.strip()
                link = entry.link.strip()
                raw_html = entry.get('summary') or entry.get('description') or ''
                soup = BeautifulSoup(raw_html, 'html.parser')
                content_raw = soup.get_text(separator=' ').strip()

                # مرحله ۱: ترجمه دقیق با پرامپت ثابت
                translated = translate_to_persian(title, content_raw)

                # مرحله ۲: تبدیل به مقاله فشن حرفه‌ای با پرامپت دوم
                final_content = convert_to_fashion_article(translated, title, link, pub_date)

                final_text = f"{final_content}\n\n🔗 {link}"

                try:
                    image_url = get_image_from_rss(entry)
                    if image_url:
                        await bot.send_photo(chat_id=chat_id, photo=image_url, caption=final_text, parse_mode='HTML', disable_notification=True)
                    else:
                        await bot.send_message(chat_id=chat_id, text=final_text, disable_web_page_preview=True, disable_notification=True)

                    posted_count += 1
                    print(f"[SUCCESS] پست موفق: {title[:60]}")

                    try:
                        databases.create_document(
                            database_id=database_id,
                            collection_id=collection_id,
                            document_id='unique()',
                            data={
                                'link': link,
                                'title': title,
                                'published_at': now.isoformat(),
                                'feed_url': feed_url
                            }
                        )
                    except Exception as save_err:
                        print(f"[WARN] خطا ذخیره DB: {str(save_err)}")

                except Exception as send_err:
                    print(f"[ERROR] خطا ارسال: {str(send_err)}")

        except Exception as feed_err:
            print(f"[ERROR] خطا فید {feed_url}: {str(feed_err)}")

    print(f"[INFO] پایان اجرا - تعداد پست ارسال‌شده: {posted_count}")
    return {"status": "success", "posted": posted_count}


def translate_to_persian(title, content_raw):
    """پرامپت ثابت ترجمه دقیق"""
    # اینجا فقط تمیز کردن و شبیه‌سازی ترجمه (چون بدون LLM واقعی هستیم)
    return f"{title}\n\n{content_raw[:450]}..."


def convert_to_fashion_article(translated, title, link, pub_date):
    """پرامپت دوم - تبدیل به مقاله فشن حرفه‌ای"""
    return f"""**{title}**

{translated}

این ترند یا خبر جدید می‌تواند ایده‌های ارزشمندی برای استایل روزمره یا انتخاب‌های هوشمندانه در فصل جاری به همراه داشته باشد.

#مد #استایل #ترند #فشن_ایرانی #مهرجامه"""


def get_image_from_rss(entry):
    if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
        return entry.enclosure.href
    if 'media_content' in entry:
        for media in entry.media_content:
            if media.get('medium') == 'image' and media.get('url'):
                return media.get('url')
    return None


if __name__ == "__main__":
    asyncio.run(main())
