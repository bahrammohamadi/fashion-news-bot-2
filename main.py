import google.generativeai as genai
import feedparser
import requests
import os
import time
import random
from bs4 import BeautifulSoup

PROJECT_ID = "699039d4000e86c2f95e"
DATABASE_ID = "6990a1310017aa6c5c0d"
COLLECTION_ID = "history"
GEMINI_KEY = "AIzaSyCHs8e_s6FryC1_HXgyf3HjJwn5SBx_llI"

def ask_gemini(text):
try:
genai.configure(api_key=GEMINI_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')
prompt = f"تو یک استایلیست حرفه‌ای ایرانی هستی. این متن را به فارسی جذاب و کوتاه برای تلگرام خلاصه کن. نکات آموزشی برای ست کردن این استایل با پوشش ایرانی اضافه کن. از ایموجی استفاده کن:\n\n{text}"
response = model.generate_content(prompt)
return response.text
except Exception as e:
print(f"Gemini Error: {e}")
return None

def is_duplicate(link):
url = f"{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
headers = {"X-Appwrite-Project": PROJECT_ID}
params = {"queries[]": f'equal("link", ["{link}"])'}
try:
res = requests.get(url, headers=headers, params=params, timeout=10)
return res.json().get('total', 0) > 0
except:
return False

def save_to_db(link, title):
url = f"{DATABASE_ID}/collections/{COLLECTION_ID}/documents"
headers = {"X-Appwrite-Project": PROJECT_ID, "Content-Type": "application/json"}
payload = {"documentId": "unique()", "data": {"link": link, "title": title[:250], "date": str(time.ctime())}}
requests.post(url, headers=headers, json=payload, timeout=10)

def main(context):
bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
channel_id = os.getenv('TELEGRAM_CHANNEL_ID')
feeds = ["", "", "", ""]
random.shuffle(feeds)
posted_count = 0
for f_url in feeds:
if posted_count >= 2: break
feed = feedparser.parse(f_url)
for entry in feed.entries[:5]:
if posted_count >= 2: break
if is_duplicate(entry.link): continue
ai_caption = ask_gemini(f"{entry.title}\n{entry.get('summary', '')[:500]}")
if not ai_caption: continue
try:
img_search = f"{entry.title[:30]}+fashion&tbm=isch"
res_img = requests.get(img_search, headers={"User-Agent": "Mozilla/5.0"})
soup = BeautifulSoup(res_img.text, 'html.parser')
img = soup.find_all("img")[2]['src']
final_text = f"{ai_caption}\n\n✨ @irfashionnews\n🏷 #مد #استایل #فشن"
tel_res = requests.post(f"{bot_token}/sendPhoto", data={'chat_id': channel_id, 'photo': img, 'caption': final_text, 'parse_mode': 'HTML'})
if tel_res.status_code == 200:
save_to_db(entry.link, entry.title)
posted_count += 1
time.sleep(10)
except: continue
return context.res.json({"status": "success", "posted": posted_count})
