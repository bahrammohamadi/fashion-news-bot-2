import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot

async def main(event=None, context=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    
    if not token or not chat_id:
        print("توکن یا chat_id تنظیم نشده!")
        return {"status": "error", "message": "متغیرها تنظیم نشده"}

    bot = Bot(token=token)
    
    # لیست RSSهای فعال (می‌تونی تغییر بدی)
    rss_feeds = [
        "https://www.vogue.com/feed/rss",                    # Vogue - runway و trends
        "https://wwd.com/feed/",                             # WWD - صنعت مد، خیلی پست روزانه
        "https://www.harpersbazaar.com/rss/fashion.xml",     # Harper's Bazaar - fashion section
        "https://fashionista.com/feed",                      # Fashionista - اخبار مستقل
        "https://feeds.feedburner.com/fibre2fashion/fashion-news",  # Fibre2Fashion - اخبار صنعت
    ]
    
    posted_count = 0
    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=24)  # فقط ۲۴ ساعت اخیر
    
    for feed_url in rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                print(f"هیچ پستی در فید: {feed_url}")
                continue
            
            print(f"فید {feed_url} - تعداد کل انتری‌ها: {len(feed.entries)}")
            
            for entry in feed.entries[:4]:  # حداکثر ۴ تا از هر فید (برای ۵ فید ≈ ۲۰ پست max)
                # چک تاریخ انتشار
                published = entry.get('published_parsed') or entry.get('updated_parsed')
                if not published:
                    continue
                pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                
                if pub_date < time_threshold:
                    continue  # قدیمی
                
                title = entry.title.strip()
                link = entry.link
                summary = (entry.get('summary') or entry.get('description') or '')[:250]
                if summary:
                    summary += '...\n'
                
                content = f"📰 {title}\n\n{summary}🔗 {link}\n\n#مد #فشن #ترند #FashionNews"
                
                # عکس اگر وجود داشت
                photo_url = None
                if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
                    photo_url = entry.enclosure.href
                elif 'media_content' in entry and entry.media_content:
                    for media in entry.media_content:
                        if media.get('medium') == 'image':
                            photo_url = media.get('url')
                            break
                
                try:
                    if photo_url:
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=photo_url,
                            caption=content,
                            parse_mode='HTML'  # اگر لینک clickable بخوای
                        )
                    else:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=content,
                            disable_web_page_preview=False
                        )
                    posted_count += 1
                    print(f"ارسال موفق: {title} (از {feed_url})")
                except Exception as send_error:
                    print(f"خطا ارسال پست '{title}': {str(send_error)}")
        
        except Exception as feed_error:
            print(f"خطا در پردازش فید {feed_url}: {str(feed_error)}")
    
    print(f"اجرای این دور: {posted_count} پست ارسال شد.")
    return {"status": "success", "posted_count": posted_count}

if __name__ == "__main__":
    asyncio.run(main())
