from datetime import datetime
import feedparser
import requests
from googletrans import Translator
import os
import time

def main():
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
        if posted_count >= 5:
            break
            
        feed = feedparser.parse(feed_url)
        
        for entry in feed.entries[:3]:
            title = entry.title
            summary = entry.get('summary', '') or entry.get('description', '')
            link = entry.link
            
            try:
                trans_title = translator.translate(title, dest='fa').text
                trans_summary = translator.translate(summary[:300], dest='fa').text if summary else ''
            except:
                continue
            
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
            
            response = requests.post(url, data=data)
            
            if response.status_code == 200:
                posted_count += 1
                print(f"✅ پست شد: {trans_title[:50]}...")
            else:
                print(f"❌ خطا در ارسال: {response.text}")
            
            time.sleep(3)
    
    print(f"🎉 {posted_count} خبر امروز پست شد!")

if __name__ == "__main__":
    main()
