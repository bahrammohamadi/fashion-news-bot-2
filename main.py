import google.generativeai as genai
import feedparser
import requests
import os
import time
import random
from bs4 import BeautifulSoup

# تنظیمات اصلی اپ‌رایت (طبق دیتابیس شما)
PROJECT_ID = "699039d4000e86c2f95e"
DATABASE_ID = "6990a1310017aa6c5c0d"
COLLECTION_ID = "history"
GEMINI_KEY = "AIzaSyCHs8e_s6FryC1_HXgyf3HjJwn5SBx_llI"

def ask_gemini(text):
    """تحلیل و بازنویسی محتوا با نگاه به استایل و فرهنگ ایرانی"""
    try:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        prompt = (
            f"تو یک سردبیر مجله مد و استایلیست حرفه‌ای در ایران هستی. "
            f"این متن خبری را به فارسی خیلی جذاب، صمیمی و کوتاه برای تلگرام خلاصه کن. "
            f"حتماً نکات آموزشی برای ست کردن این استایل با پوشش شیک ایرانی (مانند مانتو، شال یا لایه‌بندی) اضافه کن. "
            f"از ایموجی‌های مرتبط استفاده کن و لحنت کاملاً مجله‌ای باشد:\n\n{text}"
        )
        
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return None

def is_duplicate(link):
    """بررسی تکراری نبودن خبر در دیتابیس"""
    url = f"https://cloud.appwrite.io/v1/databases/{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
    headers = {"X-Appwrite-Project": PROJECT_ID}
    params = {"queries[]": f'equal("link", ["{link}"])'}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        return res.json().get('total', 0) > 0
    except:
        return False

def save_to_db(link, title):
    """ذخیره لینک در دیتابیس برای جلوگیری از تکرار"""
    url = f"https://cloud.appwrite.io/v1/databases/{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
    headers = {"X-Appwrite-Project": PROJECT_ID, "Content-Type": "application/json"}
    payload = {
        "documentId": "unique()",
        "data": {
            "link": link,
            "title": title[:250],
            "date": str(time.ctime())
        }
    }
    requests.post(url, headers=headers, json=payload, timeout=10)

def main(context):
    # ۱. تاخیر تصادفی بین ۱ تا ۱۵ دقیقه برای رفتار انسانی
    time.sleep(random.randint(60, 900))
    
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    channel_id = os.getenv('TELEGRAM_CHANNEL_ID')

    # لیست منابع خبری و آموزشی
    feeds = [
        "https://www.vogue.com/feed/rss", 
        "https://wwd.com/feed/", 
        "https://fashionista.com/.rss/full/",
        "https://www.elle.com/rss/all.xml",
        "https://shikpoushan.com/feed/",
        "https://chibepoosham.com/feed/",
        "https://komodomode.com/mag/feed/",
        "https://modopia.com/feed/",
        "https://www.whowhatwear.com/rss"
    ]
    
    random.shuffle(feeds) 
    posted_count = 0

    for f_url in feeds:
        if posted_count >= 2: break 
        
        feed = feedparser.parse(f_url)
        for entry in feed.entries[:3]:
            if posted_count >= 2: break
            
            link = entry.link
            if is_duplicate(link):
                continue

            try:
                # ۲. پردازش محتوا با Gemini
                content_to_analyze = f"Title: {entry.title}\nSummary: {entry.get('summary', '')[:500]}"
                ai_caption = ask_gemini(content_to_analyze)
                
                if not ai_caption: continue

                # ۳. پیدا کردن تصویر مرتبط
                search_query = entry.title.split('|')[0]
                img_url = f"https://www.google.com/search?q={search_query[:40]}+fashion+style&tbm=isch"
                res_img = requests.get(img_url, headers={"User-Agent": "Mozilla/5.0"})
                soup = BeautifulSoup(res_img.text, 'html.parser')
                img = soup.find_all("img")[2]['src']

                # ۴. ساخت کپشن نهایی با آیدی کانال شما
                final_text = (
                    f"{ai_caption}\n\n"
                    f"✨ @irfashionnews\n"
                    f"🏷 #مد #استایل #آموزش #فشن #تیپ_ایرانی"
                )

                # ۵. ارسال به تلگرام
                requests.post(f"https://api.telegram.org/bot{bot_token}/sendPhoto", data={
                    'chat_id': channel_id,
                    'photo': img,
                    'caption': final_text,
                    'parse_mode': 'HTML'
                })

                save_to_db(link, entry.title)
                posted_count += 1
                time.sleep(15) 
            except:
                continue

    return context.res.json({"status": "success", "posted": posted_count})
