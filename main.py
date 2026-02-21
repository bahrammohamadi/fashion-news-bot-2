# ============================================================
# process_article()
# Standalone pipeline: URL → title_fa, summary_fa,
#                      hashtags, caption, image_urls
#
# Parallelism map:
#   ┌─────────────────────────────────────────┐
#   │  asyncio.gather()                       │
#   │    ├─ _scrape_text(url)    [executor]   │
#   │    └─ _scrape_images(url)  [executor]   │
#   │  → then: summarize (executor)           │
#   │  → then: translate title+body (gather)  │
#   └─────────────────────────────────────────┘
# ============================================================

async def process_article(
    url: str,
    title: str = "",
    description: str = "",
    category: str = "",
    sentence_count: int = SUMMARY_SENTENCES,
) -> dict:
    """
    Process a single article URL into Telegram-ready content.

    Args:
        url:            Article URL to scrape
        title:          Optional pre-known title (from RSS)
        description:    Optional pre-known description (from RSS)
        category:       Optional pre-detected category
        sentence_count: Max sentences in summary (default 8)

    Returns:
        {
            "status":     "success" | "error",
            "reason":     str (only on error/skip),
            "title_fa":   str,
            "summary_fa": str,
            "hashtags":   list[str],
            "caption":    str,
            "image_urls": list[str],
        }
    """
    loop = asyncio.get_running_loop()  # Python 3.12 safe

    # ── Step 0: Validate URL ──────────────────────────────
    if not url or not url.startswith("http"):
        return {
            "status": "error",
            "reason": "invalid_url",
            "title_fa": "",
            "summary_fa": "",
            "hashtags": [],
            "caption": "",
            "image_urls": [],
        }

    print(f"[process_article] URL: {url[:80]}")

    # ── Step 1: Parallel scrape (text + images) ───────────
    # Images function needs an rss_entry — pass empty dict as
    # safe fallback when called outside RSS context.
    empty_entry = type("Entry", (), {
        "get":     lambda self, k, d=None: d,
        "content": [],
    })()

    try:
        text_result, image_result = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(None, _scrape_text, url),
                loop.run_in_executor(
                    None, _scrape_images, url, empty_entry
                ),
                return_exceptions=True,
            ),
            timeout=SCRAPE_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print("[process_article] Scrape timed out.")
        text_result  = None
        image_result = []

    # Unwrap exceptions from gather
    scraped_text = (
        text_result
        if isinstance(text_result, str)
        else None
    )
    image_urls = (
        image_result
        if isinstance(image_result, list)
        else []
    )

    # Content selection: prefer scraped > description > title
    content = _select_content(scraped_text, description, title)

    print(
        f"[process_article] "
        f"text={'scraped' if scraped_text else 'fallback'} "
        f"({len(content)}ch) | images={len(image_urls)}"
    )

    # ── Step 1b: Minimum length pre-check ─────────────────
    if len(content) < MIN_CONTENT_CHARS:
        return {
            "status":     "error",
            "reason":     f"thin_content ({len(content)}ch < {MIN_CONTENT_CHARS})",
            "title_fa":   "",
            "summary_fa": "",
            "hashtags":   [],
            "caption":    "",
            "image_urls": image_urls,
        }

    # ── Step 2: Offline summarization ─────────────────────
    # run_in_executor keeps the event loop unblocked
    english_summary = await loop.run_in_executor(
        None,
        _extractive_summarize,
        content,
        sentence_count,
    )
    print(f"[process_article] Summary: {len(english_summary)}ch")

    # ── Step 3: Parallel translation (title + body) ────────
    # Both chunks go to MyMemory concurrently.
    # Each internally chunks and sleeps — offloaded to executor.
    effective_title = title or _extract_first_sentence(content)

    try:
        title_fa, body_fa = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(
                    None, _translate_mymemory, effective_title
                ),
                loop.run_in_executor(
                    None, _translate_mymemory, english_summary
                ),
            ),
            timeout=TRANSLATION_TIMEOUT,
        )
    except asyncio.TimeoutError:
        print("[process_article] Translation timed out — using English.")
        title_fa = effective_title
        body_fa  = english_summary

    # Fallback to English if translation returned empty
    title_fa = (title_fa or "").strip() or effective_title
    body_fa  = (body_fa  or "").strip() or english_summary

    print(
        f"[process_article] "
        f"title_fa={len(title_fa)}ch | body_fa={len(body_fa)}ch"
    )

    # ── Step 4: Hashtag extraction ─────────────────────────
    combined_for_tags = f"{title} {description} {content[:500]}"
    hashtags = _extract_hashtags_from_text(combined_for_tags)

    # ── Step 5: Category detection ─────────────────────────
    detected_category = (
        category
        or _detect_category(title, description or content[:300])
    )

    # ── Step 6: Build caption ──────────────────────────────
    caption = _build_caption(title_fa, body_fa, hashtags, detected_category)

    print(
        f"[process_article] "
        f"caption={len(caption)}ch | "
        f"hashtags={len(hashtags)} | "
        f"category={detected_category}"
    )

    return {
        "status":     "success",
        "title_fa":   title_fa,
        "summary_fa": body_fa,
        "hashtags":   hashtags,
        "caption":    caption,
        "image_urls": image_urls,
    }


# ── Helpers added for process_article() ──────────────────────

def _select_content(
    scraped_text: str | None,
    description: str,
    title: str,
) -> str:
    """
    Priority: scraped_text > description > title
    Ensures returned string is never empty.
    """
    if scraped_text and len(scraped_text) >= MIN_CONTENT_CHARS:
        return scraped_text[:MAX_SCRAPED_CHARS]
    if description and len(description) >= MIN_CONTENT_CHARS:
        return description[:MAX_RSS_CHARS]
    return title  # last resort — will fail min-length check later


def _extract_first_sentence(text: str) -> str:
    """Pull first sentence as fallback title."""
    sentences = re.split(r"(?<=[.!?])\s+", text.strip())
    return sentences[0][:DB_TITLE_MAX] if sentences else text[:DB_TITLE_MAX]


def _extract_hashtags_from_text(text: str) -> list[str]:
    """
    Same logic as _extract_hashtags() but accepts a single
    combined text string instead of title + description pair.
    """
    lower    = text.lower()
    hashtags = []
    seen     = set()
    for keyword, tags in HASHTAG_MAP.items():
        if keyword in lower and keyword not in seen:
            hashtags.append(tags)
            seen.add(keyword)
            if len(hashtags) >= MAX_HASHTAGS:
                break
    return hashtags
