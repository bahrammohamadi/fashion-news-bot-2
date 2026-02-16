import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot, LinkPreviewOptions
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.query import Query
from openai import AsyncOpenAI


# ---------------------------
# MAIN
# ---------------------------

async def main(event=None, context=None):
    print("[INFO] اجرای تابع main شروع شد")

    # --- ENV VALIDATION ---
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID")
    appwrite_endpoint = os.environ.get("APPWRITE_ENDPOINT", "https://cloud.appwrite.io/v1")
    appwrite_project = os.environ.get("APPWRITE_PROJECT_ID")
    appwrite_key = os.environ.get("APPWRITE_API_KEY")
    database_id = os.environ.get("APPWRITE_DATABASE_ID")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")

    collection_id = "history"

    if not all([token, chat_id, appwrite_project, appwrite_key, database_id, openrouter_key]):
        print("[ERROR] متغیرهای محیطی ناقص هستند.")
        return {"status": "error", "message": "Missing environment variables"}

    # --- INIT SERVICES ---
    bot = Bot(token=token)

    aw_client = Client()
    aw_client.set_endpoint(appwrite_endpoint)
    aw_client.set_project(appwrite_project)
    aw_client.set_key(appwrite_key)

    databases = Databases(aw_client)

    openrouter_client = AsyncOpenAI(
        api_key=openrouter_key,
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

            for entry in feed.entries:
                if posted:
                    break

                published = entry.get("published_parsed") or entry.get("updated_parsed")
                if not published:
                    continue

                pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_date < time_threshold:
                    continue

                title = entry.title.strip()
                link = entry.link.strip()
                description = (entry.get("summary") or "").strip()
                content_raw = description[:1500]

                # --- DUPLICATE CHECK ---
                try:
                    existing = databases.list_documents(
                        database_id=database_id,
                        collection_id=collection_id,
                        queries=[Query.equal("link", link)]
                    )

                    if existing["total"] > 0:
                        print(f"[INFO] تکراری رد شد: {title[:50]}")
                        continue

                except Exception as db_err:
                    print(f"[WARN] خطا در چک دیتابیس: {db_err}")

                # --- AI PROMPT ---
                prompt = build_prompt(
                    title, description, content_raw, feed_url, pub_date
                )

                ai_text = await generate_news(openrouter_client, prompt)

                if not ai_text:
                    continue

                final_text = f"{ai_text}\n\n🔗 {link}"

                # --- TELEGRAM SEND SAFE ---
                try:
                    image_url = get_image_from_rss(entry)

                    # اگر متن برای کپشن طولانی است، پیام متنی بفرست
                    if image_url and len(final_text) <= 1000:
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=image_url,
                            caption=final_text,
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
                    print(f"[SUCCESS] ارسال شد: {title[:60]}")

                    # --- SAVE HISTORY ---
                    try:
                        databases.create_document(
                            database_id=database_id,
                            collection_id=collection_id,
                            document_id="unique()",
                            data={
                                "link": link,
                                "title": title,
                                "published_at": pub_date.isoformat(),
                                "feed_url": feed_url,
                                "created_at": now.isoformat()
                            }
                        )
                    except Exception as save_err:
                        print(f"[WARN] ذخیره نشد: {save_err}")

                except Exception as send_err:
                    print(f"[ERROR] خطا در ارسال تلگرام: {send_err}")

        except Exception as feed_err:
            print(f"[ERROR] خطا در پردازش فید {feed_url}: {feed_err}")

    print(f"[INFO] پایان اجرا - پست ارسال شد: {posted}")
    return {"status": "success", "posted": posted}


# ---------------------------
# PROMPT BUILDER
# ---------------------------

def build_prompt(title, description, content_raw, source, pub_date):
    return f"""
You are a professional fashion news editor.

Input:
- Title: {title}
- Description: {description}
- Full Content: {content_raw}
- Source: {source}
- Publish Date: {pub_date.strftime('%Y-%m-%d')}

Tasks:
1) Detect the language.
2) If English → translate to Persian.
3) If Persian → keep as is.
4) Rewrite into a professional Persian fashion news article.

Strict Rules:
- Formal journalistic tone.
- Strong lead paragraph (Who, What, Where, When, Why).
- No emojis.
- No hashtags.
- No speculation.
- No invented facts.
- Keep brand names unchanged.
- Add short neutral analysis at the end.
- If info missing, do not assume.
Output:
Headline:
Body:
"""


# ---------------------------
# AI GENERATION
# ---------------------------

async def generate_news(client, prompt):
    try:
        response = await client.chat.completions.create(
            model="google/gemma-3n-4b:free",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
            max_tokens=1200
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"[ERROR] خطا در تولید خبر: {e}")
        return None


# ---------------------------
# IMAGE EXTRACTOR
# ---------------------------

def get_image_from_rss(entry):
    if "enclosures" in entry:
        for enclosure in entry.enclosures:
            if enclosure.get("type", "").startswith("image/"):
                return enclosure.get("href")

    if "media_content" in entry:
        for media in entry.media_content:
            if media.get("medium") == "image":
                return media.get("url")

    return None


# ---------------------------
# RUN
# ---------------------------

if __name__ == "__main__":
    asyncio.run(main())
