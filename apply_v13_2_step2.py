import sys

with open('fashion_news_bot_final.py', 'r', encoding='utf-8') as f:
    text = f.read()

# Let's inspect where _find_best_candidate and main() are to replace them safely.
# Replace find call in Phase 1
text = text.replace(
    "is_peak=is_peak,\n                log_fn=log,",
    "is_peak=is_peak,\n                cal_strategy=cal_strategy,\n                log_fn=log,"
)

# Replace Phase 4 format call
old_prod_fmt = "_PROMPT_INTELLIGENCE_PRODUCT.format(\n                title=title[:500],\n                input_text=content[:3000],\n                source=link,\n                emoji=emoji,\n            )"
new_prod_fmt = "_PROMPT_INTELLIGENCE_PRODUCT.format(\n                title=title[:500],\n                input_text=content[:3000],\n                source=link,\n                emoji=emoji,\n                occasion=occasion_context,\n            )"
text = text.replace(old_prod_fmt, new_prod_fmt)

old_news_fmt = "_PROMPT_INTELLIGENCE_NEWS.format(\n                title=title[:500],\n                input_text=content[:3000],\n                source=link,\n                emoji=emoji,\n            )"
new_news_fmt = "_PROMPT_INTELLIGENCE_NEWS.format(\n                title=title[:500],\n                input_text=content[:3000],\n                source=link,\n                emoji=emoji,\n                occasion=occasion_context,\n            )"
text = text.replace(old_news_fmt, new_news_fmt)

old_mag_fmt = "_PROMPT_UNIFIED.format(\n            title=title[:500],\n            input_text=content[:3000],\n            category=category,\n            emoji=emoji,\n        )"
new_mag_fmt = "_PROMPT_UNIFIED.format(\n            title=title[:500],\n            input_text=content[:3000],\n            category=category,\n            emoji=emoji,\n            occasion=occasion_context,\n        )"
text = text.replace(old_mag_fmt, new_mag_fmt)

# Update _score_article def and return
old_score_def = "def _score_article(\n    candidate: dict, now: datetime, is_peak: bool = False\n) -> int:"
new_score_def = "def _score_article(\n    candidate: dict, now: datetime, is_peak: bool = False, cal_strategy: dict = None, log_fn=print\n) -> int:"
text = text.replace(old_score_def, new_score_def)

old_score_ret = "    # v12: boost tracked media sources\n    if any(media in candidate[\"feed_url\"].lower() for media in [\"voguebusiness\", \"businessoffashion\", \"wwd\", \"hypebeast\", \"highsnobiety\", \"fashionnetwork\"]):\n        score += 10\n\n    # Avoid hard cap so we can differentiate between excellent articles\n    return score"
new_score_ret = "    # v12: boost tracked media sources\n    if any(media in candidate[\"feed_url\"].lower() for media in [\"voguebusiness\", \"businessoffashion\", \"wwd\", \"hypebeast\", \"highsnobiety\", \"fashionnetwork\"]):\n        score += 10\n\n    # 3.8 Thematic Occasion Bonus (v13.2)\n    if cal_strategy and cal_strategy.get(\"thematic_keywords\"):\n        if any(tkw in combined for tkw in cal_strategy[\"thematic_keywords\"]):\n            score += 60\n\n    # Avoid hard cap so we can differentiate between excellent articles\n    return score"
text = text.replace(old_score_ret, new_score_ret)

with open('fashion_news_bot_final.py', 'w', encoding='utf-8') as f:
    f.write(text)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Formatting updates applied. Now let's update find_best_candidate and main logic.")
