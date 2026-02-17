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

async def main(event=None, context=None):
    print("[INFO] اجرای تابع main شروع شد")

    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    gapgpt_key = os.environ.get('GAPGPT_API_KEY')
    appwrite_endpoint = os.environ.get('APPWRITE_ENDPOINT', 'https://cloud.appwrite.io/v1')
    appwrite_project = os.environ.get('APPWRITE_PROJECT_ID')
    appwrite_key = os.environ.get('APPWRITE_API_KEY')
    database_id = os.environ.get('APPWRITE_DATABASE_ID')
    collection_id = 'history'

    if not all([token, chat_id, gapgpt_key, appwrite_project, appwrite_key, database_id]):
        print("[ERROR] متغیرهای محیطی ناقص!")
        return {"status": "error"}

    bot = Bot(token=token)

    client = AsyncOpenAI(
        api_key=gapgpt_key,
        base_url="https://api.gapgpt.app/v1"
    )

    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)

    rss_feeds = [
        "https://medopia.ir/feed/",
        "https://www.digistyle.com/mag/feed/",
        "https://www.chibepoosham.com/feed/",
        "https://www.tarahanelebas.com/feed/",
        "https://www.persianpood.com/feed/",
        "https://www.jument.style/feed/",
        "https://www.zibamoon.com/feed/",
        "https://www.sarak-co.com/feed/",
        "https://www.elsana.com/feed/",
        "https://www.beytoote.com/rss/fashion",
        "https://www.namnak.com/rss/fashion",
        "https://www.modetstyle.com/feed/",
        "https://www.antikstyle.com/feed/",
        "https://www.rnsfashion.com/feed/",
        "https://www.pattonjameh.com/feed/",
        "https://www.tonikaco.com/feed/",
        "https://www.zoomit.ir/feed/category/fashion-beauty/",
        "https://www.khabaronline.ir/rss/category/مد-زیبایی",
        "https://fararu.com/rss/category/مد-زیبایی",
        "https://www.digikala.com/mag/feed/?category=مد-و-زیبایی",
    ]

    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(days=4)

    posted_count = 0
    max_posts_per_run = 1  # فقط ۱ پست در هر اجرا

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

                # فیلتر هوشمند با GapGPT
                is_fashion = await is_fashion_related(client, title, content_raw)
                if not is_fashion:
                    print(f"[FILTER] رد شد (غیرمرتبط): {title[:60]}")
                    continue

                # ترجمه و تبدیل به مقاله فشن
                final_content = await process_fashion_article(client, title, content_raw, link, pub_date)

                final_text = f"{final_content}\n\n🔗 {link}"

                image_url = get_image_from_rss(entry)
                if not image_url:
                    image_url = await extract_image_from_page(link)

                try:
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


async def is_fashion_related(client, title, content_raw):
    prompt = f"""فقط با "بله" یا "خیر" جواب بده.

آیا این خبر در حوزه مد، فشن، استایل، زیبایی، لباس، ترندهای پوشاک، طراحی لباس یا استایل ایرانی است؟

عنوان: {title}
متن: {content_raw[:500]}

جواب فقط: بله یا خیر"""

    try:
        response = await client.chat.completions.create(
            model="gemini-2.5-pro",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=10,
            temperature=0.0
        )
        answer = response.choices[0].message.content.strip().lower()
        return "بله" in answer
    except Exception as e:
        print(f"[WARN] خطا فیلتر: {str(e)}")
        return False


async def process_fashion_article(client, title, content_raw, link, pub_date):
    translate_prompt = f"""
ترجمه دقیق و حرفه‌ای متن انگلیسی زیر به فارسی روان و مناسب انتشار در کانال مد:

متن:
{content_raw[:1200]}

خروجی فقط ترجمه فارسی باشه، بدون توضیح اضافی.
"""

    try:
        translate_res = await client.chat.completions.create(
            model="gemini-2.5-pro",
            messages=[{"role": "user", "content": translate_prompt}],
            max_tokens=1200,
            temperature=0.3
        )
        translated = translate_res.choices[0].message.content.strip()
    except:
        translated = content_raw[:800] + "..."

    fashion_prompt = f"""
شما ویراستار ارشد خبر مد هستید. متن زیر را به یک پست حرفه‌ای و جذاب برای کانال مد تبدیل کنید.

عنوان: {title}
متن ترجمه‌شده: {translated}
لینک منبع: {link}
تاریخ انتشار: {pub_date.strftime('%Y-%m-%d')}

ساختار پست:
- تیتر جذاب (۸–۱۴ کلمه)
- متن اصلی (۲۰۰–۴۰۰ کلمه، روان و حرفه‌ای)
- تحلیل کوتاه ۲–۳ جمله (درباره تأثیر در بازار مد یا استایل ایرانی)
- هشتگ‌ها در انتها

خروجی فقط پست نهایی باشه، بدون توضیح اضافی.
"""

    try:
        fashion_res = await client.chat.completions.create(
            model="gemini-2.5-pro",
            messages=[{"role": "user", "content": fashion_prompt}],
            max_tokens=1500,
            temperature=0.4
        )
        return fashion_res.choices[0].message.content.strip()
    except:
        return f"**{title}**\n\n{translated[:800]}...\n\nمنبع: {link}"


def get_image_from_rss(entry):
    if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
        return entry.enclosure.href
    if 'media_content' in entry:
        for media in entry.media_content:
            if media.get('medium') == 'image' and media.get('url'):
                return media.get('url')
    return None


async def extract_image_from_page(url):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, timeout=8, headers=headers)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        og = soup.find('meta', property='og:image')
        if og and og.get('content'):
            return og['content']

        for img in soup.find_all('img'):
            src = img.get('src') or img.get('data-src')
            if src and len(src) > 15:
                if any(bad in src.lower() for bad in ['logo', 'icon', 'banner']):
                    continue
                return src
        return None
    except:
        return None


if __name__ == "__main__":
    asyncio.run(main())
