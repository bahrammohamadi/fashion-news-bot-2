import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query
from openai import AsyncOpenAI

async def main(event=None, context=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    appwrite_endpoint = os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1')
    appwrite_project = os.environ.get('APPWRITE_PROJECT_ID')
    appwrite_key = os.environ.get('APPWRITE_API_KEY')
    database_id = os.environ.get('APPWRITE_DATABASE_ID')
    collection_id = 'history'

    if not all([token, chat_id, appwrite_project, appwrite_key, database_id]):
        print("متغیرهای محیطی ناقص! چک کن APPWRITE_PROJECT_ID درست باشه.")
        return {"status": "error"}

    bot = Bot(token=token)

    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)

    openrouter_client = AsyncOpenAI(
        api_key=os.environ.get('OPENROUTER_API_KEY'),
        base_url="https://openrouter.ai/api/v1"
    )

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

                # چک تکراری (اگر DB مشکل داشت، این بخش رد می‌شه)
                try:
                    existing = databases.list_documents(
                        database_id=database_id,
                        collection_id=collection_id,
                        queries=[Query.equal("link", link)]
                    )
                    if existing['total'] > 0:
                        print(f"تکراری رد شد: {title[:60]}")
                        continue
                except Exception as db_err:
                    print(f"خطا چک DB: {str(db_err)} - ادامه می‌دیم بدون چک تکراری")

                summary = (entry.get('summary') or entry.get('description') or '').strip()[:500]

                image_url = get_image_from_rss(entry)

                if is_persian:
                    content = f"{title}\n\n{summary}"
                else:
                    content = await translate_with_openrouter(openrouter_client, title, summary)

                final_text = f"{content}\n\n🔗 {link}\n#مد #استایل #ترند #فشن_ایرانی #مهرجامه"

                try:
                    if image_url:
                        await bot.send_photo(chat_id=chat_id, photo=image_url, caption=final_text, parse_mode='HTML', disable_notification=True)
                    else:
                        await bot.send_message(chat_id=chat_id, text=final_text, disable_web_page_preview=True, disable_notification=True)

                    posted_count += 1
                    print(f"پست موفق: {title[:60]}")

                    # ذخیره در DB (اگر خطا داد، رد می‌شه)
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
                        print(f"خطا ذخیره DB: {str(save_err)} - پست ارسال شد اما ذخیره نشد")
                except Exception as send_err:
                    print(f"خطا ارسال: {str(send_err)}")

        except Exception as feed_err:
            print(f"خطا فید {feed_url}: {str(feed_err)}")

    print(f"اجرای این دور: {posted_count} پست")
    return {"status": "success", "posted": posted_count}


async def translate_with_openrouter(client, title_en, summary_en):
    try:
        prompt = f"""این خبر مد انگلیسی را به فارسی طبیعی، روان و جذاب برای خانم‌های ایرانی بازنویسی کن.
ابتدا یک تیتر کوتاه و گیرا بنویس.
بعد متن اصلی را در ۱ تا ۲ پاراگراف کوتاه بنویس:
- با تنش واقعی زندگی شروع کن (سردرگمی خرید، تکراری شدن کمد لباس، فشار انتخاب استایل مناسب و ...).
- ترند جدید را به عنوان راه‌حل یا ایده جالب معرفی کن.
- لحن دوستانه، گفتگویی و نزدیک به زبان روزمره باشه.
- بدون تبلیغ مستقیم، بدون قیمت، بدون لینک.
خروجی دقیقاً این شکل باشه (فقط متن خام):
تیتر جذاب
متن کامل (۱ یا ۲ پاراگراف)

عنوان انگلیسی: {title_en}
خلاصه انگلیسی: {summary_en}"""

        response = await client.chat.completions.create(
            model="meta-llama/llama-3.1-70b-instruct:free",  # مدل رایگان قوی برای فارسی
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=500
        )

        full_text = response.choices[0].message.content.strip()

        lines = full_text.split('\n', 1)
        title_fa = lines[0].strip() if lines else title_en
        content_fa = lines[1].strip() if len(lines) > 1 else full_text

        return f"{title_fa}\n\n{content_fa}"

    except Exception as e:
        print(f"خطا در OpenRouter: {str(e)}")
        return f"📰 {title_en}\n\n{summary_en[:400]}... (ترجمه موقت - خطا رخ داد)"


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
