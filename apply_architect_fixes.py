import re

with open('fashion-news-bot-2/main.py', 'r', encoding='utf-8') as f:
    text = f.read()

print("Original length:", len(text))

# 1. Update version and header notes
text = text.replace(
    "# Version:    12.2 — Single-caption albums + Product-first",
    "# Version:    13.0 — Domain Architect Edition (Core Apparel + Combined Prompts + Strict Throttling)"
)
if "NEW IN v12.2:" in text:
    v13_notes = """# NEW IN v13.0 (Domain Architect Overhaul):
#   - Core Apparel Focus: Absolute priority for blouses (شومیز), pants (شلوار), skirts (دامن), coats/jackets (کت).
#   - Tracked Brands Expanded: Added premier fashion brands (Armani, Celine, Fendi, Hermes, Jacquemus, Max Mara, Moncler, etc.).
#   - Exquisite Combined Prompts: Single combined prompt structure to guarantee flawless translation AND high-end editorial summary.
#   - Strict General News Throttling: Enforced 12-hour throttling on general industry news to prevent channel spam.
#
# NEW IN v12.2:"""
    text = text.replace("# NEW IN v12.2:", v13_notes)

# 2. Configuration & Scoring constants
text = text.replace(
    "SCORE_FASHION_RELEVANCE = 20",
    "SCORE_FASHION_RELEVANCE = 20\nSCORE_CORE_APPAREL       = 75\nSCORE_TRACKED_BRAND_CORE = 50"
)

old_tracked_brands = """TRACKED_BRANDS = {
    "zara", "h&m", "hm", "uniqlo", "mango", "cos", "massimo dutti",
    "nike", "adidas", "puma", "new balance",
    "louis vuitton", "dior", "chanel", "gucci", "prada", "miu miu",
    "saint laurent", "loewe", "coach"
}"""
new_tracked_brands = """TRACKED_BRANDS = {
    "zara", "h&m", "hm", "uniqlo", "mango", "cos", "massimo dutti",
    "nike", "adidas", "puma", "new balance",
    "louis vuitton", "dior", "chanel", "gucci", "prada", "miu miu",
    "saint laurent", "loewe", "coach", "armani", "burberry", "celine",
    "fendi", "givenchy", "hermes", "versace", "bottega veneta", "jacquemus",
    "max mara", "moncler", "ralph lauren", "calvin klein", "tommy hilfiger",
    "stella mccartney", "balenciaga", "valentino", "off-white"
}"""
text = text.replace(old_tracked_brands, new_tracked_brands)

text = text.replace(
    'MIN_NEWS_SCORE = int(os.environ.get("MIN_NEWS_SCORE", "70"))',
    'MIN_NEWS_SCORE = int(os.environ.get("MIN_NEWS_SCORE", "85"))'
)

# 3. Replace AI Prompts in Section 2
pos_sec2 = text.find('# SECTION 2 — AI PROMPT TEMPLATES')
pos_sec3 = text.find('# SECTION 3 — SCHEMA DETECTION')
if pos_sec2 != -1 and pos_sec3 != -1:
    print("Found Section 2 and Section 3.")
    new_sec2 = """# SECTION 2 — AI PROMPT TEMPLATES (TELEGRAM OPTIMIZED)
# ═══════════════════════════════════════════════════════════

_PROMPT_UNIFIED = '''تو سردبیر خلاق ارشد، مترجم حرفه‌ای و معمار محتوای مجله دیجیتال لوکس مد به نام «مهرجامه» (@irfashionnews) در ایران هستی.
وظیفه تو تولید یک پست کامل، متمایز و بسیار جذاب تلگرامی با فرمت HTML بر اساس خبر یا معرفی محصول انگلیسی زیر است.

**تمرکز استراتژیک محصولات کانال ما (بسیار مهم):**
بیشتر محصولات و توجه کانال ما روی **شومیز (Blouses/Tops)**، **شلوار (Pants/Trousers)**، **دامن (Skirts)** و **کت (Coats/Jackets/Blazers/Suits)** از برندهای مطرح است. حتماً در خروجی خود، در صورت وجود یا ارتباط با خبر، روی این ۴ دسته محصول مانور ویژه بده و جذابیت‌های طراحی و استایل آن‌ها را توصیف کن.

**فرآیند شناختی ترجمه و خلاصه‌سازی (پرامپت ترکیبی):**
۱. **ترجمه وفادارانه و سلیس:** اصل خبر و نکات فنی را به دقیق‌ترین و روان‌ترین شکل ممکن به فارسی برگردان. از ترجمه ماشینی، تحت‌اللفظی و جملات سنگین یا گنگ به‌شدت اجتناب کن. اصطلاحات تخصصی مد را به بهترین معادل فارسی تبدیل کن یا با املای صحیح لاتین بنویس.
۲. **خلاصه‌سازی حرفه‌ای:** اصل پیام و جذاب‌ترین بخش‌های خبر یا کالکشن را گلچین و خلاصه کن تا در حوصله مخاطب شبکه اجتماعی (تلگرام) بگنجد و زیاده‌گویی نشود.

**قوانین ساختاری و ویراستاری فارسی (سخت‌گیرانه):**
- نیم‌فاصله‌ها را با دقت کامل رعایت کن (می‌شود، برندهای، طراحی‌شده، شومیزهای، کالکشن‌های، می‌پوشد).
- افعال را کامل و کتابی/رسمی بنویس (است، می‌باشد) و از لحن محاوره‌ای یا عامیانه دوری کن.
- نام برندها (مانند Zara, Dior, Chanel, Gucci) حتماً با الفبای لاتین نوشته شوند.
- از «گیومه فارسی» برای نقل‌قول‌ها استفاده کن و تمام اعداد را فارسی بنویس (مانند ۱۰، ۲۰۲۶، ۱۰۰).
- خروجی باید مستقیماً با کدهای HTML تلگرام تگ‌گذاری شده باشد (فقط تگ‌های مجاز <b> و <i>) و هیچ‌گونه تگ اضافه (مانند ```html) نداشته باشد.
- طول کل متن خروجی باید به شدت کنترل شود: **حداکثر ۸۵0 کاراکتر** (بسیار مهم تا در کپشن عکس تلگرام جا شود و قطع نشود).

💎 قالب نهایی تلگرام:
✨ <b>[تیتر کوتاه، مجلل و ضربه‌زن فارسی با یک ایموجی متناسب]</b>

[دو الی سه جمله ترجمه و خلاصه بسیار روان و جذاب از اصل خبر یا معرفی محصول]

🧥 <b>جزئیات طراحی و نوآوری:</b>
[یک پاراگراف کوتاه یا چند بولت‌پوینت مینیمال درباره متریال، برش‌ها یا نکات خاص طراحی به‌ویژه شومیز، شلوار، دامن یا کت]

💡 <b>راهنمای استایل (فرمولوژی):</b>
[یک پیشنهاد شیک و الهام‌بخش برای ست کردن این آیتم‌ها در استایل روزمره یا رسمی]

{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

خبر انگلیسی:
عنوان: {title}
محتوا: {input_text}'''

_PROMPT_INTELLIGENCE_NEWS = '''تو سردبیر خلاق ارشد، مترجم حرفه‌ای و معمار محتوای مجله لوکس مد «مهرجامه» (@irfashionnews) هستی.
ماموریت تو تبدیل متن انگلیسی خبر زیر به یک پست تلگرامی بسیار جذاب، لوکس، دقیق و متمایز به زبان فارسی است.

**تمرکز استراتژیک کانال (بسیار مهم):**
کانال ما به‌طور ویژه روی **شومیز (Blouses/Tops)**، **شلوار (Pants/Trousers)**، **دامن (Skirts)** و **کت (Coats/Jackets/Blazers/Suits)** از برندهای معتبر جهانی تمرکز دارد. 
اگر در متن این خبر یا رویداد، اشاره‌ای به این آیتم‌ها، ترندهای مرتبط با آن‌ها یا کالکشن‌های جدیدشان شده است، حتماً آن را در کانون توجه قرار بده و با لحنی تحلیلی و جذاب بازگو کن.

**پرامپت ترکیبی (ترجمه وفادارانه + خلاصه‌سازی حرفه‌ای):**
۱. **ترجمه دقیق و وفادارانه:** اصل خبر، رویدادهای تجاری/هنری، تحولات برندها و نقل‌قول‌ها را به‌صورت کاملاً دقیق ترجمه کن. به هیچ‌وجه از ترجمه تحت‌اللفظی یا جملات نامفهوم ماشینی استفاده نکن. جملات باید اقتدار و اصالت یک مجله رده‌بالای مد را بازتاب دهند.
۲. **خلاصه‌سازی و چکیده‌نویسی:** بخش‌های حاشیه‌ای و طولانی خبر را حذف کن و مغز متفکر و پیام اصلی خبر را در قالبی خلاصه، کوبنده و مناسب برای دنبال‌کنندگان کانال تلگرام ارائه بده.

**قوانین ویراستاری فارسی (سخت‌گیرانه):**
- رعایت دقیق نیم‌فاصله‌ها (می‌شود، برندهای، می‌پوشد، شرکت‌های، تحولات، ترندهای، کالکشن‌های).
- نگارش رسمی و کامل افعال (است، می‌باشد، اعلام کرد) و اجتناب از واژگان محاوره‌ای.
- درج نام برندها (مانند Zara, Dior, Chanel, Gucci) و اصطلاحات تخصصی با الفبای لاتین.
- استفاده از «گیومه فارسی» و درج اعداد به‌صورت فارسی (مانند ۱۰، ۲۰۲۶، ۵۰).
- خروجی باید مستقیماً با کدهای HTML تلگرام تگ‌گذاری شده باشد (فقط تگ‌های مجاز <b> و <i>) و هیچ‌گونه تگ اضافه (مانند ```html) نداشته باشد.

**ساختار بصری و قالب نهایی تلگرام:**
✨ <b>[تیتر خبری جذاب، کوبنده و کوتاه فارسی با یک ایموجی متناسب]</b>

[دو الی سه جمله ترجمه و خلاصه دقیق، سلیس و داستان‌گونه که پیام اصلی خبر را به زیبایی روایت می‌کند]

<b>ابعاد و تحلیل خبر:</b>
[یک پاراگراف کوتاه یا چند بولت‌پوینت تحلیلی درباره پیامدهای این خبر برای ترندها، بازار یا طراحی لباس (به‌ویژه شومیز، شلوار، دامن یا کت)]

💡 <b>چشم‌انداز استایل / ترند:</b>
[یک نکته کاربردی یا نتیجه‌گیری جالب برای علاقه‌مندان به مد و پوشاک]

<b>منبع:</b> {source}
{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

**کنترل طول متن:**
- طول متن نهایی حتماً باید **حداکثر ۸۵۰ کاراکتر** باشد تا در کپشن تصاویر تلگرام جا شود و ناقص نماند.

متن انگلیسی خبر:
عنوان: {title}
محتوا: {input_text}'''

_PROMPT_INTELLIGENCE_PRODUCT = '''تو سردبیر خلاق ارشد، مترجم حرفه‌ای و معمار محتوای مجله لوکس مد «مهرجامه» (@irfashionnews) هستی.
ماموریت تو تبدیل متن انگلیسی زیر (که درباره معرفی محصول یا کالکشن جدید یک برند است) به یک کپشن تلگرامی بسیار جذاب، دقیق، شیک و متمایز به زبان فارسی است.

**تمرکز تخصصی محصولات کانال ما (بسیار مهم):**
محصولات اصلی و تخصصی کانال ما **شومیز (Blouses/Tops)**، **شلوار (Pants/Trousers)**، **دامن (Skirts)** و **کت (Coats/Jackets/Blazers/Suits)** هستند. 
اگر در این کالکشن یا معرفی محصول، آیتم‌هایی از این ۴ دسته (شومیز، شلوار، دامن، کت) وجود دارد، حتماً تمرکز اصلی خروجی را روی آن‌ها بگذار و جزئیات طراحی، پارچه، دوخت و استایل آن‌ها را با لحنی تبلیغاتی و باوقار برجسته کن.

**پرامپت ترکیبی (ترجمه وفادارانه + خلاصه‌سازی حرفه‌ای):**
۱. **ترجمه وفادارانه و سلیس:** اطلاعات فنی، نام متریال، ویژگی‌های طراحی و نوآوری‌ها را به‌طور دقیق و وفادارانه ترجمه کن. از ترجمه تحت‌اللفظی، جملات ماشینی یا ساختارهای عجیب کاملاً بپرهیز. کلمات تخصصی مد را به بهترین و شیک‌ترین معادل فارسی تبدیل کن یا با املای صحیح لاتین بنویس.
۲. **خلاصه‌سازی و چکیده‌نویسی جذاب:** از زیاده‌گویی و حاشیه‌روی خودداری کن. اصل مطلب و جذاب‌ترین نکات کالکشن/محصول را در قالبی خلاصه، پرانرژی و مناسب برای مخاطبان شیک‌پوش کانال ارائه بده.

**قوانین ویراستاری فارسی (سخت‌گیرانه):**
- نیم‌فاصله‌ها را با دقت کامل رعایت کن (طراحی‌شده، ویژگی‌های، می‌شود، شومیزهای، برندهای، می‌پوشد، کالکشن‌های).
- افعال را کامل و کتابی/رسمی بنویس (است، می‌باشد، طراحی کرده‌اند) و از لحن محاوره‌ای یا عامیانه دوری کن.
- نام برندها (مانند Zara, H&M, Dior, Chanel, Mango) حتماً با الفبای لاتین نوشته شوند.
- از «گیومه فارسی» برای نقل‌قول‌ها استفاده کن و تمام اعداد را فارسی بنویس (مانند ۱۰، ۲۰۲۶، ۱۰۰).
- خروجی باید مستقیماً با کدهای HTML تلگرام تگ‌گذاری شده باشد (فقط تگ‌های مجاز <b> و <i>) و هیچ‌گونه تگ اضافه (مانند ```html) نداشته باشد.

**ساختار بصری و قالب نهایی تلگرام:**
✨ <b>[یک تیتر جذاب، تبلیغاتی و کوتاه فارسی با یک ایموجی متناسب]</b>

[دو الی سه جمله ترجمه و خلاصه بسیار روان و جذاب که ماهیت کالکشن، نوآوری برند و محصولات جدید را معرفی می‌کند]

<b>جزئیات طراحی و پارچه:</b>
[یک پاراگراف کوتاه یا چند بولت‌پوینت شیک درباره برش‌ها، پالت رنگی یا متریال؛ با تاکید ویژه بر شومیز، شلوار، دامن یا کت]

💡 <b>راهنمای استایل (فرمولوژی):</b>
[یک پیشنهاد الهام‌بخش و کاربردی برای ست کردن این آیتم‌ها در استایل روزمره، رسمی یا لایه‌لایه]

<b>منبع:</b> {source}
{emoji} <i>@irfashionnews | مجله زیبایی‌شناسی مد</i>

**کنترل طول متن:**
- متن نهایی حتماً باید **حداکثر ۸۵۰ کاراکتر** باشد تا در کپشن آلبوم تصاویر تلگرام به‌خوبی نمایش داده شود و قطع نشود.

متن انگلیسی خبر/محصول:
عنوان: {title}
محتوا: {input_text}'''

"""
    text = text[:pos_sec2] + new_sec2 + text[pos_sec3:]

# 4. Update _score_article
old_score_product = """    # 3. Product Launch & Brand releases boost (v12.2: much stronger)
    product_keywords = [
        "sneaker", "handbag", "it-bag", "perfume", "drop", "capsule collection",
        "collaboration", "collab", "limited edition", "watches", "sneakers",
        "fragrance", "jewelry", "bag", "accessory", "accessories",
        "launches", "unveils", "debuts", "drops", "introduces",
        "new collection", "capsule",
    ]
    if any(pkw in title_lower for pkw in product_keywords):
        score += SCORE_PRODUCT_LAUNCH"""

new_score_product = """    # 3. Product Launch & Brand releases boost (v13.0)
    product_keywords = [
        "sneaker", "handbag", "it-bag", "perfume", "drop", "capsule collection",
        "collaboration", "collab", "limited edition", "watches", "sneakers",
        "fragrance", "jewelry", "bag", "accessory", "accessories",
        "launches", "unveils", "debuts", "drops", "introduces",
        "new collection", "capsule", "lookbook", "apparel", "ready-to-wear", "rtw",
    ]
    if any(pkw in title_lower for pkw in product_keywords):
        score += SCORE_PRODUCT_LAUNCH

    # 3.5 Core Apparel Boost (v13.0 Domain Architect Enhancement: Blouses, Pants, Skirts, Coats)
    core_apparel_keywords = [
        "blouse", "blouses", "shirt", "shirts", "top", "tops", "tunic", "button-down",
        "pants", "trousers", "jeans", "slacks", "shorts", "chinos", "cargos", "leggings",
        "skirt", "skirts", "miniskirt", "midiskirt", "maxiskirt",
        "coat", "coats", "jacket", "jackets", "blazer", "blazers", "suit", "suits",
        "trench coat", "outerwear", "overcoat", "parka", "overcoats", "trench",
    ]
    if any(cak in title_lower or cak in desc_lower for cak in core_apparel_keywords):
        score += SCORE_CORE_APPAREL
        if any(b in combined for b in TRACKED_BRANDS):
            score += SCORE_TRACKED_BRAND_CORE"""

text = text.replace(old_score_product, new_score_product)

# 5. Update _is_product_launch and add _is_core_apparel
old_is_prod_fn = """def _is_product_launch(title: str, content: str) -> bool:
    \"\"\"Detect if article is about new product launch (v12).\"\"\"
    text = (title + " " + content[:300]).lower()
    product_signals = [
        "launches", "unveils", "debuts", "drops", "introduces",
        "new collection", "capsule", "limited edition",
        "sneaker", "handbag", "bag", "perfume", "fragrance",
        "collaboration", "collab"
    ]
    brand_hit = any(b in text for b in TRACKED_BRANDS)
    signal_hit = any(s in text for s in product_signals)
    return brand_hit and signal_hit"""

new_is_prod_fn = """def _is_core_apparel(title: str, content: str) -> bool:
    \"\"\"Detect if article is specifically about our core target products (v13):
    Blouses (شومیز), Pants/Trousers (شلوار), Skirts (دامن), or Coats/Jackets/Blazers (کت).\"\"\"
    text = (title + " " + content[:300]).lower()
    core_apparel_signals = [
        "blouse", "blouses", "shirt", "shirts", "top", "tops", "tunic",
        "pants", "trousers", "jeans", "slacks", "chinos", "cargos", "shorts",
        "skirt", "skirts", "miniskirt", "midiskirt", "maxiskirt",
        "coat", "coats", "jacket", "jackets", "blazer", "blazers", "suit", "suits",
        "trench coat", "outerwear", "overcoat", "parka", "trench"
    ]
    brand_hit = any(b in text for b in TRACKED_BRANDS)
    apparel_hit = any(signal in text for signal in core_apparel_signals)
    return brand_hit and apparel_hit


def _is_product_launch(title: str, content: str) -> bool:
    \"\"\"Detect if article is about new product launch (v13).\"\"\"
    text = (title + " " + content[:300]).lower()
    product_signals = [
        "launches", "unveils", "debuts", "drops", "introduces",
        "new collection", "capsule", "limited edition",
        "sneaker", "handbag", "bag", "perfume", "fragrance",
        "collaboration", "collab", "lookbook", "apparel", "ready-to-wear", "rtw"
    ]
    brand_hit = any(b in text for b in TRACKED_BRANDS)
    signal_hit = any(s in text for s in product_signals) or _is_core_apparel(title, content)
    return brand_hit and signal_hit"""

text = text.replace(old_is_prod_fn, new_is_prod_fn)

# 6. Update _find_best_candidate
old_candidate_sort = """    for c in all_candidates:
        c["score"]      = _score_article(c, now, is_peak)
        c["category"]   = _detect_category(c["title"], c["description"])
        c["is_product"] = _is_product_launch(c["title"], c["description"])

    # ── v12.2: PRODUCT-FIRST strategy ──
    # Product/brand-launch articles always rank above general news, so the
    # channel posts mostly new brand products instead of generic headlines.
    if PRODUCT_FIRST:
        all_candidates.sort(
            key=lambda x: (x["is_product"], x["score"]), reverse=True
        )
        n_products = sum(1 for c in all_candidates if c["is_product"])
        log_fn(
            f"[feed] Product-first ON: {n_products} product candidates / "
            f"{len(all_candidates)} total."
        )
        # If no product candidate at all, only allow strong general news
        if n_products == 0:
            before = len(all_candidates)
            all_candidates = [
                c for c in all_candidates if c["score"] >= MIN_NEWS_SCORE
            ]
            log_fn(
                f"[feed] No products found. General news filtered by "
                f"MIN_NEWS_SCORE={MIN_NEWS_SCORE}: {before} → {len(all_candidates)}"
            )
            if not all_candidates:
                log_fn("[feed] Nothing strong enough to post. Skipping this run.")
                return None
    else:
        all_candidates.sort(key=lambda x: x["score"], reverse=True)

    log_fn("[feed] Top 5:")
    for c in all_candidates[:5]:
        log_fn(
            f"  [{c['score']:>3}] [{c['category']:<14}] "
            f"{'🛍️' if c['is_product'] else '  '} {c['title'][:58]}"
        )"""

new_candidate_sort = """    for c in all_candidates:
        c["score"]           = _score_article(c, now, is_peak)
        c["category"]        = _detect_category(c["title"], c["description"])
        c["is_product"]      = _is_product_launch(c["title"], c["description"])
        c["is_core_apparel"] = _is_core_apparel(c["title"], c["description"])

    # ── v13.0: CORE-PRODUCT-FIRST strategy ──
    # Core apparel (Blouse, Pants, Skirt, Coat) from brands outranks other product launches,
    # which outrank general news. General news is strictly throttled.
    if PRODUCT_FIRST:
        all_candidates.sort(
            key=lambda x: (x["is_core_apparel"], x["is_product"], x["score"]), reverse=True
        )
        n_core     = sum(1 for c in all_candidates if c["is_core_apparel"])
        n_products = sum(1 for c in all_candidates if c["is_product"])
        log_fn(
            f"[feed] Core-Product-first ON: {n_core} core apparel / {n_products} total products / "
            f"{len(all_candidates)} total."
        )
        # If no product candidate at all, only allow strong general news and enforce strict news throttling
        if n_products == 0:
            before = len(all_candidates)
            strict_min_score = max(MIN_NEWS_SCORE, 85)
            all_candidates = [
                c for c in all_candidates if c["score"] >= strict_min_score
            ]
            log_fn(
                f"[feed] No products found. General news filtered by "
                f"strict_min_score={strict_min_score}: {before} → {len(all_candidates)}"
            )
            if not all_candidates:
                log_fn("[feed] Nothing strong enough to post. Skipping this run.")
                return None

            # Enforce news throttling: Do not post general news if another post was made recently
            try:
                r_recent = _db_list(
                    databases, database_id, collection_id,
                    [Query.limit(20)], sdk_mode
                )
                recent_docs = r_recent.get("documents", r_recent.get("rows", []))
                
                cutoff_any_hours  = 4   # Don't post news if any post was made in last 4 hours
                cutoff_news_hours = 12  # Don't post news if another general news was posted in last 12 hours
                
                skip_news = False
                for d in recent_docs:
                    if not d.get("posted", True): continue
                    if d.get("status") == "failed": continue
                    
                    cat = d.get("category", "")
                    cat_time = d.get("$createdAt") or d.get("pub_date") or d.get("posted_at")
                    if not cat_time: continue
                    
                    try:
                        dt = datetime.fromisoformat(cat_time.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        diff_hours = (now - dt).total_seconds() / 3600.0
                        
                        if diff_hours < cutoff_any_hours:
                            log_fn(f"[feed] Throttle: Another post was made {diff_hours:.1f}h ago. Skipping general news.")
                            skip_news = True
                            break
                        if cat not in ("brand", "runway", "morning", "poll") and diff_hours < cutoff_news_hours:
                            log_fn(f"[feed] Throttle: General news was posted {diff_hours:.1f}h ago. Skipping general news.")
                            skip_news = True
                            break
                    except Exception:
                        pass
                        
                if skip_news:
                    return None
            except Exception as e:
                log_fn(f"[feed] Throttle check warning: {e}")
    else:
        all_candidates.sort(key=lambda x: x["score"], reverse=True)

    log_fn("[feed] Top 5:")
    for c in all_candidates[:5]:
        marker = '🧥' if c.get('is_core_apparel') else ('🛍️' if c['is_product'] else '  ')
        log_fn(
            f"  [{c['score']:>3}] [{c['category']:<14}] "
            f"{marker} {c['title'][:58]}"
        )"""

text = text.replace(old_candidate_sort, new_candidate_sort)

print("New length:", len(text))

with open('fashion-news-bot-2/main.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Updates applied successfully.")
