import google.generativeai as genai
import feedparser
import requests
import os
import time
import random
from bs4 import BeautifulSoup

# تنظیمات دیتابیس (طبق عکس‌هایی که فرستادی)
PROJECT_ID = "699039d4000e86c2f95e"
DATABASE_ID = "6990a1310017aa6c5c0d"
COLLECTION_ID = "history"
GEMINI_KEY = "AIzaSyCHs8e_s6FryC1_HXgyf3HjJwn5SBx_llI"

def ask_gemini(text):
    try:
        genai.configure(api_key=GEMINI_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash')
        prompt = (
            f"تو یک استایلیست حرفه‌ای ایرانی هستی. این متن را به فارسی جذاب و کوتاه برای تلگرام خلاصه کن. "
            f"نکات آموزشی برای ست کردن این استایل با پوشش ایرانی (مانند مانتو و شال) اضافه کن. "
            f"از ایموجی استفاده کن و آیدی کانال @irfashionnews را در متن نیار (خودم آخرش می‌ذارم):\n\n{text}"
        )
        response = model.generate_content(prompt)
        return response.text
    except:
        return None

def is_duplicate(link):
    url = f"https://cloud.appwrite.io/v1/databases/{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
    headers = {"X-Appwrite-Project": PROJECT_ID}
    params = {"queries[]": f'equal("link", ["{link}"])'}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        return res.json().get('total', 0) > 0
    except:
        return False

def save_to_db(link, title):
    url = f"https://cloud.appwrite.io/v1/databases/{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
    headers = {"X-Appwrite-Project": PROJECT_ID, "Content-Type": "application/json"}
    payload = {"documentId": "unique()", "data": {"link": link, "title": title[:250], "date": str(time.ctime())}}
    requests.post(url, headers=headers, json=payload, timeout=10)

def main(context):
    # بخش تاخیر تصادفی را برای تست اول غیرفعال کردم تا سریع اجرا شود
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    channel_id = os.getenv('TELEGRAM_CHANNEL_ID')

    feeds = [
        "https://www.vogue.com/feed/rss", 
        "https://wwd.com/feed/", 
        "https://shikpoushan.com/feed/",
        "https://modopia.com/feed/"
    ]
    
    random.shuffle(feeds)
    posted_count = 0

    for f_url in feeds:
        if posted_count >= 2: break
        feed = feedparser.parse(f_url)
        for entry in feed.entries[:3]:
            if posted_count >= 2: break
            if is_duplicate(entry.link): continue

            try:
                ai_caption = ask_gemini(f"{entry.title}\n{entry.get('summary', '')[:500]}")
                if not ai_caption: continue

                # پیدا کردن عکس مرتبط
                img_url = f"https://www.google.com/search?q={entry.title[:30]}+fashion&tbm=isch"
                res_img = requests.get(img_url, headers={"User-Agent": "Mozilla/5.0"})
                soup = BeautifulSoup(res_img.text, 'html.parser')
                img = soup.find_all("img")[2]['src']

                final_text = f"{ai_caption}\n\n✨ @irfashionnews\n🏷 #مد #استایل #فشن"

                requests.post(f"https://api.telegram.org/bot{bot_token}/sendPhoto", data={
                    'chat_id': channel_id, 'photo': img, 'caption': final_text, 'parse_mode': 'HTML'
                })

                save_to_db(entry.link, entry.title)
                posted_count += 1
                time.sleep(5)
            except:
                continue

    return context.res.json({"status": "success", "posted": posted_count})
