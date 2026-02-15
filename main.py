import os
import time
import random
import uuid
import requests
import feedparser
from google import genai
from bs4 import BeautifulSoup


# ========= CONFIG =========

PROJECT_ID = os.getenv("APPWRITE_PROJECT_ID")
DATABASE_ID = os.getenv("APPWRITE_DATABASE_ID")
COLLECTION_ID = "history"
APPWRITE_API_KEY = os.getenv("APPWRITE_API_KEY")

GEMINI_KEY = os.getenv("GEMINI_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")


# ========= GEMINI (NEW SDK) =========

def ask_gemini(text):
    try:
        client = genai.Client(api_key=GEMINI_KEY)

        prompt = (
            "تو یک استایلیست حرفه‌ای ایرانی هستی. "
            "این متن را کوتاه، جذاب و تلگرامی خلاصه کن. "
            "نکات ست کردن با پوشش ایرانی اضافه کن. "
            "از ایموجی استفاده کن:\n\n"
            + text
        )

        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt
        )

        return response.text

    except Exception as e:
        print("Gemini Error:", e)
        return None


# ========= APPWRITE =========

def is_duplicate(link):
    url = f"https://cloud.appwrite.io/v1/databases/{DATABASE_ID}/collections/{COLLECTION_ID}/documents"

    headers = {
        "X-Appwrite-Project": PROJECT_ID,
        "X-Appwrite-Key": APPWRITE_API_KEY
    }

    params = {
        "queries[]": f'equal("link", ["{link}"])'
    }

    try:
        res = requests.get(url, headers=headers, params=params, timeout=10)
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
            "date": time.ctime()
        }
    }

    requests.post(url, headers=headers, json=payload, timeout=10)


# ========= IMAGE =========

def fetch_image(query):
    try:
        url = f"https://www.google.com/search?q={query}+fashion&tbm=isch"
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)

        soup = BeautifulSoup(res.text, "html.parser")
        images = soup.find_all("img")

        if len(images) >= 3:
            return images[2]["src"]

    except:
        pass

    return None


# ========= MAIN =========

def main(context):
    feeds = [
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
        "https://shikpoushan.com/feed/",
        "https://modopia.com/feed/"
    ]

    random.shuffle(feeds)
    posted = 0

    for feed_url in feeds:
        if posted >= 2:
            break

        feed = feedparser.parse(feed_url)

        for entry in feed.entries[:5]:
            if posted >= 2:
                break

            if is_duplicate(entry.link):
                continue

            ai_text = ask_gemini(
                entry.title + "\n" + entry.get("summary", "")[:500]
            )

            if not ai_text:
                continue

            image_url = fetch_image(entry.title)
            if not image_url:
                continue

            caption = (
                f"{ai_text}\n\n"
                "✨ @irfashionnews\n"
                "#مد #استایل #فشن"
            )

            telegram_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"

            res = requests.post(
                telegram_url,
                data={
                    "chat_id": TELEGRAM_CHANNEL_ID,
                    "photo": image_url,
                    "caption": caption
                },
                timeout=10
            )

            if res.status_code == 200:
                save_to_db(entry.link, entry.title)
                posted += 1

            time.sleep(10)

    return context.res.json({
        "status": "ok",
        "posted": posted
    })
