import os
import time
import random
import uuid
import requests
import feedparser
from bs4 import BeautifulSoup
import google.generativeai as genai

# ──────────────── تنظیمات ────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")          # ← با نام Appwrite همخوانی دارد
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID")
DATABASE_ID = os.getenv("APPWRITE_DATABASE_ID")
COLLECTION_ID = "history"
APPWRITE_API_KEY = os.getenv("APPWRITE_API_KEY")

# تنظیم Gemini
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")  # مدل پایدارتر و سریع‌تر

# ──────────────── توابع کمکی ────────────────
def safe_generate_content(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            print(f"Gemini retry {attempt+1}/{max_retries}: {e}")
            time.sleep(2 ** attempt + 1)
    return "خلاصه‌ای در دسترس نیست"

def is_duplicate(link):
    if not all([PROJECT_ID, DATABASE_ID, APPWRITE_API_KEY]):
        return False
    url = f"https://cloud.appwrite.io/v1/databases/{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
    headers = {
        "X-Appwrite-Project": PROJECT_ID,
        "X-Appwrite-Key": APPWRITE_API_KEY,
        "Content-Type": "application/json"
    }
    params = {"queries[]": f'equal("link", ["{link}"])'}
    try:
        res = requests.get(url, headers=headers, params=params, timeout=8)
        res.raise_for_status()
        return res.json().get("total", 0) > 0
    except:
        return False

def save_to_db(link, title):
    if not all([PROJECT_ID, DATABASE_ID, APPWRITE_API_KEY]):
        return
    url = f"https://cloud.appwrite.io/v1/databases/{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
    headers = {
        "X-Appwrite-Project": PROJECT_ID,
        "X-Appwrite-Key": APPWRITE_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "documentId": str(uuid.uuid4()),
        "data": {
            "link": link,
            "title": title[:250],
            "date": time.time()
        }
    }
    try:
        requests.post(url, headers=headers, json=payload, timeout=8)
    except:
        pass

def fetch_image(query):
    try:
        url = f"https://source.unsplash.com/random/512x512/?fashion,{query.replace(' ', ',')}"
        return url  # Unsplash مستقیم لینک می‌دهد (بدون نیاز به parse)
    except:
        return "https://via.placeholder.com/512?text=Fashion+News"

def escape_markdown(text):
    """تلگرام MarkdownV2 نیاز به escape دارد"""
    chars = r'_*[]()~`>#+-=|{}.!'
    for c in chars:
        text = text.replace(c, f'\\{c}')
    return text

# ──────────────── تابع اصلی ────────────────
def main(context=None):
    required = [GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID]
    if not all(required):
        print("Missing required env variables")
        return context.res.json({"error": "Missing env vars"}) if context else None

    feeds = [
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
        "https://shikpoushan.com/feed/",
        "https://modopia.com/feed/"
    ]

    random.shuffle(feeds)
    posted = 0
    MAX_POSTS = 3

    for feed_url in feeds:
        if posted >= MAX_POSTS:
            break

        try:
            feed = feedparser.parse(feed_url, sanitize_html=True)
            if feed.bozo:
                continue
        except Exception as e:
            print(f"Feed error {feed_url}: {e}")
            continue

        for entry in feed.entries[:6]:
            if posted >= MAX_POSTS:
                break

            link = entry.get("link")
            if not link or is_duplicate(link):
                continue

            title = entry.get("title", "بدون عنوان").strip()
            summary = (entry.get("summary") or entry.get("description") or "").strip()[:600]

            ai_prompt = (
                "تو یک استایلیست حرفه‌ای ایرانی هستی. "
                "این متن را کوتاه، جذاب و مناسب تلگرام خلاصه کن. "
                "نکات ست کردن با پوشش ایرانی اضافه کن. ایموجی استفاده کن:\n\n"
                f"عنوان: {title}\n{summary}"
            )

            ai_text = safe_generate_content(ai_prompt)
            if not ai_text:
                ai_text = f"خبر جدید: {title}\n{summary[:200]}..."

            image_url = fetch_image(title)
            caption = escape_markdown(
                f"{ai_text}\n\n✨ @irfashionnews\n#مد #استایل #فشن"
            )

            telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": image_url,
                "caption": caption[:1024],
                "parse_mode": "MarkdownV2"
            }

            for attempt in range(3):
                try:
                    r = requests.post(telegram_url, data=payload, timeout=12)
                    r.raise_for_status()
                    save_to_db(link, title)
                    posted += 1
                    print(f"Posted: {title[:60]}")
                    break
                except Exception as e:
                    print(f"Telegram retry {attempt+1}: {e}")
                    time.sleep(2 ** attempt + 2)

            time.sleep(random.uniform(3.8, 6.5))

    result = {"status": "ok", "posted": posted}
    return context.res.json(result) if context else result


if __name__ == "__main__":
    main()
