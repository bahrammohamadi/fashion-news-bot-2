# main_v2.py - Version 2: With Better Error Handling & Limits (For Production)
import os
import asyncio
import feedparser
from datetime import datetime
from telegram import Bot
from openai import AsyncOpenAI
from appwrite.client import Client
from appwrite.services.databases import Databases
from appwrite.query import Query
from bs4 import BeautifulSoup
import time

MAX_POSTS_PER_RUN = 1
SLEEP_BETWEEN_MODELS = 3  # Seconds

async def main(event=None, context=None):
    # Environment variables
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    openrouter_key = os.environ.get('OPENROUTER_API_KEY')
    appwrite_key = os.environ.get('APPWRITE_API_KEY')
    project_id = '699039d4000e86c2f95e'
    database_id = '6990a1310017aa6c5c0d'
    collection_id = 'history'

    if not all([token, chat_id, openrouter_key, appwrite_key]):
        print("Missing environment variables!")
        return {"status": "error", "message": "Missing env vars"}

    bot = Bot(token=token)
    
    # Appwrite setup
    aw_client = Client()
    aw_client.set_endpoint("https://cloud.appwrite.io/v1")
    aw_client.set_project(project_id)
    aw_client.set_key(appwrite_key)
    databases = Databases(aw_client)

    # RSS feeds
    rss_feeds = ["https://www.vogue.com/feed/rss", "https://wwd.com/feed/"]
    
    posted_count = 0
    
    for feed_url in rss_feeds:
        if posted_count >= MAX_POSTS_PER_RUN:
            break

        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            print(f"Feed parse error for {feed_url}: {e}")
            continue

        for entry in feed.entries[:6]:  # Slightly more entries for better chance
            if posted_count >= MAX_POSTS_PER_RUN:
                break

            link = entry.link.strip()
            
            # Check for duplicates
            try:
                existing = databases.list_documents(
                    database_id=database_id, 
                    collection_id=collection_id, 
                    queries=[Query.equal("link", link)]
                )
                if existing['total'] > 0: 
                    continue
            except Exception as e:
                print(f"Database error: {e}")
                # Continue anyway, but log

            # Translation
            title = entry.title
            summary = (entry.get('summary', '') or entry.get('description', ''))[:750]
            
            final_content = await translate_with_openrouter_v2(title, summary)
            
            if not final_content:
                time.sleep(SLEEP_BETWEEN_MODELS)  # Pause if translation fails
                continue

            # Send to Telegram
            try:
                image_url = get_image(entry)
                caption = f"{final_content}\n\n✨ @irfashionnews\n🔗 {link}"
                
                if image_url:
                    await bot.send_photo(
                        chat_id=chat_id, 
                        photo=image_url, 
                        caption=caption, 
                        parse_mode='HTML',
                        disable_notification=True
                    )
                else:
                    await bot.send_message(
                        chat_id=chat_id, 
                        text=caption, 
                        parse_mode='HTML',
                        disable_notification=True
                    )

                # Save to database
                databases.create_document(
                    database_id=database_id, 
                    collection_id=collection_id, 
                    document_id='unique()', 
                    data={
                        'link': link, 
                        'title': title[:250], 
                        'published_at': datetime.now().isoformat(),
                        'translated_content': final_content[:500]  # Store snippet
                    }
                )
                
                posted_count += 1
                print(f"Posted successfully: {title[:50]}...")
                await asyncio.sleep(5)  # Short pause after post
                break 
            except Exception as e:
                print(f"Send/Save error: {e}")

    return {"status": "completed", "posted": posted_count}

async def translate_with_openrouter_v2(title, text):
    """Enhanced translation with more retries and logging"""
    client = AsyncOpenAI(
        api_key=os.environ.get('OPENROUTER_API_KEY'),
        base_url="https://openrouter.ai/api/v1",
    )
    
    prompt = f"""عنوان خبر: {title}
متن: {text}

به عنوان سردبیر مجله مد ایرانی، این را به فارسی شیک و جذاب ترجمه کن. 
نکات استایل ایرانی (مانتو، شال، اکسسوری) اضافه کن. ایموجی بگذار. 
فقط فارسی نهایی."""

    models = [
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "stepfun/step-3.5-flash:free",
        "z-ai/glm-4.5-air:free"
    ]
    
    for i, model in enumerate(models):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=850
            )
            content = response.choices[0].message.content.strip()
            if content and len(content) > 60 and len(content) < 1200:  # Quality check
                print(f"✓ Translation OK with {model}")
                return content
        except Exception as e:
            print(f"✗ Model {model} (attempt {i+1}) failed: {str(e)[:100]}")
            if i < len(models) - 1:
                await asyncio.sleep(SLEEP_BETWEEN_MODELS)
    
    print("All models failed - skipping")
    return None

def get_image(entry):
    """Improved image extraction"""
    if 'media_thumbnail' in entry and entry.media_thumbnail:
        return entry.media_thumbnail[0].get('url')
    if 'media_content' in entry and entry.media_content:
        return entry.media_content[0].get('url') or entry.media_content[0].get('href')
    if 'enclosure' in entry:
        return entry.enclosure.get('href')
    if entry.get('description'):
        soup = BeautifulSoup(entry.description, 'html.parser')
        img = soup.find('img', src=True)
        if img:
            return img['src']
    return None

if __name__ == "__main__":
    asyncio.run(main())