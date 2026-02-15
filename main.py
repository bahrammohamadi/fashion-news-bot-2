import os
import asyncio
from telegram import Bot

async def main(event=None, context=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    
    if not token or not chat_id:
        print("توکن یا chat_id تنظیم نشده!")
        return {"status": "error", "message": "متغیرهای محیطی تنظیم نشده"}

    bot = Bot(token=token)
    
    message = "سلام! این پست اتوماتیک از Appwrite است 🚀 (نسخه async)"
    
    try:
        await bot.send_message(chat_id=chat_id, text=message)
        print("پیام با موفقیت ارسال شد!")
        return {"status": "success", "message": "پیام ارسال شد"}
    except Exception as e:
        print(f"خطا در ارسال پیام: {str(e)}")
        return {"status": "error", "message": str(e)}

# اگر مستقیم اجرا شد (برای تست محلی)
if __name__ == "__main__":
    asyncio.run(main())
