import os
import asyncio
import feedparser
import requests
from datetime import datetime, timedelta, timezone
from telegram import Bot
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query

async def main(event=None, context=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    appwrite_endpoint = os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1')
    appwrite_project = os.environ.get('APPWRITE_PROJECT_ID')
    appwrite_key = os.environ.get('APPWRITE_API_KEY')
    database_id = os.environ.get('APPWRITE_DATABASE_ID')
    collection_id = 'history'

    if not all([token, chat_id, appwrite_project, appwrite_key, database_id]):
        print("متغیرهای محیطی ناقص!")
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
        "https://feeds.feedburner.com/fibre2fashion/fashion-news",
        "https://medopia.ir/feed/",
        "https://www.digikala.com/mag/feed/?category=مد",
        "https://www.khabaronline.ir/rss/category/مد-زیبایی",
        "https://www.isna.ir/rss/category/فرهنگ-هنر",
        "https://www.tasnimnews.com/fa/rss/feed/0/0/0/سبک-زندگی",
        "https://www.hamshahrionline.ir/rss/category/مد",
        "https://fararu.com/rss/category/مد-زیبایی",
        "https://www.beytoote.com/rss/fashion",
        "https://www.zoomit.ir/feed/category/fashion-beauty/",
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
                link = entry.link.strip()

                try:
                    existing = databases.list_documents(
                        database_id=database_id,
                        collection_id=collection_id,
                        queries=[Query.equal("link", link)]
                    )
                    if existing['total'] > 0:
                        print(f"تکراری رد شد: {title[:60]}")
                        continue
                except AppwriteException as e:
                    print(f"خطا چک DB: {str(e)}")

                summary = (entry.get('summary') or entry.get('description') or '').strip()[:400]

                if is_persian:
                    content = f"{title}\n\n{summary}"
                    image_url = get_image_from_rss(entry)
                else:
                    content, image_url = await process_with_puter_gemini(title, summary)

                final_text = f"{content}\n\n#مد #استایل #ترند #فشن_ایرانی #مهرجامه"

                try:
                    if image_url:
                        await bot.send_photo(chat_id=chat_id, photo=image_url, caption=final_text, parse_mode='HTML', disable_notification=True)
                    else:
                        await bot.send_message(chat_id=chat_id, text=final_text, disable_web_page_preview=True, disable_notification=True)

                    posted_count += 1
                    print(f"پست موفق: {title[:60]}")

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

    print(f"این اجرا: {posted_count} پست")
    return {"status": "success", "posted": posted_count}


async def process_with_puter_gemini(title_en, summary_en):
    prompt = f"""این خبر مد انگلیسی را به فارسی طبیعی، روان و جذاب برای خانم‌های ایرانی بازنویسی کن.
ابتدا یک تیتر کوتاه و گیرا بنویس.
بعد متن اصلی را در ۱ تا ۲ پاراگراف کوتاه بنویس:
- با تنش واقعی زندگی شروع کن (سردرگمی خرید، تکراری شدن کمد لباس، فشار انتخاب استایل مناسب و ...).
- ترند جدید را به عنوان راه‌حل یا ایده جالب معرفی کن.
- لحن دوستانه، گفتگویی و نزدیک به زبان روزمره باشه.
- بدون تبلیغ مستقیم، بدون قیمت، بدون لینک، بدون برچسب اضافی مثل "پاراگراف اول" یا "متن خبر".

خروجی دقیقاً این شکل باشه (فقط متن خام):
تیتر جذاب
متن کامل (۱ یا ۲ پاراگراف)

در انتها یک پرامپت دقیق و حرفه‌ای برای ساخت عکس مرتبط بنویس (برای txt2img): پرامپت تصویر:

عنوان انگلیسی: {title_en}
خلاصه انگلیسی: {summary_en}"""

    try:
        # درخواست chat به Puter (Gemini از طریق Puter)
        response = requests.post(
            "https://api.puter.com/v2/ai/chat",
            json={
                "prompt": prompt,
                "model": "gemini-2.5-flash-preview"  # سریع و خوب برای متن فارسی
            },
            headers={"Content-Type": "application/json"}
        ).json()

        full_text = response.get('response', '').strip()
        if not full_text:
            raise ValueError("پاسخ خالی")

        # جدا کردن پرامپت تصویر
        if "پرامپت تصویر:" in full_text:
            parts = full_text.split("پرامپت تصویر:")
            content = parts[0].strip()
            image_prompt = parts[1].strip()
        else:
            content = full_text
            image_prompt = f"تصویر استایل مد ایرانی شیک و جذاب برای خانم‌ها بر اساس ترند: {title_en}، با رنگ‌های هماهنگ، لباس روزمره، فضای مدرن ایرانی"

        print(f"Puter متن موفق: {content[:80]}...")

        # ساخت تصویر با Nano Banana (Gemini Image از طریق Puter)
        image_response = requests.post(
            "https://api.puter.com/v2/ai/txt2img",
            json={
                "prompt": image_prompt,
                "model": "gemini-2.5-flash-image-preview"  # سریع و باکیفیت
            },
            headers={"Content-Type": "application/json"}
        ).json()

        image_url = image_response.get('image_url')

        return content, image_url
    except Exception as e:
        print(f"Puter خطا: {str(e)}")
        return f"📰 {title_en}\n{summary_en[:200]}...", None


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
