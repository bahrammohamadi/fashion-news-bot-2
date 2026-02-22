# ═══════════════════════════════════════════════════════════
# SECTION 6 — PARALLEL SUMMARIZE + TRANSLATE ENGINE
#
# DESIGN: "First-response-wins" race
#
#   All APIs are called simultaneously at t=0.
#   The first to return a valid Persian response wins.
#   All other in-flight coroutines are cancelled immediately.
#   No API can delay the pipeline once a winner is found.
#
#   API PRIORITY (by typical latency, fastest first):
#   1. Groq          — LLM, ~1-3s, highest quality
#   2. OpenRouter    — LLM, ~2-5s, good quality
#   3. Google AI     — LLM, ~2-6s, good quality
#   4. MyMemory      — MT,  ~3-8s, last resort
#
#   VALIDITY CHECKS (applied to every response):
#   - Not None / not empty string
#   - Length >= MIN_PERSIAN_CHARS
#   - Contains at least one Persian Unicode character
#   - Does not contain API error markers
#
#   CANCELLATION:
#   asyncio.Task.cancel() is called on all losing tasks.
#   Winner is extracted the moment its future resolves.
#   No sequential waiting — pure concurrent race.
# ═══════════════════════════════════════════════════════════


# ── Master prompt template ───────────────────────────────
_SUMMARIZE_TRANSLATE_PROMPT = """\
You are a professional Persian (Farsi) fashion journalist.

Your task:
1. Read the following English fashion article text carefully.
2. Write a SHORT, NATURAL Persian summary (5-7 sentences maximum).
3. The summary must read like editorial magazine writing — fluid, \
engaging, human. Not bullet points. Not literal translation.
4. Cover only the most important facts: what happened, who is involved, \
why it matters for fashion.
5. Output ONLY the Persian summary text. No English. No preamble. \
No explanation. No markdown.

Article text:
\"\"\"
{text}
\"\"\"

Persian summary:"""


def _is_valid_persian(text: str | None) -> bool:
    """
    Returns True only if the text is a genuine Persian response.
    Rejects: None, empty, too short, no Persian chars, error markers.
    """
    if not text or not isinstance(text, str):
        return False
    stripped = text.strip()
    if len(stripped) < MIN_PERSIAN_CHARS:
        return False
    # Must contain at least one Persian/Arabic Unicode character
    # Persian range: U+0600–U+06FF, U+FB50–U+FDFF, U+FE70–U+FEFF
    has_persian = any(
        "\u0600" <= ch <= "\u06ff"
        or "\ufb50" <= ch <= "\ufdff"
        or "\ufe70" <= ch <= "\ufeff"
        for ch in stripped
    )
    if not has_persian:
        return False
    # Reject known API error strings
    error_markers = [
        "error", "invalid", "unauthorized", "rate limit",
        "quota", "too many", "unavailable", "bad request",
        "model not found", "context length",
    ]
    lower = stripped.lower()
    if any(m in lower for m in error_markers):
        return False
    return True


async def _call_groq(
    session: "aiohttp.ClientSession",
    text: str,
) -> str | None:
    """
    Groq LLM API — typically fastest responder.
    Uses llama3-70b for high-quality Persian output.
    """
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        print("[parallel] Groq: no API key — skipping.")
        return None

    prompt = _SUMMARIZE_TRANSLATE_PROMPT.format(text=text[:3000])
    payload = {
        "model":       GROQ_MODEL,
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens":  600,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    try:
        async with session.post(
            "https://api.groq.com/openai/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=TRANSLATION_API_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                print(f"[parallel] Groq HTTP {resp.status}: {body[:120]}")
                return None
            data   = await resp.json()
            result = (
                data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                or ""
            ).strip()
            print(f"[parallel] Groq responded: {len(result)}ch")
            return result if _is_valid_persian(result) else None

    except asyncio.CancelledError:
        # Task was cancelled because another API won the race
        print("[parallel] Groq: cancelled (another API won).")
        raise
    except Exception as e:
        print(f"[parallel] Groq error: {e}")
        return None


async def _call_openrouter(
    session: "aiohttp.ClientSession",
    text: str,
) -> str | None:
    """
    OpenRouter LLM API — broad model selection, good fallback.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("[parallel] OpenRouter: no API key — skipping.")
        return None

    prompt = _SUMMARIZE_TRANSLATE_PROMPT.format(text=text[:3000])
    payload = {
        "model":       OPENROUTER_MODEL,
        "messages":    [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens":  600,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://irfashionnews.com",
        "X-Title":       "IrFashionNews",
    }
    try:
        async with session.post(
            "https://openrouter.ai/api/v1/chat/completions",
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=TRANSLATION_API_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                print(f"[parallel] OpenRouter HTTP {resp.status}: {body[:120]}")
                return None
            data   = await resp.json()
            result = (
                data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                or ""
            ).strip()
            print(f"[parallel] OpenRouter responded: {len(result)}ch")
            return result if _is_valid_persian(result) else None

    except asyncio.CancelledError:
        print("[parallel] OpenRouter: cancelled (another API won).")
        raise
    except Exception as e:
        print(f"[parallel] OpenRouter error: {e}")
        return None


async def _call_google_ai(
    session: "aiohttp.ClientSession",
    text: str,
) -> str | None:
    """
    Google Gemini API — reliable, good Persian quality.
    """
    api_key = os.environ.get("GOOGLE_AI_KEY", "")
    if not api_key:
        print("[parallel] Google AI: no API key — skipping.")
        return None

    prompt = _SUMMARIZE_TRANSLATE_PROMPT.format(text=text[:3000])
    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }],
        "generationConfig": {
            "temperature":    0.3,
            "maxOutputTokens": 600,
        },
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GOOGLE_AI_MODEL}:generateContent?key={api_key}"
    )
    try:
        async with session.post(
            url,
            json=payload,
            timeout=aiohttp.ClientTimeout(total=TRANSLATION_API_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                print(f"[parallel] Google AI HTTP {resp.status}: {body[:120]}")
                return None
            data   = await resp.json()
            result = (
                data.get("candidates", [{}])[0]
                    .get("content", {})
                    .get("parts", [{}])[0]
                    .get("text", "")
                or ""
            ).strip()
            print(f"[parallel] Google AI responded: {len(result)}ch")
            return result if _is_valid_persian(result) else None

    except asyncio.CancelledError:
        print("[parallel] Google AI: cancelled (another API won).")
        raise
    except Exception as e:
        print(f"[parallel] Google AI error: {e}")
        return None


async def _call_mymemory_async(
    session: "aiohttp.ClientSession",
    text: str,
) -> str | None:
    """
    MyMemory machine translation — free, no LLM, last resort.
    Summarizes by taking the first 450 chars before translating.
    This is intentionally the lowest-priority fallback.
    """
    if not text or not text.strip():
        return None

    # Trim to a single meaningful chunk for speed in race mode
    chunk = text.strip()[:MYMEMORY_CHUNK_SIZE]

    params: dict = {
        "q":        chunk,
        "langpair": "en|fa",
    }
    if MYMEMORY_EMAIL:
        params["de"] = MYMEMORY_EMAIL

    try:
        async with session.get(
            "https://api.mymemory.translated.net/get",
            params=params,
            timeout=aiohttp.ClientTimeout(total=TRANSLATION_API_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                return None
            data   = await resp.json()
            result = (
                data.get("responseData", {})
                    .get("translatedText", "")
                or ""
            ).strip()

            if data.get("quotaFinished"):
                print("[parallel] MyMemory quota finished.")
                return None

            error_flags = [
                "MYMEMORY WARNING",
                "YOU USED ALL AVAILABLE",
            ]
            if any(f in result for f in error_flags):
                return None

            print(f"[parallel] MyMemory responded: {len(result)}ch")
            return result if _is_valid_persian(result) else None

    except asyncio.CancelledError:
        print("[parallel] MyMemory: cancelled (another API won).")
        raise
    except Exception as e:
        print(f"[parallel] MyMemory error: {e}")
        return None


async def parallel_summarize_translate(text: str) -> str | None:
    """
    ════════════════════════════════════════════════════════
    FIRST-RESPONSE-WINS PARALLEL RACE ENGINE

    Fires all API calls simultaneously at t=0.
    Returns the first valid Persian result received.
    Cancels all other in-flight tasks immediately on win.

    Args:
        text: English article text (plain, no HTML)

    Returns:
        Persian summary string, or None if ALL APIs fail.

    Concurrency model:
        asyncio.Task per API + asyncio.Queue for results.
        Worker tasks push valid results to queue.
        Collector awaits first queue item then cancels all.

    Timeline example:
        t=0.0s  → all 4 API calls fire
        t=1.8s  → Groq responds with valid Persian
        t=1.8s  → OpenRouter, Google AI, MyMemory cancelled
        t=1.8s  → result returned to pipeline
    ════════════════════════════════════════════════════════
    """
    import aiohttp  # local import — not needed elsewhere

    if not text or not text.strip():
        print("[parallel] Empty input — skipping race.")
        return None

    # Result queue — first valid result placed here wins
    result_queue: asyncio.Queue[str | None] = asyncio.Queue()
    active_tasks: list[asyncio.Task] = []

    async def _race_worker(
        name: str,
        coro_fn,            # async callable(session, text) -> str|None
        session: aiohttp.ClientSession,
    ):
        """
        Wrapper: calls one API, validates result,
        pushes to queue if valid, sentinel None if not.
        CancelledError is re-raised cleanly.
        """
        try:
            result = await coro_fn(session, text)
            if _is_valid_persian(result):
                print(f"[parallel] ✓ {name} pushed valid result.")
                await result_queue.put(result)
            else:
                print(f"[parallel] ✗ {name} result invalid — no push.")
                await result_queue.put(None)
        except asyncio.CancelledError:
            # Normal cancellation — do not push sentinel
            raise
        except Exception as e:
            print(f"[parallel] {name} worker error: {e}")
            await result_queue.put(None)

    # Map of API name → coroutine function
    # Order here does NOT affect which wins — all fire at t=0
    API_REGISTRY = [
        ("Groq",        _call_groq),
        ("OpenRouter",  _call_openrouter),
        ("Google AI",   _call_google_ai),
        ("MyMemory",    _call_mymemory_async),
    ]

    # Count only APIs that have keys configured
    # (workers with no key return None immediately — still counted)
    total_workers = len(API_REGISTRY)

    connector = aiohttp.TCPConnector(limit=10, ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:

        # ── Fire ALL tasks simultaneously ─────────────────
        for name, coro_fn in API_REGISTRY:
            task = asyncio.create_task(
                _race_worker(name, coro_fn, session),
                name=f"translation_{name}",
            )
            active_tasks.append(task)

        print(
            f"[parallel] Race started: {total_workers} APIs "
            f"fired simultaneously at t=0."
        )

        # ── Collect results until first VALID win ─────────
        winner: str | None = None
        none_count          = 0  # tracks failed/invalid responses

        try:
            async with asyncio.timeout(TRANSLATION_RACE_TIMEOUT):
                while none_count < total_workers:
                    result = await result_queue.get()

                    if _is_valid_persian(result):
                        winner = result
                        print(
                            f"[parallel] ★ Winner found "
                            f"({len(winner)}ch). "
                            f"Cancelling remaining tasks."
                        )
                        break
                    else:
                        none_count += 1
                        print(
                            f"[parallel] Invalid result received "
                            f"({none_count}/{total_workers} failed)."
                        )

        except TimeoutError:
            print(
                f"[parallel] Race timeout after "
                f"{TRANSLATION_RACE_TIMEOUT}s."
            )

        finally:
            # ── Cancel ALL remaining in-flight tasks ──────
            cancelled_count = 0
            for task in active_tasks:
                if not task.done():
                    task.cancel()
                    cancelled_count += 1
            if cancelled_count:
                # Allow cancellations to propagate cleanly
                await asyncio.gather(*active_tasks, return_exceptions=True)
                print(
                    f"[parallel] {cancelled_count} task(s) "
                    f"cancelled cleanly."
                )

    return winner
