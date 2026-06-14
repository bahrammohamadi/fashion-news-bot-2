import re

with open('fashion_news_bot_final.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update main() content strategist
old_main_strategist = """    # ═══════════════════════════════════════════════════════════
    # NEW: CONTENT STRATEGIST (Determine what to post)
    # ═══════════════════════════════════════════════════════════
    now_ir = now + timedelta(hours=3, minutes=30)
    current_hour_ir = now_ir.hour
    
    post_type = "news"
    
    # 1. Morning Greeting Check
    if 8 <= current_hour_ir <= 10:
        try:
            r_morning = _db_list(
                databases, database_id, COLLECTION_ID,
                [Query.equal("category", "morning"), Query.greater_than("$createdAt", (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")), Query.limit(1)],
                sdk_mode
            )
            if r_morning.get("total", 0) == 0:
                post_type = "morning"
        except Exception as e:
            # Fallback for missing Appwrite indices: fetch recent and filter in python
            try:
                r_fallback = _db_list(databases, database_id, COLLECTION_ID, [Query.limit(30)], sdk_mode)
                recent_docs = r_fallback.get("documents", r_fallback.get("rows", []))
                cutoff = now - timedelta(hours=12)
                has_recent_morning = False
                for d in recent_docs:
                    if d.get("category") == "morning":
                        # Simplistic time check based on $createdAt if available
                        cat_time = d.get("$createdAt")
                        if cat_time:
                            cat_dt = datetime.fromisoformat(cat_time.replace("Z", "+00:00"))
                            if cat_dt > cutoff:
                                has_recent_morning = True
                                break
                        else:
                            has_recent_morning = True # If no time, assume true to avoid spam
                if not has_recent_morning:
                    post_type = "morning"
            except Exception:
                post_type = "news"
            
    # 2. Random Poll Check (e.g. 15% chance in the evening/night)
    if post_type == "news" and 17 <= current_hour_ir <= 22 and random.random() < 0.15:
        try:
            r_poll = _db_list(
                databases, database_id, COLLECTION_ID,
                [Query.equal("category", "poll"), Query.greater_than("$createdAt", (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")), Query.limit(1)],
                sdk_mode
            )
            if r_poll.get("total", 0) == 0:
                post_type = "poll"
        except Exception as e:
            # Fallback for missing Appwrite indices: fetch recent and filter in python
            try:
                r_fallback = _db_list(databases, database_id, COLLECTION_ID, [Query.limit(50)], sdk_mode)
                recent_docs = r_fallback.get("documents", r_fallback.get("rows", []))
                cutoff = now - timedelta(days=2)
                has_recent_poll = False
                for d in recent_docs:
                    if d.get("category") == "poll":
                        cat_time = d.get("$createdAt")
                        if cat_time:
                            cat_dt = datetime.fromisoformat(cat_time.replace("Z", "+00:00"))
                            if cat_dt > cutoff:
                                has_recent_poll = True
                                break
                        else:
                            has_recent_poll = True
                if not has_recent_poll:
                    post_type = "poll"
            except Exception:
                post_type = "news""""

new_main_strategist = """    # ═══════════════════════════════════════════════════════════
    # v13.2 UNIVERSAL CONTENT STRATEGIST & ENGAGEMENT ENGINE
    # ═══════════════════════════════════════════════════════════
    strategist   = FashionCalendarStrategist(now)
    cal_strategy = strategist.get_daily_strategy()
    log(f"📅 Universal Strategist: Occasion='{cal_strategy['occasion_name']}' | Recommended Limit={cal_strategy['target_posts_per_day']} posts/day")

    occasion_context = (
        f"مناسبت و تم فصلی کنونی: {cal_strategy['occasion_name']} — {cal_strategy['thematic_focus']}"
        if cal_strategy['occasion_name'] != "عادی (رصد روزانه بازار مد)"
        else "تم روزانه: تمرکز بر جدیدترین شومیز، شلوار، دامن و کت‌ها از برندهای معتبر"
    )

    now_ir          = now + timedelta(hours=3, minutes=30)
    current_hour_ir = now_ir.hour
    
    post_type = "news"
    
    # 1. Morning Greeting Check (8-10 AM IRT)
    if 8 <= current_hour_ir <= 10:
        try:
            r_morning = _db_list(
                databases, database_id, COLLECTION_ID,
                [Query.equal("category", "morning"), Query.greater_than("$createdAt", (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")), Query.limit(1)],
                sdk_mode
            )
            if r_morning.get("total", 0) == 0:
                post_type = "morning"
        except Exception:
            try:
                r_fallback = _db_list(databases, database_id, COLLECTION_ID, [Query.limit(30)], sdk_mode)
                recent_docs = r_fallback.get("documents", r_fallback.get("rows", []))
                cutoff = now - timedelta(hours=12)
                has_recent_morning = False
                for d in recent_docs:
                    if d.get("category") == "morning":
                        cat_time = d.get("$createdAt") or d.get("pub_date") or d.get("posted_at")
                        if cat_time:
                            cat_dt = datetime.fromisoformat(cat_time.replace("Z", "+00:00"))
                            if cat_dt > cutoff:
                                has_recent_morning = True
                                break
                        else:
                            has_recent_morning = True
                if not has_recent_morning:
                    post_type = "morning"
            except Exception:
                pass
            
    # 2. Universal Poll / Quiz Engagement Check ("روزانه یا چند روز یکبار")
    # Tries to post if no poll/quiz was posted in the last 28 hours, or with a 25% chance in active hours (14-22)
    if post_type == "news" and 14 <= current_hour_ir <= 22:
        try:
            r_poll = _db_list(
                databases, database_id, COLLECTION_ID,
                [Query.equal("category", "poll"), Query.greater_than("$createdAt", (now - timedelta(hours=28)).strftime("%Y-%m-%dT%H:%M:%S.000+00:00")), Query.limit(1)],
                sdk_mode
            )
            if r_poll.get("total", 0) == 0 or random.random() < 0.25:
                post_type = "poll"
        except Exception:
            try:
                r_fallback = _db_list(databases, database_id, COLLECTION_ID, [Query.limit(50)], sdk_mode)
                recent_docs = r_fallback.get("documents", r_fallback.get("rows", []))
                cutoff = now - timedelta(hours=28)
                has_recent_poll = False
                for d in recent_docs:
                    if d.get("category") == "poll":
                        cat_time = d.get("$createdAt") or d.get("pub_date") or d.get("posted_at")
                        if cat_time:
                            cat_dt = datetime.fromisoformat(cat_time.replace("Z", "+00:00"))
                            if cat_dt > cutoff:
                                has_recent_poll = True
                                break
                        else:
                            has_recent_poll = True
                if not has_recent_poll or random.random() < 0.25:
                    post_type = "poll"
            except Exception:
                pass"""

if old_main_strategist in text:
    text = text.replace(old_main_strategist, new_main_strategist)
else:
    print("Warning: old_main_strategist not matched exactly.")

# 2. Update Poll handling block
old_poll_handler = """    elif post_type == "poll":
        poll_raw = await _parallel_ai_race(_PROMPT_POLL_GENERATOR, AI_RACE_TIMEOUT, log)
        if poll_raw:
            try:
                import json
                poll_text = poll_raw.strip()
                if poll_text.startswith("```"):
                    poll_text = re.sub(r"^```(?:json)?\s*", "", poll_text, flags=re.IGNORECASE)
                    poll_text = re.sub(r"\s*```$", "", poll_text)
                poll_data = json.loads(poll_text)
                q = poll_data.get("question", "کدام استایل را ترجیح می‌دهید؟")
                opts = poll_data.get("options", ["کلاسیک", "مدرن"])[:10] # Max 10 options in Telegram
                
                await bot.send_poll(chat_id=chat_id, question=q, options=opts)
                payload = {
                    "link": f"poll://{int(now.timestamp())}",
                    "title": q[:250],
                    "category": "poll",
                }
                if schema.has_posted: payload["posted"] = True
                if schema.has_status: payload["status"] = STATUS_POSTED
                _db_create(databases, database_id, COLLECTION_ID, payload, sdk_mode)
                log(f"[{elapsed()}s] 📊 Poll sent successfully.")
                return {"status": "success", "type": "poll"}
            except Exception as e:
                log(f"[{elapsed()}s] Poll post failed: {e}")
        post_type = "news" # Fallback to news if poll fails"""

new_poll_handler = """    elif post_type == "poll":
        poll_prompt = _PROMPT_POLL_GENERATOR.format(occasion=occasion_context)
        poll_raw    = await _parallel_ai_race(poll_prompt, AI_RACE_TIMEOUT, log)
        if poll_raw:
            try:
                import json
                poll_text = poll_raw.strip()
                if poll_text.startswith("```"):
                    poll_text = re.sub(r"^```(?:json)?\s*", "", poll_text, flags=re.IGNORECASE)
                    poll_text = re.sub(r"\s*```$", "", poll_text)
                poll_data = json.loads(poll_text)
                
                poll_type = poll_data.get("type", "regular").lower()
                q         = poll_data.get("question", "در یک قرار کاری مهم، کدام آیتم را ترجیح می‌دهید؟")[:300]
                opts      = poll_data.get("options", ["ترنچ کت کلاسیک کرم", "مانتو کتی ساختاریافته", "کت و شلوار اورسایز"])[:10]
                
                correct_id  = None
                explanation = None
                if poll_type == "quiz":
                    correct_id  = poll_data.get("correct_option_id")
                    explanation = poll_data.get("explanation")
                    if correct_id is None or not isinstance(correct_id, int) or correct_id >= len(opts):
                        poll_type = "regular"

                if poll_type == "quiz":
                    await bot.send_poll(
                        chat_id=chat_id,
                        question=q,
                        options=opts,
                        type="quiz",
                        correct_option_id=correct_id,
                        explanation=explanation[:200] if explanation else None,
                    )
                else:
                    await bot.send_poll(
                        chat_id=chat_id,
                        question=q,
                        options=opts,
                        type="regular",
                    )
                
                payload = {
                    "link":     f"poll://{int(now.timestamp())}",
                    "title":    q[:250],
                    "category": "poll",
                }
                if schema.has_posted: payload["posted"] = True
                if schema.has_status: payload["status"] = STATUS_POSTED
                _db_create(databases, database_id, COLLECTION_ID, payload, sdk_mode)
                log(f"[{elapsed()}s] 📊 Poll/Quiz sent successfully: {q[:65]}")
                return {"status": "success", "type": "poll"}
            except Exception as e:
                log(f"[{elapsed()}s] Poll/Quiz post failed: {e}")
        post_type = "news" # Fallback to news if poll fails"""

if old_poll_handler in text:
    text = text.replace(old_poll_handler, new_poll_handler)
else:
    print("Warning: old_poll_handler not matched exactly.")

# 3. Update _find_best_candidate call
old_find_call = """            _find_best_candidate(
                feeds=RSS_FEEDS,
                databases=databases,
                database_id=database_id,
                collection_id=COLLECTION_ID,
                time_threshold=time_threshold,
                sdk_mode=sdk_mode,
                schema=schema,
                now=now,
                recent_titles=recent_titles,
                is_peak=is_peak,
                log_fn=log,
            ),"""

new_find_call = """            _find_best_candidate(
                feeds=RSS_FEEDS,
                databases=databases,
                database_id=database_id,
                collection_id=COLLECTION_ID,
                time_threshold=time_threshold,
                sdk_mode=sdk_mode,
                schema=schema,
                now=now,
                recent_titles=recent_titles,
                is_peak=is_peak,
                cal_strategy=cal_strategy,
                log_fn=log,
            ),"""

if old_find_call in text:
    text = text.replace(old_find_call, new_find_call)
else:
    print("Warning: old_find_call not matched.")

# 4. Update Phase 4 prompt formatting
old_phase4 = """    # v12: Intelligence Agent mode
    if PROMPT_MODE == "intelligence":
        is_product = _is_product_launch(title, content)
        if is_product:
            prompt = _PROMPT_INTELLIGENCE_PRODUCT.format(
                title=title[:500],
                input_text=content[:3000],
                source=link,
                emoji=emoji,
            )
            log(f"[{elapsed()}s] Using PRODUCT prompt")
        else:
            prompt = _PROMPT_INTELLIGENCE_NEWS.format(
                title=title[:500],
                input_text=content[:3000],
                source=link,
                emoji=emoji,
            )
            log(f"[{elapsed()}s] Using NEWS prompt")
    else:
        # legacy magazine mode
        prompt = _PROMPT_UNIFIED.format(
            title=title[:500],
            input_text=content[:3000],
            category=category,
            emoji=emoji,
        )"""

new_phase4 = """    # v13.2: Universal Thematic & Engagement Agent mode
    if PROMPT_MODE == "intelligence":
        is_product = _is_product_launch(title, content)
        if is_product:
            prompt = _PROMPT_INTELLIGENCE_PRODUCT.format(
                title=title[:500],
                input_text=content[:3000],
                source=link,
                emoji=emoji,
                occasion=occasion_context,
            )
            log(f"[{elapsed()}s] Using PRODUCT prompt")
        else:
            prompt = _PROMPT_INTELLIGENCE_NEWS.format(
                title=title[:500],
                input_text=content[:3000],
                source=link,
                emoji=emoji,
                occasion=occasion_context,
            )
            log(f"[{elapsed()}s] Using NEWS prompt")
    else:
        # legacy magazine mode
        prompt = _PROMPT_UNIFIED.format(
            title=title[:500],
            input_text=content[:3000],
            category=category,
            emoji=emoji,
            occasion=occasion_context,
        )"""

if old_phase4 in text:
    text = text.replace(old_phase4, new_phase4)
else:
    print("Warning: old_phase4 not matched.")

# 5. Update _find_best_candidate definition and logic
old_find_def = """async def _find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, schema, now,
    recent_titles, is_peak, log_fn=print,
):
    loop  = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(
            None, _fetch_feed, url, time_threshold, log_fn
        )
        for url in feeds
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_candidates = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log_fn(f"[feed] Error ({feeds[i][:45]}): {result}")
            continue
        if result:
            all_candidates.extend(result)

    log_fn(f"[feed] {len(all_candidates)} articles collected.")
    if not all_candidates:
        return None

    for c in all_candidates:
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
        all_candidates.sort(key=lambda x: x["score"], reverse=True)"""

new_find_def = """async def _find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, schema, now,
    recent_titles, is_peak, cal_strategy, log_fn=print,
):
    loop  = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(None, _fetch_feed, url, time_threshold, log_fn)
        for url in feeds
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_candidates = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            continue
        if result:
            all_candidates.extend(result)

    log_fn(f"[feed] {len(all_candidates)} articles collected.")
    if not all_candidates:
        return None

    for c in all_candidates:
        c["score"]           = _score_article(c, now, is_peak, cal_strategy, log_fn)
        c["category"]        = _detect_category(c["title"], c["description"])
        c["is_product"]      = _is_product_launch(c["title"], c["description"])
        c["is_core_apparel"] = _is_core_apparel(c["title"], c["description"])

    # ── v13.2: CORE-PRODUCT-FIRST strategy ──
    # Core apparel (Blouse, Pants, Skirt, Coat) from brands outranks other products,
    # which outrank general news. Enforces dynamic post throttling based on seasonal strategy.
    if PRODUCT_FIRST:
        all_candidates.sort(
            key=lambda x: (x["is_core_apparel"], x["is_product"], x["score"]), reverse=True
        )
        n_core     = sum(1 for c in all_candidates if c["is_core_apparel"])
        n_products = sum(1 for c in all_candidates if c["is_product"])
        log_fn(f"[feed] Core-Product-first ON: {n_core} core apparel / {n_products} total products / {len(all_candidates)} total.")
        
        # If no product candidate at all, only allow strong general news and enforce strict news throttling
        if n_products == 0:
            before = len(all_candidates)
            strict_min_score = max(MIN_NEWS_SCORE, 85)
            all_candidates = [c for c in all_candidates if c["score"] >= strict_min_score]
            if not all_candidates:
                log_fn("[feed] Nothing strong enough to post. Skipping this run.")
                return None

            try:
                r_recent = _db_list(databases, database_id, collection_id, [Query.limit(20)], sdk_mode)
                recent_docs = r_recent.get("documents", r_recent.get("rows", []))
                
                # Dynamic post throttling based on recommended calendar daily limit (3-6 posts daily)
                target_daily      = cal_strategy.get("target_posts_per_day", 4)
                cutoff_any_hours  = max(2.5, 24.0 / target_daily)
                cutoff_news_hours = 12  # Strict 12 hour throttling on general news
                
                skip_news = False
                for d in recent_docs:
                    if not d.get("posted", True) or d.get("status") == "failed":
                        continue
                    
                    cat      = d.get("category", "")
                    cat_time = d.get("$createdAt") or d.get("pub_date") or d.get("posted_at")
                    if not cat_time: continue
                    
                    try:
                        dt = datetime.fromisoformat(cat_time.replace("Z", "+00:00"))
                        if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
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
        all_candidates.sort(key=lambda x: x["score"], reverse=True)"""

if old_find_def in text:
    text = text.replace(old_find_def, new_find_def)
else:
    print("Warning: old_find_def not matched.")

# 6. Update _score_article definition and add thematic bonus
old_score_def = """def _score_article(
    candidate: dict, now: datetime, is_peak: bool = False
) -> int:"""

new_score_def = """def _score_article(
    candidate: dict, now: datetime, is_peak: bool = False, cal_strategy: dict = None, log_fn=print
) -> int:"""

if old_score_def in text:
    text = text.replace(old_score_def, new_score_def)
else:
    print("Warning: old_score_def not matched.")

old_score_return = """    # v12: boost tracked media sources
    if any(media in candidate["feed_url"].lower() for media in ["voguebusiness", "businessoffashion", "wwd", "hypebeast", "highsnobiety", "fashionnetwork"]):
        score += 10

    # Avoid hard cap so we can differentiate between excellent articles
    return score"""

new_score_return = """    # v12: boost tracked media sources
    if any(media in candidate["feed_url"].lower() for media in ["voguebusiness", "businessoffashion", "wwd", "hypebeast", "highsnobiety", "fashionnetwork"]):
        score += 10

    # 3.8 Thematic Occasion Bonus (v13.2)
    if cal_strategy and cal_strategy.get("thematic_keywords"):
        if any(tkw in combined for tkw in cal_strategy["thematic_keywords"]):
            score += 60
            log_fn(f"[score] 🌸 Thematic match bonus (+60) applied for {candidate['title'][:40]}")

    # Avoid hard cap so we can differentiate between excellent articles
    return score"""

if old_score_return in text:
    text = text.replace(old_score_return, new_score_return)
else:
    print("Warning: old_score_return not matched.")

with open('fashion_news_bot_final.py', 'w', encoding='utf-8') as f:
    f.write(text)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("v13.2 fully applied to both final files.")
