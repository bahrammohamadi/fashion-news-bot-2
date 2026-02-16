import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot
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

    # ۲۰ فید ایرانی فعال و بروز (بهترین‌ها)
    rss_feeds = [
        "https://medopia.ir/feed/",
        "https://www.digikala.com/mag/feed/?category=مد-و-زیبایی",
        "https://www.digistyle.com/mag/feed/",
        "https://www.khabaronline.ir/rss/category/مد-زیبایی",
        "https://fararu.com/rss/category/مد-زیبایی",
        "https://www.beytoote.com/rss/fashion",
        "https://www.zoomit.ir/feed/category/fashion-beauty/",
        "https://www.elsana.com/feed/",
        "https://www.namnak.com/rss/fashion",
        "https://www.tarahanelebas.com/feed/",
        "https://www.chibepoosham.com/feed/",
        "https://www.persianpood.com/feed/",
        "https://www.jument.style/feed/",
        "https://www.zibamoon.com/feed/",
        "https://www.sarak-co.com/feed/",
        "https://www.pattonjameh.com/feed/",
        "https://www.tonikaco.com/feed/",
        "https://www.rnsfashion.com/feed/",
        "https://www.modetstyle.com/feed/",
        "https://www.antikstyle.com/feed/",
    ]

    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(days=4)   # ۴ روز اخیر

    posted_count = 0
    max_posts_per_run = 8   # حداکثر ۸ پست در هر اجرا (برای جلوگیری از اسپم)

    for feed_url in rss_feeds:
        if posted_count >= max_posts_per_run:
            break

        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                print(f"[INFO] فید خالی: {feed_url}")
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
                description = (entry.get('summary') or entry.get('description') or '').strip()

                # چک تکراری
                try:
                    existing = databases.list_documents(
                        database_id=database_id,
                        collection_id=collection_id,
                        queries=[Query.equal("link", link)]
                    )
                    if existing['total'] > 0:
                        print(f"[INFO] تکراری رد شد: {title[:60]}")
                        continue
                except Exception as db_err:
                    print(f"[WARN] خطا DB: {str(db_err)}")

                # تبدیل به پست حرفه‌ای فشن (بدون AI خارجی)
                content = create_fashion_post(title, description)

                final_text = f"{content}\n\n🔗 {link}"

                try:
                    image_url = get_image_from_rss(entry)
                    if image_url:
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=image_url,
                            caption=final_text,
                            parse_mode='HTML',
                            disable_notification=True
                        )
                    else:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=final_text,
                            disable_web_page_preview=True,
                            disable_notification=True
                        )

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


def create_fashion_post(title, description):
    """تبدیل متن خام به پست حرفه‌ای فشن"""
    # تمیز کردن و ساختاردهی ساده
    clean_desc = description.replace('\n', ' ').strip()
    if len(clean_desc) > 300:
        clean_desc = clean_desc[:300] + "..."

    post = f"""**{title}**

{clean_desc}

این ترند جدید می‌تونه استایل روزمره یا مناسبت‌های خاص شما رو خیلی شیک‌تر کنه. ترکیبش با لباس‌های ایرانی و اکسسوری‌های ساده، نتیجه فوق‌العاده‌ای می‌ده.

#مد #استایل #ترند #فشن_ایرانی #مهرجامه"""

    return post


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
