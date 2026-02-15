import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot, InputMediaPhoto

async def main(event=None, context=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    
    if not token or not chat_id:
        print("توکن یا chat_id تنظیم نشده!")
        return {"status": "error"}

    bot = Bot(token=token)
    
    # لیست RSSهای مد (می‌تونی اضافه/کم کنی)
    rss_feeds = [
        "https://wwd.com/feed/",                          # WWD - خوب و به‌روز
        "https://www.vogue.com/feed/rss",                 # Vogue اصلی
        "https://fashionista.com/feed",                   # Fashionista
        "https://www.harpersbazaar.com/feed/",            # Harper's Bazaar
        "https://www.elle.com/rss/all.xml",               # ELLE
        # اگر ایرانی خواستی بعدا اضافه کنیم (فعلاً کم RSS معتبر فارسی دارن)
    ]
    
    posted_count = 0
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)  # فقط پست‌های ۲۴ ساعت اخیر
    
    for feed_url in rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                print(f"هیچ انترایی در {feed_url}")
                continue
            
            for entry in feed.entries[:5]:  # حداکثر ۵ تا از هر فید (برای کنترل حجم)
                published = entry.get('published_parsed')
                if not published:
                    continue
                pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                
                if pub_date < yesterday:
                    continue  # قدیمی
                
                title = entry.title
                link = entry.link
                summary = entry.get('summary', '')[:200] + '...' if entry.get('summary') else ''
                
                content = f"📰 {title}\n\n{summary}\n\n🔗 {link}\n#مد #فشن #ترند"
                
                photo_url = None
                if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
                    photo_url = entry.enclosure.href
                
                try:
                    if photo_url:
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=photo_url,
                            caption=content
                        )
                    else:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=content
                        )
                    posted_count += 1
                    print(f"پست شد: {title}")
                except Exception as e:
                    print(f"خطا در ارسال {title}: {str(e)}")
        
        except Exception as e:
            print(f"خطا در فید {feed_url}: {str(e)}")
    
    print(f"کل پست‌های ارسال شده در این اجرا: {posted_count}")
    return {"status": "success", "posted": posted_count}

if __name__ == "__main__":
    asyncio.run(main())
