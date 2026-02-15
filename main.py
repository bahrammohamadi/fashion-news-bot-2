import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot
import google.generativeai as genai

# Appwrite imports
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException

async def main(event=None, context=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    gemini_key = os.environ.get('GEMINI_API_KEY')
    appwrite_endpoint = os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1')
    appwrite_project = os.environ.get('APPWRITE_PROJECT_ID')
    appwrite_key = os.environ.get('APPWRITE_API_KEY')
    database_id = os.environ.get('APPWRITE_DATABASE_ID')  # ID دیتابیس fashion_db
    collection_id = 'history'  # نام کالکشن

    if not all([token, chat_id, gemini_key, appwrite_project, appwrite_key, database_id]):
        print("متغیرهای محیطی ناقص!")
        return {"status": "error"}

    bot = Bot(token=token)
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel('gemini-2.5-flash')

    # اتصال به Appwrite
    client = Client()
    client.set_endpoint(appwrite_endpoint)
    client.set_project(appwrite_project)
    client.set_key(appwrite_key)
    databases = Databases(client)

    rss_feeds = [
        # خارجی - ترجمه می‌شوند
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
        "https://www.harpersbazaar.com/rss/fashion.xml",
        "https://fashionista.com/feed",
        "https://www.businessoffashion.com/feed/",
        "https://www.elle.com/rss/fashion.xml",
        "https://www.refinery29.com/rss.xml",
        "https://www.thecut.com/feed",
        "https://www.whowhatwear.com/rss",
        "https://feeds.feedburner.com/fibre2fashion/fashion-news",
        "https://www.instyle.com/rss",  # اضافه جدید

        # فارسی/ایرانی - مستقیم پست
        "https://medopia.ir/feed/",
        "https://www.digikala.com/mag/feed/?category=مد",
        "https://www.khabaronline.ir/rss/category/مد-زیبایی",
        "https://www.isna.ir/rss/category/فرهنگ-هنر",
        "https://www.tasnimnews.com/fa/rss/feed/0/0/0/سبک-زندگی",
        "https://www.hamshahrionline.ir/rss/category/مد",
        "https://fararu.com/rss/category/مد-زیبایی",
        "https://www.beytoote.com/rss/fashion",
        "https://www.zoomit.ir/feed/category/fashion-beauty/",
        # اگر فیدهای بیشتری پیدا کردی اضافه کن
    ]

    posted_count = 0
    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=24)

    for feed_url in rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue

            is_persian = any(x in feed_url.lower() for x in ['.ir', 'khabaronline', 'isna', 'tasnim', 'hamshahrionline', 'fararu', 'beytoote', 'digikala', 'zoomit', 'medopia'])

            for entry in feed.entries[:4]:
                published = entry.get('published_parsed') or entry.get('updated_parsed')
                if not published:
                    continue
                pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_date < time_threshold:
                    continue

                title = entry.title.strip()
                link = entry.link.strip()  # کلیدی برای چک تکراری

                # چک تکراری در DB
                try:
                    existing = databases.list_documents(
                        database_id=database_id,
                        collection_id=collection_id,
                        queries=[f'equal("link", ["{link}"])']
                    )
                    if existing['total'] > 0:
                        print(f"تکراری: {title[:60]}... رد شد")
                        continue
                except AppwriteException as db_err:
                    print(f"خطا چک DB: {str(db_err)} - پست ارسال می‌شود (fallback)")
                    # اگر DB مشکل داشت، پست می‌فرستیم ولی ذخیره نمی‌کنیم

                summary = (entry.get('summary') or entry.get('description') or '').strip()[:400]

                if is_persian:
                    content = f"{title}\n\n{summary}"
                else:
                    content = await rewrite_with_gemini(model, title, summary)

                final_text = f"{content}\n\n#مد #استایل #ترند #فشن_ایرانی #مهرجامه"

                photo_url = None
                if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
                    photo_url = entry.enclosure.href
                elif 'media_content' in entry:
                    for media in entry.media_content:
                        if media.get('medium') == 'image' and media.get('url'):
                            photo_url = media.get('url')
                            break

                try:
                    if photo_url:
                        await bot.send_photo(chat_id=chat_id, photo=photo_url, caption=final_text, parse_mode='HTML', disable_notification=True)
                    else:
                        await bot.send_message(chat_id=chat_id, text=final_text, disable_web_page_preview=True, disable_notification=True)

                    posted_count += 1
                    print(f"پست موفق: {title[:60]}...")

                    # ذخیره در DB
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
                        print("ذخیره در DB موفق")
                    except AppwriteException as save_err:
                        print(f"خطا ذخیره DB: {str(save_err)}")

                except Exception as send_err:
                    print(f"خطا ارسال: {str(send_err)}")

        except Exception as feed_err:
            print(f"خطا فید {feed_url}: {str(feed_err)}")

    print(f"اجرای این دور: {posted_count} پست")
    return {"status": "success", "posted": posted_count}


async def rewrite_with_gemini(model, title_en, summary_en):
    prompt = f"""این خبر مد را به فارسی طبیعی و جذاب برای خانم‌های ایرانی بازنویسی کن.
ابتدا یک تیتر کوتاه و گیرا بنویس.
بعد متن را در ۱ تا ۲ پاراگراف (حداکثر ۸-۱۰ جمله) بنویس:
- با تنش واقعی شروع کن (سردرگمی خرید، تکراری شدن استایل و ...).
- ترند را به عنوان راه‌حل معرفی کن.
- لحن دوستانه و روزمره.

خروجی:
تیتر جذاب
پاراگراف اول
پاراگراف دوم (اختیاری)

عنوان انگلیسی: {title_en}
خلاصه انگلیسی: {summary_en}"""

    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        text = response.text.strip()
        if not text:
            raise ValueError("پاسخ خالی")
        print(f"Gemini موفق: {text[:80]}...")
        return text
    except Exception as e:
        print(f"Gemini خطا: {str(e)}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
