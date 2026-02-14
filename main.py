import feedparser
import requests
import os
import time
from bs4 import BeautifulSoup
from googletrans import Translator

# تنظیمات دیتابیس طبق تصاویر ارسالی شما
PROJECT_ID = "699039d4000e86c2f95e"
DATABASE_ID = "6990a1310017aa6c5c0d"
COLLECTION_ID = "history"

def is_duplicate(link):
    """بررسی دیتابیس برای جلوگیری از پست تکراری"""
    url = f"https://cloud.appwrite.io/v1/databases/{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
    headers = {"X-Appwrite-Project": PROJECT_ID}
    # کوئری برای چک کردن وجود لینک
    params = {"queries[]": f'equal("link", ["{link}"])'}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
        return res.json().get('total', 0) > 0
    except:
        return False

def save_to_db(link, title):
    """ذخیره در دیتابیس اپرایت"""
    url = f"https://cloud.appwrite.io/v1/databases/{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
    headers = {
        "X-Appwrite-Project": PROJECT_ID,
        "Content-Type": "application/json"
    }
    payload = {
        "documentId": "unique()",
        "data": {
            "link": link,
            "title": title[:255],
            "date": str(time.strftime("%Y-%m-%d %H:%M"))
        }
    }
    try:
        requests.post(url, headers=headers, json=payload, timeout=10)
    except:
        pass

def get_image(text):
    """پیدا کردن عکس مرتبط"""
    try:
        url = f"https://www.google.com/search?q={text[:30]}+fashion+trend&tbm=isch"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        return soup.find_all("img")[2]['src']
    except:
        return None

def main(context):
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    channel_id = os.getenv('TELEGRAM_CHANNEL_ID')
    translator = Translator()

    # منابع خبری
    feeds = ["https://www.vogue.com/feed/rss", "https://wwd.com/feed/"]
    
    posted_count = 0
    for f_url in feeds:
        if posted_count >= 2: break
        
        feed = feedparser.parse(f_url)
        for entry in feed.entries[:3]:
            if posted_count >= 2: break
            
            link = entry.link
            if is_duplicate(link): continue

            try:
                # ترجمه و خلاصه‌سازی
                fa_title = translator.translate(entry.title, dest='fa').text
                summary = entry.get('summary', '')[:200]
                fa_summary = translator.translate(summary, dest='fa').text if summary else ""

                caption = (
                    f"👗 <b>{fa_title}</b>\n\n"
                    f"💡 {fa_summary}...\n\n"
                    f"🔗 <a href='{link}'>ادامه خبر</a>\n\n"
                    f"🏷 #مد #فشن #استایل"
                )

                img = get_image(entry.title)

                # ارسال به تلگرام
                t_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
                res = requests.post(t_url, data={
                    'chat_id': channel_id,
                    'photo': img,
                    'caption': caption,
                    'parse_mode': 'HTML'
                })

                if res.status_code == 200:
                    save_to_db(link, fa_title)
                    posted_count += 1
                    time.sleep(5)
            except:
                continue

    return context.res.json({"status": "success", "posted": posted_count})
