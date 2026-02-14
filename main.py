from datetime import datetime
import feedparser
import requests
from googletrans import Translator
import os
import time


def main(context=None):  # ← این خط را تغییر دادیم (context=None)
    bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
    channel_id = os.getenv('TELEGRAM_CHANNEL_ID')
    
    if not bot_token or not channel_id:
        print("❌ متغیرهای محیطی تنظیم نشده")
        return
    
    translator = Translator()
    
    # منابع اخبار (جهانی + ایرانی)
    feeds = [
        "https://news.google.com/rss/search?q=%D9%85%D8%AF+%D9%81%D8%B4%D9%86+%D8%A7%D8%B3%D8%AA%D8%A7%DB%8C%D9%84&hl=fa&gl=IR&ceid=IR:fa",
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
        "https://feeds.feedburner.com/fibre2fashion/fashion-news",
        "https://www.fashionista.com/.rss/full"
    ]
    
    posted_count = 0
    today = datetime.now().strftime("%Y-%m-%d")
    
    for feed_url in feeds:
        if posted_count >= 5:  # حداکثر ۵ پست در روز
            break
            
        feed = feedparser.parse(feed_url)
        
        for entry in feed.entries[:3]:  # حداکثر ۳ خبر از هر منبع
            title = entry.title
            summary = entry.get('summary', '') or entry.get('description', '')
            link = entry.link
            
            # ترجمه به فارسی
            try:
                trans_title = translator.translate(title, dest='fa').text
                trans_summary = translator.translate(summary[:300], dest='fa').text if summary else ''
            except Exception as e:
                print(f"خطا در ترجمه: {e}")
                continue
            
            # پیام نهایی
            message = f"""
📰 <b>خبر روز مد و فشن</b>

{trans_title}

{trans_summary}

🔗 <a href="{link}">ادامه خبر</a>

#مد #فشن #استایل_ایرانی #ترند_فصلی #ایران_مد
            """
            
            url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
            data = {
                'chat_id': channel_id,
                'text': message,
                'parse_mode': 'HTML',
                'disable_web_page_preview': False
            }
            
            try:
                response = requests.post(url, data=data, timeout=15)
                response.raise_for_status()  # اگر کد وضعیت غیر 200 بود خطا بده
                
                posted_count += 1
                print(f"✅ پست شد: {trans_title[:50]}...")
            except Exception as e:
                print(f"❌ خطا در ارسال پیام: {e}")
                print(f"پاسخ سرور: {response.text if 'response' in locals() else 'هیچ پاسخی دریافت نشد'}")
            
            time.sleep(4)  # کمی بیشتر صبر می‌کنیم تا از rate limit تلگرام جلوگیری شود
    
    print(f"🎉 {posted_count} خبر امروز پست شد!")


if __name__ == "__main__":
    main()
