import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot
import google.generativeai as genai

async def main(event=None, context=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    gemini_key = os.environ.get('GEMINI_API_KEY')

    if not token or not chat_id or not gemini_key:
        print("یکی از متغیرهای محیطی تنظیم نشده!")
        return {"status": "error"}

    bot = Bot(token=token)
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel('gemini-2.5-flash')  # یا gemini-2.5-flash-latest اگر خطا داد

    # لیست فیدها – ۱۰ خارجی + ۱۰ فارسی/ایرانی
    rss_feeds = [
        # خارجی (ترجمه می‌شوند)
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
        "https://www.harpersbazaar.com/rss/fashion.xml",
        "https://fashionista.com/feed",
        "https://www.businessoffashion.com/feed/",
        "https://www.elle.com/rss/fashion.xml",
        "https://www.refinery29.com/rss.xml",
        "https://www.thecut.com/feed",
        "https://www.whowhatwear.com/rss",
        "https://feeds.feedburner.com/fibre2fashion/fashion-news",
        
        # فارسی/ایرانی (مستقیم پست می‌شوند – بدون ترجمه)
        "https://medopia.ir/feed/",
        "https://www.digikala.com/mag/feed/?category=مد",
        "https://www.khabaronline.ir/rss/category/مد-زیبایی",
        "https://www.isna.ir/rss/category/فرهنگ-هنر",
        "https://www.tasnimnews.com/fa/rss/feed/0/0/0/سبک-زندگی",
        "https://www.hamshahrionline.ir/rss/category/مد",
        "https://fararu.com/rss/category/مد-زیبایی",
        "https://www.beytoote.com/rss/fashion",
        # اگر فیدهای بیشتری داری اضافه کن
        "https://www.voguearabia.com/feed",  # اگر فارسی داشته باشه
        "https://www.zoomit.ir/feed/category/fashion-beauty/"  # زومیت بخش زیبایی
    ]

    posted_count = 0
    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=24)

    for feed_url in rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                continue

            # تشخیص اینکه فید فارسی است یا خارجی (برای ترجمه یا مستقیم)
            is_persian = any(x in feed_url.lower() for x in ['.ir', 'khabaronline', 'isna', 'tasnim', 'hamshahrionline', 'fararu', 'beytoote', 'digikala', 'zoomit'])

            for entry in feed.entries[:4]:  # حداکثر ۴ تا از هر فید برای کنترل حجم
                published = entry.get('published_parsed') or entry.get('updated_parsed')
                if not published:
                    continue
                pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_date < time_threshold:
                    continue

                title = entry.title.strip()
                link = entry.link  # فقط برای لاگ
                summary = (entry.get('summary') or entry.get('description') or '').strip()[:400]

                # اگر فید فارسی باشد → مستقیم استفاده کن
                if is_persian:
                    content = f"{title}\n\n{summary}"
                else:
                    # ترجمه و بازنویسی با Gemini
                    content = await rewrite_with_gemini(model, title, summary)

                final_text = f"{content}\n\n#مد #استایل #ترند #فشن_ایرانی #مهرجامه"

                photo_url = None
                if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
                    photo_url = entry.enclosure.href
                elif 'media_content' in entry:
                    for media in entry.media_content:
                        if media.get('medium') == 'image' and media.get('url'):
                            photo_url = media.get('url')
                            break

                try:
                    if photo_url:
                        await bot.send_photo(
                            chat_id=chat_id,
                            photo=photo_url,
                            caption=final_text,
                            parse_mode='HTML',
                            disable_notification=True
                        )
                    else:
                        await bot.send_message(
                            chat_id=chat_id,
                            text=final_text,
                            disable_web_page_preview=True,
                            disable_notification=True
                        )
                    posted_count += 1
                    print(f"پست موفق: {title[:60]}...")
                except Exception as e:
                    print(f"خطا ارسال: {str(e)}")

        except Exception as e:
            print(f"خطا فید {feed_url}: {str(e)}")

    print(f"این اجرا: {posted_count} پست ارسال شد")
    return {"status": "success", "posted": posted_count}


async def rewrite_with_gemini(model, title_en, summary_en):
    prompt = f"""این خبر مد را به فارسی طبیعی و روان برای مخاطب ایرانی بازنویسی کن.
ابتدا یک تیتر جذاب و کوتاه فارسی بنویس.
بعد متن خبر را در ۱ تا ۲ پاراگراف (حداکثر ۸-۱۰ جمله کوتاه) بنویس:
- با یک موقعیت واقعی و احساسی شروع کن (سردرگمی خرید، تکراری شدن لباس‌ها، فشار انتخاب استایل و ...).
- ترند جدید را به عنوان راه‌حل یا ایده جالب معرفی کن.
- لحن دوستانه و نزدیک به گفتگوی روزمره باشد.
- بدون تبلیغ مستقیم، بدون قیمت، بدون لینک.

خروجی دقیقاً این شکل:
تیتر جذاب
پاراگراف اول
پاراگراف دوم (اگر لازم بود)

عنوان انگلیسی: {title_en}
خلاصه انگلیسی: {summary_en}"""

    try:
        response = await asyncio.to_thread(model.generate_content, prompt)
        text = response.text.strip()
        if not text or len(text) < 30:
            raise ValueError("پاسخ نامناسب")
        print(f"Gemini موفق: {text[:80]}...")
        return text
    except Exception as e:
        print(f"Gemini خطا: {str(e)}")
        raise


if __name__ == "__main__":
    asyncio.run(main())
