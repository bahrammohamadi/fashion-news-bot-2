import os
import time
import random
import requests
import feedparser
from bs4 import BeautifulSoup
from openai import OpenAI

# تنظیمات
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")

# کلاینت DeepSeek (سازگار با OpenAI)
client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

def get_ai_summary(title: str, summary: str) -> str:
    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {
                    "role": "system",
                    "content": "تو یک استایلیست حرفه‌ای ایرانی هستی. متن رو کوتاه، جذاب و مناسب تلگرام خلاصه کن. نکات ست کردن با پوشش ایرانی اضافه کن. ایموجی استفاده کن."
                },
                {
                    "role": "user",
                    "content": f"عنوان: {title}\n{summary[:600]}"
                }
            ],
            temperature=0.7,
            max_tokens=280,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"DeepSeek error: {e}")
        return f"خبر جدید: {title}\n{summary[:200]}..."

def fetch_image(query: str) -> str:
    try:
        # Unsplash – پایدار و رایگان
        return f"https://source.unsplash.com/random/512x512/?fashion,{query.replace(' ', ',')}"
    except:
        return "https://via.placeholder.com/512?text=Fashion+News"

def send_to_telegram(caption: str, photo_url: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
    payload = {
        "chat_id": TELEGRAM_CHANNEL_ID,
        "photo": photo_url,
        "caption": caption[:1024],
        "parse_mode": "Markdown"
    }
    for attempt in range(3):
        try:
            r = requests.post(url, data=payload, timeout=12)
            r.raise_for_status()
            return True
        except Exception as e:
            print(f"Telegram retry {attempt+1}: {e}")
            time.sleep(3)
    return False

def main(context=None):
    required = [DEEPSEEK_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID]
    if not all(required):
        print("Missing required env variables")
        return context.res.json({"error": "Missing env"}) if context else None

    feeds = [
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
        "https://shikpoushan.com/feed/",
        "https://modopia.com/feed/"
    ]

    random.shuffle(feeds)
    posted = 0
    MAX_POSTS = 4

    for feed_url in feeds:
        if posted >= MAX_POSTS:
            break

        try:
            feed = feedparser.parse(feed_url)
            if feed.bozo:
                continue
        except Exception as e:
            print(f"Feed error: {e}")
            continue

        for entry in feed.entries[:8]:
            if posted >= MAX_POSTS:
                break

            link = entry.get("link")
            if not link:
                continue

            title = entry.get("title", "بدون عنوان").strip()
            summary = (entry.get("summary") or entry.get("description") or "").strip()[:600]

            ai_text = get_ai_summary(title, summary)

            image_url = fetch_image(title)
            caption = f"{ai_text}\n\n✨ @irfashionnews\n#مد #استایل #فشن"

            if send_to_telegram(caption, image_url):
                posted += 1
                print(f"Posted: {title[:60]}")

            time.sleep(random.uniform(3.0, 5.5))

    result = {"status": "ok", "posted": posted}
    return context.res.json(result) if context else result


if __name__ == "__main__":
    main()
