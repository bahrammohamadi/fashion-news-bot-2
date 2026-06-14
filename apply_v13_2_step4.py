import sys

with open('fashion_news_bot_final.py', 'r', encoding='utf-8') as f:
    text = f.read()

old_find_func = """async def _find_best_candidate(
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

new_find_func = """async def _find_best_candidate(
    feeds, databases, database_id, collection_id,
    time_threshold, sdk_mode, schema, now,
    recent_titles, is_peak, cal_strategy, log_fn=print,
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
        log_fn(
            f"[feed] Core-Product-first ON: {n_core} core apparel / {n_products} total products / "
            f"{len(all_candidates)} total."
        )
        
        # Enforce News and General Throttling
        if n_products == 0:
            before = len(all_candidates)
            strict_min_score = max(MIN_NEWS_SCORE, 85)
            all_candidates = [
                c for c in all_candidates if c["score"] >= strict_min_score
            ]
            if not all_candidates:
                log_fn("[feed] Nothing strong enough to post. Skipping this run.")
                return None

            try:
                r_recent = _db_list(
                    databases, database_id, collection_id,
                    [Query.limit(20)], sdk_mode
                )
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

if old_find_func in text:
    text = text.replace(old_find_func, new_find_func)
    print("Successfully replaced old_find_func.")
else:
    print("Warning: old_find_func not matched exactly. Let's inspect.")

with open('fashion_news_bot_final.py', 'w', encoding='utf-8') as f:
    f.write(text)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Step 4 complete.")
