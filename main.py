import os
import asyncio
import feedparser
import time
from datetime import datetime, timezone
from telegram import Bot
from bs4 import BeautifulSoup
from appwrite.client import Client
from appwrite.services.databases import Databases
from google import genai # نسخه نهایی API

async def main(event=None, context=None):
    # تنظیمات اولیه
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    gemini_key = os.environ.get('GEMINI_KEY')
    project_id = '699039d4000e86c2f95e'
    database_id = '6990a1310017aa6c5c0d'
    collection_id = 'history'

    bot = Bot(token=token)
    
    # تنظیمات اپ‌رایت
    aw_client = Client()
    aw_client.set_endpoint("https://cloud.appwrite.io/v1")
    aw_client.set_project(project_id)
    aw_client.set_key(os.environ.get('APPWRITE_API_KEY'))
    databases = Databases(aw_client)

    # لیست فیدها
    rss_feeds = ["https://www.vogue.com/feed/rss", "https://wwd.com/feed/"]
    
    posted_count = 0
    
    for feed_url in rss_feeds:
        if posted_count >= 1: break # فقط ۱ خبر برای کل اجرا

        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:5]: # بررسی ۵ خبر اول برای پیدا کردن یکی که تکراری نباشد
            link = entry.link.strip()
            
            # بررسی تکراری بودن در دیتابیس
            try:
                from appwrite.query import Query
                existing = databases.list_documents(database_id, collection_id, [Query.equal("link", link)])
                if existing['total'] > 0: continue
            except: pass

            # --- بخش ترجمه هوشمند با Gemini ---
            title = entry.title
            summary = entry.get('summary', '')[:800]
            
            final_content = await translate_with_gemini(gemini_key, title, summary)
            
            if not final_content:
                continue # اگر ترجمه نشد برو سراغ خبر بعدی

            # ارسال به تلگرام
            try:
                image_url = get_image(entry)
                caption = f"{final_content}\n\n✨ @irfashionnews\n🔗 {link}"
                
                if image_url:
                    await bot.send_photo(chat_id=chat_id, photo=image_url, caption=caption, parse_mode='HTML')
                else:
                    await bot.send_message(chat_id=chat_id, text=caption, parse_mode='HTML')

                # ذخیره در دیتابیس
                databases.create_document(database_id, collection_id, 'unique()', {
                    'link': link, 'title': title[:250], 'published_at': datetime.now().isoformat()
                })
                
                posted_count = 1
                break 
            except Exception as e:
                print(f"Error sending: {e}")

    return {"status": "done", "posted": posted_count}

async def translate_with_gemini(api_key, title, text, retries=2):
    """ترجمه با متد جدید Gemini 2.0 و قابلیت Retry"""
    client = genai.Client(api_key=api_key)
    prompt = f"عنوان خبر: {title}\nمتن: {text}\n\nبه عنوان یک سردبیر مجله مد، این خبر را به فارسی جذاب ترجمه کن. نکات ست کردن با استایل ایرانی را اضافه کن و از ایموجی استفاده کن. فقط متن فارسی را برگردان."
    
    for i in range(retries + 1):
        try:
            response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
            if response.text:
                return response.text
        except Exception as e:
            print(f"Retry {i} failed: {e}")
            await asyncio.sleep(2)
    return None

def get_image(entry):
    if 'enclosure' in entry: return entry.enclosure.href
    if 'media_content' in entry: return entry.media_content[0]['url']
    return None

if __name__ == "__main__":
    asyncio.run(main())
