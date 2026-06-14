# ⚜️ Mahrjameh Fashion News Bot (@irfashionnews)
**Version:** `13.2 — Master Universal Thematic & Telegram Engagement Edition`  
**Runtime:** Python 3.12 / Appwrite Cloud Functions / Telegram API v21+  

---

## 🏛️ Project Architecture & Strategic Blueprint

An enterprise-grade, fully automated AI Fashion Agent designed to curate, translate, and publish luxury fashion news and new product launches specifically optimized for the Iranian sophisticated audience on Telegram.

### 🌟 Key Strategic Highlights (v13.2 Overhaul):
1. **Core Apparel Priority (`شومیز`, `شلوار`, `دامن`, `کت`):** Absolute highest candidate priority and custom XML/JSON entity boosts for target clothing items (Blouses, Pants, Skirts, Coats/Blazers/Suits).
2. **Master Parallel AI Race Engine:** Simultaneous, concurrent multi-provider API racing across **GitHub Models (Azure AI)** (`GITHUB_API_KEY_4`), **Groq Llama 3** (rotating `GROQ_API_KEY` & `GROQ_API_KEY2`), **OpenRouter** (rotating `OPENROUTER_API_KEY` & `OPENROUTER_API_KEY2`), and **Google Gemini** (`GEMINI_API_KEY`). The first provider to deliver pristine Persian editorial output wins instantly.
3. **Autonomous Thematic Calendar Strategist:** Built-in calculation of Iranian Jalaali (Shamsi) seasonal dates and global high-fashion events (Fashion Weeks SS/FW, Met Gala, World Fashion Day, Shab-e Yalda, Norooz, Valentine's) to dynamically inject thematic context into AI styling tips and scale daily output (3–6 posts daily).
4. **Interactive Telegram Polls & Quizzes:** Automated, JSON-driven generation of high-engagement Telegram fashion dilemmas, brand battles, and educational fashion quizzes.
5. **Strict Anti-Spam Rate Limiting:** Built-in Appwrite DB lookbacks to throttle general dry industry news (max once every 12 hours) while ensuring product-first dominance.

---

## 🚀 Deployment Guide (Appwrite Cloud Functions)

### 1. Repository File Structure
For a flawless Appwrite deployment, your repository should contain:
* `main.py` — The core Python execution logic.
* `requirements.txt` — Dependencies (`python-telegram-bot`, `appwrite`, `feedparser`, `beautifulsoup4`, `requests`, `aiohttp`, `lxml`).

*(Note: If you uploaded utility scripts like `apply_*.py` or `fashion_news_bot_final.py` from the redesign workspace, you can safely move them to a `scripts/` folder or delete them to keep your main branch pristine).*

### 2. Environment Variables
Configure the following keys in your Appwrite Cloud Function settings:
```ini
# Telegram Config
TELEGRAM_BOT_TOKEN="your_bot_token"
TELEGRAM_CHANNEL_ID="@irfashionnews" # or chat_id

# Appwrite Database Config
APPWRITE_ENDPOINT="https://cloud.appwrite.io/v1"
APPWRITE_PROJECT_ID="your_project_id"
APPWRITE_API_KEY="your_appwrite_api_key"
APPWRITE_DATABASE_ID="your_database_id"

# AI Provider Keys (Configure at least one; all active ones will race concurrently)
GITHUB_API_KEY_4="ghp_your_github_models_key"
GROQ_API_KEY="gsk_your_groq_key_1"
GROQ_API_KEY2="gsk_your_groq_key_2"
OPENROUTER_API_KEY="sk_or_v1_your_openrouter_key_1"
OPENROUTER_API_KEY2="sk_or_v1_your_openrouter_key_2"
GEMINI_API_KEY="AIzaSy_your_gemini_key" # or GOOGLE_API_KEY

# Content Strategy
PRODUCT_FIRST="1"
MIN_NEWS_SCORE="85"
```

---
**Curated with 🤍 for the advancement of Iranian Fashion Aesthetics.**
