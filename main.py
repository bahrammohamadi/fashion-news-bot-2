import os
from telegram import Bot

def main(event=None, context=None):
    token = os.environ.get('TELEGRAM_BOT_TOKEN')
    chat_id = os.environ.get('TELEGRAM_CHANNEL_ID')
    message = "سلام! این پست اتوماتیک از Appwrite است 🚀"

    if not token or not chat_id:
        print("توکن یا chat_id تنظیم نشده!")
        return {"status": "error"}

    bot = Bot(token=token)
    bot.send_message(chat_id=chat_id, text=message)
    print("پیام ارسال شد!")
    return {"status": "success"}
