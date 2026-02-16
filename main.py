import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot, LinkPreviewOptions
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.exception import AppwriteException
from appwrite.query import Query
from openai import AsyncOpenAI

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
        print("[ERROR] متغیرهای محیطی ناقص! APPWRITE_PROJECT_ID را چک کنید.")
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
        "https://fashionista.com/feed",
        "https://medopia.ir/feed/",
        "https://www.khabaronline.ir/rss/category/مد-زیبایی",
    ]

    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=24)

    posted = False

    for feed_url in rss_feeds:
        if posted:
            break

        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                print(f"[INFO] فید خالی: {feed_url}")
                continue

            is_persian = any(x in feed_url.lower() for x in ['.ir', 'khabaronline', 'medopia'])

            for entry in feed.entries:
                if posted:
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
                content_raw = description[:800]

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
                    print(f"[WARN] خطا در چک دیتابیس (ادامه بدون چک): {str(db_err)}")

                # پرامپت جدید و حرفه‌ای (دقیقاً همون که دادی)
                prompt = f"""
You are a senior fashion journalist with 15+ years of experience writing for Vogue, Harper's Bazaar, and Elle in Persian market.

Objective:
Produce a magazine-quality Persian fashion news article that is analytically strong, professionally written, yet accessible to informed general audiences.

Input:
Title: {title}
Summary: {description}
Content: {content_raw}
Source URL: {feed_url}
Publish Date: {pub_date.strftime('%Y-%m-%d')}

Execution Instructions:
1) Language:
- If the content is in English → translate into fluent, refined Persian.
- If already Persian → professionally edit and elevate.
- Keep all brand names, designer names, fashion houses, event names, and locations in original language.
- Do NOT translate proper nouns.
2) Accuracy Rules:
- Use only information present in the input.
- No speculation.
- No added facts.
- No invented quotes.
- No exaggeration.
3) Tone:
- Professional, analytical, and composed.
- Accessible but not simplistic.
- Use correct fashion terminology when relevant.
- Avoid marketing language.
- Avoid emotional or dramatic adjectives.
4) Structure (strict):
Headline:
- 8–14 words
- Clear and informative
- No sensationalism
Lead:
- 1–2 sentences
- Answer who, what, where, when, and why it matters.
Body:
- 2–4 structured paragraphs
- Expand on key details
- Maintain logical flow
- Avoid repetition
Industry Perspective:
- 2–3 sentences
- Briefly explain why this matters for designers, buyers, retailers, or the broader fashion market.
Length:
- 220–350 words
Output Format:
Headline:
[Persian headline]
Lead:
[Lead paragraph]
Body:
[Main article]
Industry Perspective:
[Analytical closing]
Source: {feed_url}
"""

                content = await translate_with_openrouter(openrouter_client, prompt)

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
                            link_preview_options=LinkPreviewOptions(is_disabled=True),
                            disable_notification=True
                        )

                    posted = True
                    print(f"[SUCCESS] پست موفق ارسال شد: {title[:60]}")

                    try:
                        databases.create_document(
                            database_id=database_id,
                            collection_id=collection_id,
                            document_id='unique()',
                            data={
                                'link': link,
                                'title': title,
                                'published_at': now.isoformat(),
                                'feed_url': feed_url,
                                'created_at': now.isoformat()
                            }
                        )
                        print("[SUCCESS] ذخیره در دیتابیس موفق")
                    except Exception as save_err:
                        print(f"[WARN] خطا در ذخیره دیتابیس: {str(save_err)}")

                except Exception as send_err:
                    print(f"[ERROR] خطا در ارسال پست: {str(send_err)}")

        except Exception as feed_err:
            print(f"[ERROR] خطا در پردازش فید {feed_url}: {str(feed_err)}")

    print(f"[INFO] پایان اجرا - پست ارسال شد: {posted}")
    return {"status": "success", "posted": posted}


async def translate_with_openrouter(client, prompt):
    try:
        response = await client.chat.completions.create(
            model="deepseek/deepseek-r1-0528:free",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.6,
            max_tokens=900
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"[ERROR] خطا در ترجمه با DeepSeek R1: {str(e)}")
        return f"خبر: {title}\n\n{description[:400]}...\n(ترجمه موقت - خطا رخ داد)\nمنبع: {feed_url}"


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
