# main.py - Version 1: Simple & Efficient (Recommended for GitHub)
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
        if posted_count >= 1:  # Limit to 1 post per run
            break

        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:5]:  # Check first 5 entries
            link = entry.link.strip()
            
            # Check for duplicates in database
            try:
                existing = databases.list_documents(
                    database_id=database_id, 
                    collection_id=collection_id, 
                    queries=[Query.equal("link", link)]
                )
                if existing['total'] > 0: 
                    continue
            except Exception as e:
                print(f"Database check error: {e}")
                continue

            # Translation with OpenRouter
            title = entry.title
            summary = (entry.get('summary', '') or entry.get('description', ''))[:800]
            
            final_content = await translate_with_openrouter(title, summary)
            
            if not final_content:
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
                        parse_mode='HTML'
                    )
                else:
                    await bot.send_message(
                        chat_id=chat_id, 
                        text=caption, 
                        parse_mode='HTML'
                    )

                # Save to database
                databases.create_document(
                    database_id=database_id, 
                    collection_id=collection_id, 
                    document_id='unique()', 
                    data={
                        'link': link, 
                        'title': title[:250], 
                        'published_at': datetime.now().isoformat()
                    }
                )
                
                posted_count += 1
                break 
            except Exception as e:
                print(f"Error sending/posting: {e}")

    return {"status": "done", "posted": posted_count}

async def translate_with_openrouter(title, text):
    """Translate using OpenRouter free models with fallback"""
    client = AsyncOpenAI(
        api_key=os.environ.get('OPENROUTER_API_KEY'),
        base_url="https://openrouter.ai/api/v1",
    )
    
    prompt = f"""عنوان خبر: {title}
متن: {text}

به عنوان یک سردبیر مجله مد، این خبر را به فارسی جذاب ترجمه کن. 
نکات ست کردن با استایل ایرانی را اضافه کن و از ایموجی استفاده کن. 
فقط متن فارسی را برگردان."""

    models = [
        "qwen/qwen3-next-80b-a3b-instruct:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "stepfun/step-3.5-flash:free",
        "z-ai/glm-4.5-air:free"
    ]
    
    for model in models:
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7,
                max_tokens=800
            )
            content = response.choices[0].message.content.strip()
            if content and len(content) > 50:
                print(f"Translation successful with {model}")
                return content
        except Exception as e:
            print(f"Model {model} failed: {e}")
            await asyncio.sleep(2)
    
    return None

def get_image(entry):
    """Extract best image from RSS entry"""
    if 'media_thumbnail' in entry:
        return entry.media_thumbnail[0].get('url')
    if 'media_content' in entry:
        return entry.media_content[0].get('url') or entry.media_content[0].get('href')
    if 'enclosure' in entry:
        return entry.enclosure.get('href')
    if 'description' in entry:
        soup = BeautifulSoup(entry.description, 'html.parser')
        img = soup.find('img')
        if img and img.get('src'):
            return img['src']
    return None

if __name__ == "__main__":
    asyncio.run(main())