import os
import time
import random
import requests
import feedparser

def main(context=None):
    bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")

    if not bot_token or not channel_id:
        print("Missing Telegram env vars")
        return context.res.json({"error": "Missing env"}) if context else None

    feeds = [
        "https://news.google.com/rss/search?q=مد+فشن+استایل&hl=fa&gl=IR&ceid=IR:fa",
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
    ]

    posted = 0
    MAX_POSTS = 3

    for feed_url in feeds:
        if posted >= MAX_POSTS:
            break

        feed = feedparser.parse(feed_url)
        if feed.bozo:
            continue

        for entry in feed.entries[:5]:
            if posted >= MAX_POSTS:
                break

            title = entry.get("title", "بدون عنوان").strip()
            summary = (entry.get("summary") or entry.get("description") or "").strip()[:300]
            link = entry.get("link", "")

            if not title or not link:
                continue

            message = f"""
📰 <b>خبر روز مد و فشن</b>

{title}

{summary}

🔗 <a href="{link}">ادامه خبر</a>

#مد #فشن #استایل_ایرانی
            """.strip()

            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {
                "chat_id": channel_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": False
            }

            try:
                r = requests.post(url, data=data, timeout=10)
                r.raise_for_status()
                posted += 1
                print(f"Posted: {title[:60]}")
            except Exception as e:
                print(f"Telegram error: {e}")

            time.sleep(random.uniform(2, 4))

    result = {"status": "ok", "posted": posted}
    return context.res.json(result) if context else result

if __name__ == "__main__":
    main()
