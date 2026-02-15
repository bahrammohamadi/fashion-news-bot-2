import os
import time
import random
import uuid
import requests
import feedparser
from bs4 import BeautifulSoup
import google.generativeai as genai   # ← اصلاح مهم

# ──────────────── تنظیمات ────────────────
GEMINI_KEY = os.getenv("GEMINI_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID")
DATABASE_ID = os.getenv("APPWRITE_DATABASE_ID")
COLLECTION_ID = "history"
APPWRITE_API_KEY = os.getenv("APPWRITE_API_KEY")

# تنظیم Gemini
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel("gemini-2.0-flash")  # یا gemini-1.5-flash-002 اگر در دسترس بود

# ──────────────── توابع کمکی ────────────────
def safe_generate_content(prompt, max_retries=3):
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            print(f"Gemini retry {attempt+1}/{max_retries}: {e}")
            time.sleep(2 ** attempt)
    return None

def is_duplicate(link):
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
        url = f"https://www.google.com/search?q={query}+fashion+style&tbm=isch"
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=8)
        soup = BeautifulSoup(res.text, "html.parser")
        img = soup.find("img", {"class": "YQ4ga"})
        if img and "src" in img.attrs:
            return img["src"]
    except:
        pass
    return None

# ──────────────── تابع اصلی ────────────────
def main(context=None):
    if not all([GEMINI_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID]):
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
    MAX_POSTS = 3  # برای جلوگیری از سوءاستفاده

    for feed_url in feeds:
        if posted >= MAX_POSTS:
            break

        feed = feedparser.parse(feed_url)
        if feed.bozo:
            continue

        for entry in feed.entries[:6]:
            if posted >= MAX_POSTS:
                break

            link = entry.get("link")
            if not link or is_duplicate(link):
                continue

            title = entry.get("title", "")
            summary = entry.get("summary") or entry.get("description") or ""
            content = f"{title}\n{summary[:600]}"

            ai_text = safe_generate_content(
                f"تو یک استایلیست حرفه‌ای ایرانی هستی. "
                f"این متن را کوتاه، جذاب و تلگرامی خلاصه کن. "
                f"نکات ست کردن با پوشش ایرانی اضافه کن. ایموجی استفاده کن:\n\n{content}"
            )

            if not ai_text:
                continue

            image_url = fetch_image(title or "fashion style")
            caption = f"{ai_text}\n\n✨ @irfashionnews\n#مد #استایل #فشن"

            telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            payload = {
                "chat_id": TELEGRAM_CHANNEL_ID,
                "photo": image_url or "https://via.placeholder.com/512?text=Fashion",  # fallback
                "caption": caption[:1024],
                "parse_mode": "Markdown"
            }

            try:
                r = requests.post(telegram_url, data=payload, timeout=12)
                r.raise_for_status()
                save_to_db(link, title)
                posted += 1
                print(f"Posted: {title[:60]}")
            except Exception as e:
                print(f"Telegram error: {e}")

            time.sleep(random.uniform(4.5, 7.2))  # طبیعی‌تر

    return context.res.json({"status": "ok", "posted": posted}) if context else {"posted": posted}


if __name__ == "__main__":
    main()
