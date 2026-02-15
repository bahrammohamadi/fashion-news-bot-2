import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot
from openai import AsyncOpenAI  # برای DeepSeek

async def main(event=None, context=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    deepseek_key = os.environ.get('DEEPSEEK_API_KEY')
    
    if not token or not chat_id or not deepseek_key:
        print("یکی از متغیرها تنظیم نشده!")
        return {"status": "error"}

    bot = Bot(token=token)
    client = AsyncOpenAI(
        api_key=deepseek_key,
        base_url="https://api.deepseek.com/v1"
    )

    rss_feeds = [
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
        "https://www.harpersbazaar.com/rss/fashion.xml",
        "https://fashionista.com/feed",
        "https://feeds.feedburner.com/fibre2fashion/fashion-news",
    ]
    
    posted_count = 0
    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=24)

    for feed_url in rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue
            
            for entry in feed.entries[:4]:
                published = entry.get('published_parsed') or entry.get('updated_parsed')
                if not published:
                    continue
                pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_date < time_threshold:
                    continue
                
                title = entry.title.strip()
                link = entry.link
                summary = (entry.get('summary') or entry.get('description') or '')[:300]
                
                # DeepSeek فارسی‌سازی
                farsi_text = await rewrite_with_deepseek(client, title, summary)
                
                content = f"{farsi_text}\n\n🔗 {link}\n#مد #استایل #ترند #فشن_ایرانی"
                
                photo_url = None
                if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
                    photo_url = entry.enclosure.href
                elif 'media_content' in entry:
                    for media in entry.media_content:
                        if media.get('medium') == 'image':
                            photo_url = media.get('url')
                            break
                
                try:
                    if photo_url:
                        await bot.send_photo(chat_id=chat_id, photo=photo_url, caption=content)
                    else:
                        await bot.send_message(chat_id=chat_id, text=content)
                    posted_count += 1
                    print(f"پست موفق: {title}")
                except Exception as e:
                    print(f"خطا ارسال: {str(e)}")
        
        except Exception as e:
            print(f"خطا فید {feed_url}: {str(e)}")

    print(f"این اجرا: {posted_count} پست")
    return {"status": "success", "posted": posted_count}

async def rewrite_with_deepseek(client, title_en, summary_en):
    prompt = f"""
این خبر مد رو به فارسی طبیعی و جذاب برای خانم‌های ایرانی بازنویسی کن.
با تنش واقعی شروع کن (مثل: همیشه لباس خوب پیدا نمی‌شه؟ استایلت تکراری شده؟ سردرگمی خرید؟)
بعد ترند رو به عنوان راه‌حل نشون بده.
۳–۵ جمله کوتاه کافیه. بدون تبلیغ مستقیم یا قیمت. فقط محتوا.

عنوان: {title_en}
خلاصه: {summary_en}

فقط متن فارسی بنویس:
"""
    try:
        response = await client.chat.completions.create(
            model="deepseek-chat",  # یا "deepseek-v3" اگر مدل جدیدتر بخوای
            messages=[{"role": "user", "content": prompt}],
            max_tokens=180,
            temperature=0.7
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"DeepSeek خطا: {e}")
        return f"📰 {title_en}\n{summary_en[:200]}..."  # fallback

if __name__ == "__main__":
    asyncio.run(main())
