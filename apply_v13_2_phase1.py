import re

with open('fashion_news_bot_final.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update Version
text = text.replace(
    "# Version:    13.1 — Master Multi-Provider Edition (GitHub Models + Multiple Keys + Full Parallel Race)",
    "# Version:    13.2 — Master Universal Thematic & Telegram Engagement Edition"
)
text = text.replace(
    "log(\"═══ FashionBot v12.1 Intelligence Agent started ═══\")",
    "log(\"═══ FashionBot v13.2 Universal Thematic & Engagement Agent started ═══\")"
)

# 2. Update AI Prompts in Section 2 and Section 13
pos_sec2 = text.find('# SECTION 2 — AI PROMPT TEMPLATES')
pos_sec3 = text.find('# SECTION 3 — SCHEMA DETECTION')

if pos_sec2 != -1 and pos_sec3 != -1:
    new_sec2 = """# SECTION 2 — AI PROMPT TEMPLATES (v13.2 - EDITORIAL & THEMATIC OPTIMIZED)
# ═══════════════════════════════════════════════════════════

_PROMPT_UNIFIED = '''تو یک استراتژیست ارشد محتوا، توسعه‌دهنده هوشمند مد و سردبیر خلاق مجله لوکس «مهرجامه» (@irfashionnews) در ایران هستی.
وظیفه تو تولید یک پست کامل، متمایز و بسیار جذاب تلگرامی با فرمت HTML بر اساس خبر یا معرفی محصول انگلیسی زیر است تا تعامل مخاطبان ایرانی را به حداکثر برساند.

**فضای فصلی و مناسبتی کنونی کانال:**
{occasion}

**اولویت‌های مطلق محتوا (Core Focus):**
بیشتر محصولات و توجه کانال ما روی **شومیز (blouse)**، **شلوار (pants/trousers)**، **کت و کتونی/بلیزر (coat/jacket/blazer)** و **دامن (skirt)** از برندهای مطرح است. حتماً در خروجی خود روی این ۴ دسته محصول مانور ویژه بده و جذابیت‌های طراحی و استایل آن‌ها را توصیف کن.

**ساختار پرامپت ترکیبی حرفه‌ای (ترجمه دقیق + ویرایش editorial):**
۱. **ترجمه وفادارانه و سلیس:** اصل خبر و نکات فنی را به دقیق‌ترین و روان‌ترین شکل ممکن به فارسی برگردان. از ترجمه ماشینی، تحت‌اللفظی و جملات سنگین یا گنگ به‌شدت اجتناب کن. اصطلاحات تخصصی مد را به بهترین معادل فارسی تبدیل کن یا با املای صحیح لاتین بنویس.
۲. **خلاصه‌سازی حرفه‌ای:** اصل پیام و جذاب‌ترین بخش‌های خبر یا کالکشن را گلچین و خلاصه کن تا در حوصله مخاطب شبکه اجتماعی (تلگرام) بگنجد و زیاده‌گویی نشود.

**قوانین ساختاری و ویراستاری فارسی (سخت‌گیرانه):**
- نیم‌فاصله‌ها را با دقت کامل رعایت کن (می‌شود، برندهای، طراحی‌شده، شومیزهای، کالکشن‌های، می‌پوشد).
- افعال را کامل و کتابی/رسمی بنویس (است، می‌باشد) و از لحن محاوره‌ای یا عامیانه دوری کن.
- نام برندها (مانند Zara, Dior, Chanel, Gucci) حتماً با الفبای لاتین نوشته شوند.
- از «گیومه فارسی» برای نقل‌قول‌ها استفاده کن و تمام اعداد را فارسی بنویس (مانند ۱۰، ۲۰۲۶، ۱۰۰).
- خروجی باید مستقیماً با کدهای HTML تلگرام تگ‌گذاری شده باشد (فقط تگ‌های مجاز <b> و <i>) و هیچ‌گونه تگ اضافه (مانند ```html) نداشته باشد.
- طول کل متن خروجی باید به شدت کنترل شود: **زیر ۱۰۲۰ کاراکتر** (ترجیحاً حداکثر ۸۵۰ کاراکتر) تا در کپشن عکس تلگرام جا شود و قطع نشود.

💎 قالب نهایی تلگرام:
✨ <b>[تیتر جذاب، کوتاه و شیک فارسی با یک ایموجی متناسب]</b>

[دو الی سه جمله ترجمه دقیق و خلاصه editorial (body) بسیار روان و داستان‌گونه از اصل خبر یا معرفی محصول]

🧥 <b>جزئیات طراحی و نوآوری:</b>
[یک پاراگراف کوتاه یا چند بولت‌پوینت مینیمال درباره متریال، برش‌ها یا نکات خاص طراحی به‌ویژه شومیز، شلوار، دامن یا کت]

💡 <b>نکته استایلی (Tip):</b>
[یک پیشنهاد شیک و الهام‌بخش برای ست کردن این آیتم‌ها در استایل روزمره یا رسمی برای مخاطب ایرانی]

{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

خبر انگلیسی:
عنوان: {title}
محتوا: {input_text}'''

_PROMPT_INTELLIGENCE_NEWS = '''تو یک استراتژیست ارشد محتوا، توسعه‌دهنده هوشمند مد و سردبیر خلاق مجله لوکس «مهرجامه» (@irfashionnews) هستی.
ماموریت تو تبدیل متن انگلیسی خبر زیر به یک پست تلگرامی بسیار حرفه‌ای، جذاب، باکیفیت و متمایز به زبان فارسی است تا تعامل مخاطبان ایرانی را حداکثر کند.

**فضای فصلی و مناسبتی کنونی کانال:**
{occasion}

**اولویت‌های مطلق محتوا (Core Focus):**
کانال ما به‌طور ویژه روی **شومیز (blouse)**، **شلوار (pants/trousers)**، **کت و کتونی/بلیزر (coat/jacket/blazer)** و **دامن (skirt)** از برندهای معتبر جهانی تمرکز دارد. 
اگر در متن این خبر یا رویداد عمومی (runway, trend, sustainability, business, celebrity) اشاره‌ای به این آیتم‌ها، ترندهای مرتبط با آن‌ها یا کالکشن‌های جدیدشان شده است، حتماً آن را در کانون توجه تحلیلی خود قرار بده.

**ساختار پرامپت ترکیبی حرفه‌ای (ترجمه دقیق + ویرایش editorial):**
۱. **ترجمه دقیق + روان‌سازی و ویرایش Editorial:** اصل خبر، رویدادهای تجاری/هنری، تحولات برندها و نقل‌قول‌ها را به‌صورت کاملاً دقیق ترجمه کن. به هیچ‌وجه از ترجمه تحت‌اللفظی یا جملات نامفهوم ماشینی استفاده نکن. جملات باید اقتدار و اصالت یک مجله رده‌بالای مد را بازتاب دهند.
۲. **خلاصه‌سازی و چکیده‌نویسی:** بخش‌های حاشیه‌ای و طولانی خبر را حذف کن و مغز متفکر و پیام اصلی خبر را در قالبی خلاصه، کوبنده و مناسب برای دنبال‌کنندگان کانال تلگرام ارائه بده.

**قوانین ویراستاری فارسی (سخت‌گیرانه):**
- رعایت دقیق نیم‌فاصله‌ها (می‌شود، برندهای، می‌پوشد، شرکت‌های، تحولات، ترندهای، کالکشن‌های).
- نگارش رسمی و کامل افعال (است، می‌باشد، اعلام کرد) و اجتناب از واژگان محاوره‌ای.
- درج نام برندها (مانند Zara, Dior, Chanel, Gucci) و اصطلاحات تخصصی با الفبای لاتین.
- استفاده از «گیومه فارسی» و درج اعداد به‌صورت فارسی (مانند ۱۰، ۲۰۲۶، ۵۰).
- خروجی باید مستقیماً با کدهای HTML تلگرام تگ‌گذاری شده باشد (فقط تگ‌های مجاز <b> و <i>) و هیچ‌گونه تگ اضافه (مانند ```html) نداشته باشد.

**ساختار بصری و قالب نهایی تلگرام:**
✨ <b>[تیتر خبری جذاب، کوبنده و کوتاه فارسی با یک ایموجی متناسب]</b>

[دو الی سه جمله ترجمه دقیق و خلاصه editorial (body) سلیس و داستان‌گونه که پیام اصلی خبر را به زیبایی روایت می‌کند]

<b>ابعاد و تحلیل خبر:</b>
[یک پاراگراف کوتاه یا چند بولت‌پوینت تحلیلی درباره پیامدهای این خبر برای ترندها، بازار یا صنعت مد (به‌ویژه شومیز، شلوار، دامن یا کت)]

💡 <b>نکته استایلی / ترند (Tip):</b>
[یک نکته کاربردی، الهام‌بخش یا نتیجه‌گیری جالب برای دنبال‌کنندگان کانال]

<b>منبع:</b> {source}
{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

**کنترل طول متن:**
- طول متن نهایی حتماً باید **زیر ۱۰۲۰ کاراکتر** (ترجیحاً حداکثر ۸۵۰ کاراکتر) باشد تا در کپشن تصاویر تلگرام جا شود و خوانا بماند.

متن انگلیسی خبر:
عنوان: {title}
محتوا: {input_text}'''

_PROMPT_INTELLIGENCE_PRODUCT = '''تو یک استراتژیست ارشد محتوا، توسعه‌دهنده هوشمند مد و سردبیر خلاق مجله لوکس «مهرجامه» (@irfashionnews) هستی.
ماموریت تو تبدیل متن انگلیسی زیر (معرفی محصول یا کالکشن جدید) به یک کپشن تلگرامی بسیار حرفه‌ای، جذاب، باکیفیت و متمایز به زبان فارسی است تا تعامل مخاطبان ایرانی را به حداکثر برساند.

**فضای فصلی و مناسبتی کنونی کانال:**
{occasion}

**اولویت‌های مطلق محتوا (Core Focus):**
محصولات اصلی ما **شومیز (blouse)**، **شلوار (pants/trousers)**، **کت و کتونی/بلیزر (coat/jacket/blazer)** و **دامن (skirt)** هستند. اگر آیتم‌هایی از این ۴ دسته در خبر/کالکشن وجود دارد، بالاترین اولویت و توجه را به آن‌ها اختصاص بده و جزئیات برش، پارچه و استایل آن‌ها را باوقار و لوکس برجسته کن.

**ساختار پرامپت ترکیبی حرفه‌ای (ترجمه دقیق + ویرایش editorial):**
۱. **ترجمه دقیق + روان‌سازی و ویرایش Editorial:** متن را به‌طور دقیق و وفادارانه به فارسی برگردان و با لحن لوکس، شیک و اقتدار یک مجله معتبر مد ویرایش کن. از ترجمه تحت‌اللفظی یا جملات ماشینی بپرهیز. کلمات تخصصی مد را به بهترین معادل فارسی تبدیل کن یا با املای لاتین بنویس.
۲. **رعایت قوانین ویراستاری:** نیم‌فاصله‌ها را کاملاً و با وسواس رعایت کن (می‌شود، برندهای، می‌پوشد، طراحی‌شده، شومیزهای، کالکشن‌های). تمام اعداد فارسی باشند (مانند ۱۰، ۲۰۲۶، ۱۰۰) و از «گیومه فارسی» استفاده شود.

**قالب نهایی تلگرام (فقط تگ‌های HTML شامل <b> و <i>):**
خروجی باید دقیقاً شامل ساختار زیر با ایموجی‌های مینیمال باشد و بدون هیچ تگ اضافه (مثل ```html) تولید شود:

✨ <b>[یک تیتر جذاب، تبلیغاتی، کوتاه و شیک فارسی با یک ایموجی متناسب]</b>

[دو الی سه جمله ترجمه دقیق و خلاصه editorial (body) بسیار روان و جذاب که ماهیت کالکشن، نوآوری برند و محصولات جدید را روایت می‌کند]

<b>جزئیات طراحی و پارچه:</b>
[یک پاراگراف کوتاه یا چند بولت‌پوینت شیک درباره برش‌ها، پالت رنگی یا متریال؛ با تاکید ویژه بر شومیز، شلوار، دامن یا کت]

💡 <b>نکته استایلی (Tip):</b>
[یک پیشنهاد الهام‌بخش، حرفه‌ای و کاربردی برای ست کردن این آیتم‌ها در استایل روزمره یا رسمی برای مخاطب ایرانی]

<b>منبع:</b> {source}
{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

**کنترل طول متن:**
- متن نهایی حتماً باید **زیر ۱۰۲۰ کاراکتر** (ترجیحاً حداکثر ۸۵۰ کاراکتر) باشد تا در کپشن آلبوم تصاویر تلگرام به‌خوبی نمایش داده شود و خوانا بماند.

متن انگلیسی خبر/محصول:
عنوان: {title}
محتوا: {input_text}'''

"""
    text = text[:pos_sec2] + new_sec2 + text[pos_sec3:]

# Replace _PROMPT_POLL_GENERATOR
old_poll_prompt = """_PROMPT_POLL_GENERATOR = '''تو استایلیست ارشد و استراتژیست تعامل در یک مجله لوکس مد (مهرجامه) هستی.
وظیفه تو طراحی یک نظرسنجی (Poll) بسیار جذاب و چالشی برای مخاطبان ایرانی است.

موضوع نظرسنجی باید یکی از موارد زیر باشد (به صورت تصادفی یکی را انتخاب کن):
- دو راهی استایل (مثلا: در یک قرار کاری مهم، ترنچ کت یا مانتو کتی؟)
- انتخاب ترند فصل (مثلا: رنگ ترند پاییز امسال؟ شرابی یا زیتونی؟)
- انتخاب آیتم کلاسیک (مثلا: کیف شانل کلاسیک یا دیور لیدی؟)

قوانین:
- سوال باید کوتاه، جذاب و دقیق باشد (حداکثر ۱۰۰ کاراکتر).
- دقیقاً ۲ تا ۴ گزینه (Option) بده. هر گزینه باید کوتاه و وسوسه‌انگیز باشد (حداکثر ۴۰ کاراکتر).
- خروجی تو باید دقیقاً با فرمت JSON و بدون هیچ متن اضافه‌ای باشد. هیچ قالب markdown مثل ```json را ننویس. فقط خود جیسون را برگردان.

ساختار JSON خروجی:
{
  "question": "سوال جذاب تو اینجا",
  "options": ["گزینه ۱", "گزینه ۲", "گزینه ۳"]
}
'''"""

new_poll_prompt = """_PROMPT_POLL_GENERATOR = '''تو یک استراتژیست ارشد محتوا، توسعه‌دهنده هوشمند مد، استایلیست و طراح تعامل مجله لوکس «مهرجامه» (@irfashionnews) هستی.
ماموریت تو تولید یک نظرسنجی (Poll) یا کوییز (Quiz) فشن بسیار جذاب، باکیفیت و تعاملی برای مخاطبان ایرانی کانال تلگرام است تا میزان مشارکت (engagement) را به حداکثر برساند.

**فضای مناسبتی و فصلی کنونی کانال:**
{occasion}

**موضوعات استراتژیک پیشنهادی (یکی را با سلیقه حرفه‌ای خود انتخاب کن):**
۱. **دوراهی استایل (Style Dilemma - Core Apparel Focus):** مقایسه دو آیتم پوشیدنی جذاب (مثلاً: «در یک قرار کاری مهم، ترنچ کت کلاسیک را ترجیح می‌دهید یا مانتو کتی ساختاریافته؟» یا «برای استایل روزمره این فصل، کدام مدل شومیز، شلوار یا دامن را می‌پسندید؟»).
۲. **نبرد برندهای مطرح (Brand Battle):** مقایسه سبک طراحی و هویت برندها (مثلاً Zara در برابر Mango یا Chanel در برابر Dior).
۳. **کوییز تخصصی مد (Fashion Quiz):** یک سوال جذاب و آموزنده درباره تاریخچه مد، اصطلاحات فشن، پارچه‌ها یا ترندها (با تعیین گزینه صحیح و ارائه یک جمله توضیح).

**قوانین خروجی تلگرام:**
- خروجی تو باید **فقط و مستقیماً یک ساختار JSON معتبر و تمیز** باشد و هیچ متن اضافه یا تگ ```json در ابتدا و انتها نداشته باشد.
- حداکثر تعداد گزینه‌ها ۱۰ عدد است (ترجیحاً بین ۳ الی ۴ گزینه شیک، وسوسه‌انگیز و کوتاه).
- در صورت انتخاب حالت quiz، حتماً شماره گزینه صحیح (`correct_option_id` از 0) و یک جمله توضیح آموزنده (`explanation`) ارائه بده. در حالت regular این دو فیلد را null بگذار.

ساختار JSON مورد انتظار:
{
  "type": "regular",  // یا "quiz"
  "question": "[سوال جذاب، کوتاه و چالش‌برانگیز فارسی]",
  "options": [
    "[گزینه اول با یک ایموجی شیک]",
    "[گزینه دوم]",
    "[گزینه سوم]"
  ],
  "correct_option_id": 0,  // در حالت regular برابر null و در حالت quiz شماره ایندکس گزینه صحیح
  "explanation": "[در حالت regular برابر null و در حالت quiz یک جمله توضیح آموزنده و الهام‌بخش]"
}'''"""

if old_poll_prompt in text:
    text = text.replace(old_poll_prompt, new_poll_prompt)
else:
    # Fuzzy replace if whitespace differs
    pos_poll = text.find('_PROMPT_POLL_GENERATOR =')
    pos_next = text.find('# SECTION 14 — SCRAPING')
    if pos_poll != -1 and pos_next != -1:
        text = text[:pos_poll] + new_poll_prompt + "\n\n" + text[pos_next:]

# 3. Add FashionCalendarStrategist before main()
pos_main = text.find('async def main(event=None, context=None):')
if pos_main != -1:
    strategist_code = """# ═══════════════════════════════════════════════════════════
# NEW: FASHION CALENDAR STRATEGIST (Iranian & Global Occasions & Thematic Engine)
# ═══════════════════════════════════════════════════════════

class FashionCalendarStrategist:
    \"\"\"
    A powerful Python strategist that calculates Iranian (Jalali/Shamsi) dates,
    identifies national/global occasions (Norooz, Yalda, Met Gala, Fashion Weeks),
    determines seasonal thematic focus, and establishes optimal posting frequencies.
    \"\"\"
    def __init__(self, now_utc: datetime):
        self.now_ir = now_utc + timedelta(hours=3, minutes=30)
        self.gy     = self.now_ir.year
        self.gm     = self.now_ir.month
        self.gd     = self.now_ir.day
        self.jy, self.jm, self.jd = self._gregorian_to_jalali(self.gy, self.gm, self.gd)

    @staticmethod
    def _gregorian_to_jalali(gy, gm, gd):
        g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
        gy2   = gy if gm > 2 else gy - 1
        days  = (355666 + (365 * gy) + ((gy2 + 3) // 4) - ((gy2 + 99) // 100) + ((gy2 + 399) // 400) + gd + g_d_m[gm - 1])
        jy    = -1595 + (33 * (days // 12053))
        days %= 12053
        jy   += 4 * (days // 1461)
        days %= 1461
        if days > 365:
            jy  += (days - 1) // 365
            days = (days - 1) % 365
        if days < 186:
            jm = 1 + (days // 31)
            jd = 1 + (days % 31)
        else:
            jm = 7 + ((days - 186) // 30)
            jd = 1 + ((days - 186) % 30)
        return jy, jm, jd

    def get_daily_strategy(self) -> dict:
        occasion_name        = ""
        thematic_focus       = ""
        thematic_keywords    = []
        target_posts_per_day = 4  # Default regular posting frequency (3-5 posts daily)

        # 1. Iranian (Jalali) Occasions & Seasonal Shifts
        if self.jm == 12 and self.jd >= 15:
            occasion_name        = "پیشواز نوروز و خرید بهاری"
            thematic_focus       = "کالکشن‌های عیدانه، استایل بهاری، شومیزهای مجلسی و کت و شلوارهای شیک"
            thematic_keywords    = ["spring", "norooz", "new collection", "blouse", "suit", "بهار", "عید", "شومیز", "کت"]
            target_posts_per_day = 6  # 3-6 posts daily during peak shopping season
        elif self.jm == 1 and self.jd <= 13:
            occasion_name        = "عید نوروز و دید و بازدید"
            thematic_focus       = "استایل شیک و باوقار نوروزی، ترکیب رنگ‌های شاد بهاری، دامن و شومیز"
            thematic_keywords    = ["spring", "floral", "bright", "skirt", "blouse", "نوروز", "بهار", "دامن"]
            target_posts_per_day = 5
        elif self.jm == 9 and self.jd >= 25:
            occasion_name        = "شب یلدا و آغاز زمستان"
            thematic_focus       = "استایل یلدایی، پالت رنگی اصیل (زرشکی، قرمز، سبز)، پالتوهای گرم و پشمی"
            thematic_keywords    = ["red", "green", "velvet", "coat", "winter", "یلدا", "قرمز", "پالتو", "زرشکی"]
            target_posts_per_day = 6
        elif self.jm in (4, 5, 6):
            occasion_name        = "فصل تابستان و پوشاک خنک"
            thematic_focus       = "استایل خنک تابستانی، لباس‌های لینن و پنبه‌ای، شلوار و شومیزهای راحت و روشن"
            thematic_keywords    = ["summer", "linen", "cotton", "light", "pants", "blouse", "تابستان", "خنک", "لینن"]
        elif self.jm in (7, 8):
            occasion_name        = "فصل پاییز و لایه‌لایه‌پوشی"
            thematic_focus       = "استایل پاییزی، ترنچ کت، کت‌های ساختاریافته، رنگ‌های نود و گرم (کرم، قهوه‌ای، آجری)"
            thematic_keywords    = ["autumn", "fall", "trench", "coat", "blazer", "layering", "پاییز", "ترنچ کت", "کت"]
        elif self.jm in (10, 11):
            occasion_name        = "فصل زمستان و پالتوهای لوکس"
            thematic_focus       = "پالتوهای لوکس پشمی، کاپشن‌های شیک، کت و شلوارهای گرم و باوقار"
            thematic_keywords    = ["winter", "wool", "overcoat", "suit", "jacket", "زمستان", "پالتو", "پشمی"]

        # 2. Global Gregorian Events
        if (self.gm == 9 and self.gd >= 5) or (self.gm == 10 and self.gd <= 5):
            occasion_name += " | هفته‌های مد جهانی (Fashion Weeks SS)"
            thematic_keywords.extend(["runway", "fashion week", "ss26", "catwalk", "collection"])
            target_posts_per_day = 6
        elif (self.gm == 2 and self.gd >= 10) or (self.gm == 3 and self.gd <= 10):
            occasion_name += " | هفته‌های مد جهانی (Fashion Weeks FW)"
            thematic_keywords.extend(["runway", "fashion week", "fw26", "catwalk", "collection"])
            target_posts_per_day = 6
        elif self.gm == 5 and 1 <= self.gd <= 7 and self.now_ir.weekday() == 0:
            occasion_name     = "مراسم مت گالا (Met Gala)"
            thematic_focus    = "تحلیل استایل‌های فرش قرمز مت گالا، طراحی‌های آوانگارد و شاهکارهای هنری"
            thematic_keywords.extend(["met gala", "red carpet", "couture", "vogue"])
            target_posts_per_day = 6
        elif self.gm == 8 and self.gd == 21:
            occasion_name     = "روز جهانی مد (World Fashion Day)"
            thematic_focus    = "گرامیداشت اصالت طراحی، تاریخچه خانه‌های مد لوکس و شاهکارهای ماندگار"
            thematic_keywords.extend(["luxury", "iconic", "designer", "vogue"])
            target_posts_per_day = 5
        elif (self.gm == 12 and self.gd >= 20) or (self.gm == 1 and self.gd <= 5):
            occasion_name += " | تعطیلات سال نو میلادی و Resort"
            thematic_keywords.extend(["holiday", "resort", "sparkle", "festive", "party"])
        elif self.gm == 2 and 10 <= self.gd <= 14:
            occasion_name     = "روز ولنتاین (Valentine's)"
            thematic_focus    = "استایل‌های رمانتیک، پالت رنگی قرمز و صورتی، لباس‌های مجلسی و اکسسوری‌های خاص"
            thematic_keywords.extend(["valentine", "red", "pink", "romantic", "gift", "ولنتاین"])

        return {
            "occasion_name":        occasion_name or "عادی (رصد روزانه بازار مد)",
            "thematic_focus":       thematic_focus or "تمرکز بر جدیدترین شومیز، شلوار، دامن و کت‌ها از برندهای معتبر",
            "thematic_keywords":    thematic_keywords,
            "target_posts_per_day": target_posts_per_day,
        }


"""
    text = text[:pos_main] + strategist_code + text[pos_main:]

with open('fashion_news_bot_final.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Phase 1 of v13.2 applied. Now let's update main() and _score_article.")
