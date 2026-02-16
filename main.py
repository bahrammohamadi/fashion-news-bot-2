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
        print("خطا: متغیرهای محیطی ناقص! APPWRITE_PROJECT_ID را چک کنید.")
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
                content_raw = description[:1000]

                # چک تکراری (اگر DB مشکل داشت، رد نمی‌شه)
                try:
                    existing = databases.list_documents(
                        database_id=database_id,
                        collection_id=collection_id,
                        queries=[Query.equal("link", link)]
                    )
                    if existing['total'] > 0:
                        continue
                except Exception as db_err:
                    print(f"خطا DB: {str(db_err)} - ادامه بدون چک تکراری")

                # پرامپت حرفه‌ای جدید
                prompt = f"""You are a professional fashion news editor.
Input:
- Title: {title}
- Description: {description}
- Full Content: {content_raw}
- Source: {feed_url}
- Publish Date: {pub_date.strftime('%Y-%m-%d')}

Tasks:
1) Detect the language of the content.
2) If the text is in English, translate it accurately into fluent Persian.
3) If the text is already Persian, do NOT translate it.
4) Rewrite the final Persian text into a professional, journalistic fashion news article.
Strict Guidelines:
- Use a formal but engaging news tone.
- Start with a strong lead paragraph that summarizes the key news (Who, What, Where, When, Why).
- Keep the structure journalistic and logical.
- Avoid exaggerated marketing tone.
- No emojis.
- No hashtags.
- No casual or conversational style.
- Keep brand names, designer names, fashion houses, and locations unchanged.
- Add context if necessary to clarify the importance of the news in the fashion industry.
- Keep it concise but complete.
- Do not invent facts.
- Do not speculate.
- Only use information from the input.
Output format:
Headline:
[Professional news headline in Persian]
Body:
[Well-structured news article in Persian]
Additionally:
- Add a short analytical paragraph at the end explaining the potential impact of this news on the fashion industry or market.
- Maintain objectivity.
- Avoid personal opinions.
- Write in a tone suitable for a professional fashion news website.
If information is missing, do not fill gaps with assumptions."""

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
                            disable_web_page_preview=True,
                            disable_notification=True
                        )

                    posted = True
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
                        print("ذخیره DB موفق")
                    except Exception as save_err:
                        print(f"خطا ذخیره DB: {str(save_err)}")

                except Exception as send_err:
                    print(f"خطا ارسال: {str(send_err)}")

        except Exception as feed_err:
            print(f"خطا فید {feed_url}: {str(feed_err)}")

    print(f"پایان اجرا - پست ارسال شد: {posted}")
    return {"status": "success", "posted": posted}


async def translate_with_openrouter(client, prompt):
    try:
        response = await client.chat.completions.create(
            model="meta-llama/llama-3.3-70b-instruct:free",  # مدل سریع و فعال
            messages=[{"role": "user", "content": prompt}],
            temperature=0.7,
            max_tokens=800
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"خطا ترجمه: {str(e)}")
        return "(ترجمه موقت - خطا رخ داد)\n\nلینک خبر اصلی را ببینید."


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
