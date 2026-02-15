import os
import asyncio
import feedparser
from datetime import datetime, timedelta, timezone
from telegram import Bot
import google.generativeai as genai

async def main(event=None, context=None):
    # گرفتن متغیرهای محیطی
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    gemini_key = os.environ.get('GEMINI_API_KEY')

    if not token or not chat_id or not gemini_key:
        print("یکی از متغیرهای محیطی تنظیم نشده است!")
        return {"status": "error", "message": "متغیرهای لازم موجود نیستند"}

    # تنظیم بات تلگرام
    bot = Bot(token=token)

    # تنظیم Gemini با مدل جدید
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel('gemini-2.5-flash')  # مدل جدید و سریع برای 2026 - اگر خطا داد به 'gemini-3-flash-preview' تغییر بده

    # لیست فیدهای RSS (خارجی‌ها برای ترجمه، می‌تونی فارسی اضافه کنی مثل https://medopia.ir/feed/)
    rss_feeds = [
        "https://www.vogue.com/feed/rss",
        "https://wwd.com/feed/",
        "https://www.harpersbazaar.com/rss/fashion.xml",
        "https://fashionista.com/feed",
        "https://feeds.feedburner.com/fibre2fashion/fashion-news",
        "https://www.businessoffashion.com/feed/",
    ]

    posted_count = 0
    now = datetime.now(timezone.utc)
    time_threshold = now - timedelta(hours=24)  # فقط اخبار ۲۴ ساعت اخیر

    for feed_url in rss_feeds:
        try:
            feed = feedparser.parse(feed_url)
            if not feed.entries:
                print(f"فید خالی: {feed_url}")
                continue

            print(f"پردازش فید: {feed_url} — {len(feed.entries)} مورد")

            for entry in feed.entries[:5]:  # حداکثر ۵ تا از هر فید
                # چک تاریخ
                published = entry.get('published_parsed') or entry.get('updated_parsed')
                if not published:
                    continue
                pub_date = datetime(*published[:6], tzinfo=timezone.utc)
                if pub_date < time_threshold:
                    continue

                title_en = entry.title.strip()
                link = entry.link
                summary_en = (entry.get('summary') or entry.get('description') or '').strip()[:350]
                if summary_en:
                    summary_en += '...'

                # فارسی‌سازی با Gemini (تیتر جذاب + متن)
                farsi_content = await rewrite_with_gemini(model, title_en, summary_en)

                final_text = (
                    f"{farsi_content}\n\n"
                    f"🔗 {link}\n"
                    f"#مد #استایل #ترند #فشن_ایرانی #مهرجامه"
                )

                # پیدا کردن عکس (اگر وجود داشت)
                photo_url = None
                if 'enclosure' in entry and entry.enclosure.get('type', '').startswith('image/'):
                    photo_url = entry.enclosure.href
                elif 'media_content' in entry:
                    for media in entry.media_content:
                        if media.get('medium') == 'image' and media.get('url'):
                            photo_url = media.get('url')
                            break

                # ارسال پست: اول عکس اگر بود، با caption تیتر+متن
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
                            disable_web_page_preview=False,
                            disable_notification=True
                        )
                    posted_count += 1
                    print(f"پست موفق: {title_en[:60]}...")
                except Exception as send_err:
                    print(f"خطا در ارسال پست '{title_en[:40]}...': {str(send_err)}")

        except Exception as feed_err:
            print(f"خطا در پردازش فید {feed_url}: {str(feed_err)}")

    print(f"اجرای این دور: {posted_count} پست ارسال شد")
    return {"status": "success", "posted_count": posted_count}


async def rewrite_with_gemini(model, title_en, summary_en):
    prompt = f"""این خبر مد را به فارسی طبیعی و جذاب برای خانم‌های ایرانی بازنویسی کن.
ابتدا یک تیتر جذاب و فارسی بنویس (کوتاه و گیرا، مثل یک جمله واقعی زندگی).
بعد متن اصلی خبر را در ۳ تا ۵ جمله کوتاه بنویس: با تنش واقعی شروع کن (مثل سردرگمی خرید لباس، تکراری شدن استایل، یا فشار انتخاب مناسب).
بعد ترند را به عنوان راه‌حل معرفی کن.
بدون تبلیغ مستقیم، بدون قیمت، بدون لینک فروش.
فقط خروجی: تیتر در خط اول، سپس متن در خطوط بعدی (بدون برچسب اضافی).

عنوان انگلیسی: {title_en}
خلاصه انگلیسی: {summary_en}
"""

    try:
        response = await asyncio.to_thread(model.generate_content, prompt)  # async wrapper برای sync call
        text = response.text.strip()
        if not text:
            raise ValueError("پاسخ خالی از Gemini")
        print(f"Gemini موفق تولید کرد: {text[:60]}...")
        return text
    except Exception as e:
        error_msg = str(e)
        print(f"Gemini خطا داد: {error_msg}")
        raise  # حالا execution fail می‌شه تا لاگ واضح بشه - بدون fallback


if __name__ == "__main__":
    asyncio.run(main())
