import re

with open('fashion_news_bot_final.py', 'r', encoding='utf-8') as f:
    text = f.read()

# 1. Update Version Comment
text = text.replace(
    "# Version:    13.0 — Domain Architect Edition (Core Apparel + Combined Prompts + Strict Throttling)",
    "# Version:    13.1 — Master Multi-Provider Edition (GitHub Models + Multiple Keys + Full Parallel Race)"
)

# 2. Add GITHUB_MODELS
old_gemini_models = """# ── Google Gemini ──
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-pro",
]"""

new_models_config = """# ── Google Gemini ──
GEMINI_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-1.5-pro",
    "gemini-pro",
]

# ── GitHub Models (Azure AI Inference) ──
GITHUB_MODELS = [
    "gpt-4o",
    "meta-llama-3.3-70b-instruct",
    "gpt-4o-mini",
    "cohere-command-r-plus",
]
GITHUB_MAX_TOKENS  = 900
GITHUB_TEMPERATURE = 0.3"""

text = text.replace(old_gemini_models, new_models_config)

# 3. Replace Section 6 and Section 7
pos_sec6 = text.find('# SECTION 6 — AI PROVIDER VALIDATION')
pos_run3 = text.find('async def _run_three_races(')

if pos_sec6 != -1 and pos_run3 != -1:
    new_sec6_7 = """# SECTION 6 — AI PROVIDER VALIDATION (v13.1)
# ═══════════════════════════════════════════════════════════

async def _validate_groq_key(log_fn=print) -> bool:
    keys = [k for k in [os.environ.get("GROQ_API_KEY", "").strip(), os.environ.get("GROQ_API_KEY2", "").strip()] if k]
    log_fn(f"[startup] Groq available keys: {len(keys)}")
    return len(keys) > 0


async def _validate_openrouter_key(log_fn=print) -> bool:
    keys = [k for k in [os.environ.get("OPENROUTER_API_KEY", "").strip(), os.environ.get("OPENROUTER_API_KEY2", "").strip()] if k]
    log_fn(f"[startup] OpenRouter available keys: {len(keys)}")
    return len(keys) > 0


async def _validate_github_key(log_fn=print) -> bool:
    api_key = os.environ.get("GITHUB_API_KEY_4", "").strip()
    log_fn(f"[startup] GitHub Models key configured: {bool(api_key)}")
    return bool(api_key)


async def _validate_gemini_key(log_fn=print) -> bool:
    api_key = (os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GOOGLE_AI_KEY", "").strip())
    log_fn(f"[startup] Google Gemini key configured: {bool(api_key)}")
    return bool(api_key)


# ═══════════════════════════════════════════════════════════
# SECTION 7 — MASTER PARALLEL AI RACE ENGINE (v13.1)
# ═══════════════════════════════════════════════════════════

async def _call_github(session: aiohttp.ClientSession, prompt: str, log_fn=print) -> str | None:
    api_key = os.environ.get("GITHUB_API_KEY_4", "").strip()
    if not api_key:
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    
    for model in GITHUB_MODELS:
        payload = {
            "model":       model,
            "messages":    [{"role": "user", "content": prompt}],
            "temperature": GITHUB_TEMPERATURE,
            "max_tokens":  GITHUB_MAX_TOKENS,
        }
        try:
            async with session.post(
                "https://models.inference.ai.azure.com/chat/completions",
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
            ) as resp:
                body_text = await resp.text()
                if resp.status != 200:
                    log_fn(f"[race] GitHub/{model} HTTP {resp.status}: {body_text[:120]}")
                    continue
                    
                import json as _json
                data   = _json.loads(body_text)
                result = _extract_openai_content(data)
                valid  = _is_valid_persian(result)
                log_fn(f"[race] GitHub/{model}: {len(result or '')}ch | valid={valid}")
                if valid:
                    return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_fn(f"[race] GitHub/{model} error: {type(e).__name__}: {e}")
            continue
            
    return None


async def _call_groq(session: aiohttp.ClientSession, prompt: str, log_fn=print) -> str | None:
    keys = [k for k in [os.environ.get("GROQ_API_KEY", "").strip(), os.environ.get("GROQ_API_KEY2", "").strip()] if k]
    if not keys:
        return None

    for idx, api_key in enumerate(keys):
        key_name = "GROQ_API_KEY" if idx == 0 else f"GROQ_API_KEY{idx+1}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
        }
        log_fn(f"[race] Groq: trying key {key_name}...")

        for model in GROQ_MODELS:
            payload = {
                "model":       model,
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": GROQ_TEMPERATURE,
                "max_tokens":  GROQ_MAX_TOKENS,
            }
            try:
                async with session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
                ) as resp:
                    body_text = await resp.text()

                    if resp.status == 400 and "decommission" in body_text.lower():
                        continue
                    if resp.status != 200:
                        log_fn(f"[race] Groq/{model} ({key_name}) HTTP {resp.status}: {body_text[:120]}")
                        continue

                    import json as _json
                    data   = _json.loads(body_text)
                    result = _extract_openai_content(data)
                    valid  = _is_valid_persian(result)
                    log_fn(f"[race] Groq/{model} ({key_name}): {len(result or '')}ch | valid={valid}")
                    if valid:
                        return result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_fn(f"[race] Groq/{model} ({key_name}) error: {type(e).__name__}: {e}")
                continue

    return None


async def _call_openrouter(session: aiohttp.ClientSession, prompt: str, log_fn=print) -> str | None:
    keys = [k for k in [os.environ.get("OPENROUTER_API_KEY", "").strip(), os.environ.get("OPENROUTER_API_KEY2", "").strip()] if k]
    if not keys:
        return None

    for idx, api_key in enumerate(keys):
        key_name = "OPENROUTER_API_KEY" if idx == 0 else f"OPENROUTER_API_KEY{idx+1}"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  "https://t.me/irfashionnews",
            "X-Title":       "IrFashionNews",
        }
        log_fn(f"[race] OpenRouter: trying key {key_name}...")

        for model in OPENROUTER_MODELS:
            payload = {
                "model":       model,
                "messages":    [{"role": "user", "content": prompt}],
                "temperature": OPENROUTER_TEMPERATURE,
                "max_tokens":  OPENROUTER_MAX_TOKENS,
            }
            try:
                async with session.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
                ) as resp:
                    body_text = await resp.text()

                    if resp.status == 401:
                        log_fn(f"[race] OpenRouter ({key_name}): 401 invalid key — skipping this key.")
                        break  # inner loop break to try next API key
                    if resp.status == 402:
                        continue
                    if resp.status != 200:
                        log_fn(f"[race] OpenRouter/{model} ({key_name}) HTTP {resp.status}: {body_text[:120]}")
                        continue

                    import json as _json
                    data   = _json.loads(body_text)
                    result = _extract_openai_content(data)
                    valid  = _is_valid_persian(result)
                    log_fn(f"[race] OpenRouter/{model} ({key_name}): {len(result or '')}ch | valid={valid}")
                    if valid:
                        return result
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_fn(f"[race] OpenRouter/{model} ({key_name}) error: {type(e).__name__}: {e}")
                continue

    return None


async def _call_gemini(session, prompt: str, log_fn=print) -> str | None:
    api_key = (os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GOOGLE_API_KEY", "").strip() or os.environ.get("GOOGLE_AI_KEY", "").strip())
    if not api_key:
        return None

    headers = {"Content-Type": "application/json"}

    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 900}
        }
        try:
            async with session.post(
                url,
                json=payload,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=AI_PER_API_TIMEOUT),
            ) as resp:
                body_text = await resp.text()
                if resp.status != 200:
                    log_fn(f"[race] Gemini/{model} HTTP {resp.status}: {body_text[:120]}")
                    continue

                import json as _json
                data = _json.loads(body_text)
                try:
                    result = data['candidates'][0]['content']['parts'][0]['text']
                except (KeyError, IndexError):
                    continue

                valid = _is_valid_persian(result)
                log_fn(f"[race] Gemini/{model}: {len(result or '')}ch | valid={valid}")
                if valid:
                    return result
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log_fn(f"[race] Gemini/{model} error: {type(e).__name__}: {e}")
            continue

    return None


async def _parallel_ai_race(prompt: str, race_timeout: int = AI_RACE_TIMEOUT, log_fn=print) -> str | None:
    \"\"\"
    Master Multi-Provider Full Parallel Race Engine (v13.1)
    Simultaneously fires requests across GitHub Models, Google Gemini, Groq, and OpenRouter.
    The absolute first provider to return a valid Persian translation/summary wins instantly,
    and all other pending calls are cancelled.
    \"\"\"
    if not prompt or not prompt.strip():
        return None

    log_fn("[ai] 🚀 Launching master multi-provider parallel AI race (GitHub vs Gemini vs Groq vs OpenRouter)...")
    connector = aiohttp.TCPConnector(limit=16, enable_cleanup_closed=True)
    async with aiohttp.ClientSession(connector=connector) as session:
        result_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        
        providers = [
            ("GitHub",     _call_github),
            ("Gemini",     _call_gemini),
            ("Groq",       _call_groq),
            ("OpenRouter", _call_openrouter),
        ]
        total = len(providers)

        async def _worker(name: str, caller_fn):
            try:
                res = await caller_fn(session, prompt, log_fn)
                if res and _is_valid_persian(res):
                    await result_queue.put((name, res))
                else:
                    await result_queue.put((name, ""))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log_fn(f"[race] _worker({name}) error: {e}")
                await result_queue.put((name, ""))

        tasks: list[asyncio.Task] = [
            asyncio.create_task(_worker(name, fn), name=f"race_{name.lower()}")
            for name, fn in providers
        ]

        winner: str | None = None
        finished_count: int = 0

        try:
            async with asyncio.timeout(race_timeout):
                while finished_count < total:
                    name, res = await result_queue.get()
                    if res and _is_valid_persian(res):
                        winner = res
                        log_fn(f"[race] 🏆 Fully Parallel Race Winner: {name} ({len(winner)}ch)! Cancelling pending competitors.")
                        break
                    else:
                        finished_count += 1
                        log_fn(f"[race] ✗ Competitor {name} yielded no valid result ({finished_count}/{total}).")
        except TimeoutError:
            log_fn(f"[race] ✗ Master parallel AI race timed out after {race_timeout}s.")
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        return winner


"""
    text = text[:pos_sec6] + new_sec6_7 + text[pos_run3:]

# 4. Update main() startup checks
old_startup_check = """    schema, groq_ok, or_ok = await asyncio.gather(
        loop.run_in_executor(
            None, _detect_schema,
            databases, database_id, COLLECTION_ID, sdk_mode, log,
        ),
        _validate_groq_key(log),
        _validate_openrouter_key(log),
    )

    log(
        f"[{elapsed()}s] Schema={schema} | "
        f"Groq={'✓' if groq_ok else '✗'} | "
        f"OpenRouter={'✓' if or_ok else '✗'}"
    )

    if not groq_ok and not or_ok:
        error(
            "No working AI providers. "
            "Check GROQ_API_KEY and OPENROUTER_API_KEY."
        )
        return {
            "status": "error",
            "reason": "no_ai_providers",
        }"""

new_startup_check = """    schema, groq_ok, or_ok, github_ok, gemini_ok = await asyncio.gather(
        loop.run_in_executor(
            None, _detect_schema,
            databases, database_id, COLLECTION_ID, sdk_mode, log,
        ),
        _validate_groq_key(log),
        _validate_openrouter_key(log),
        _validate_github_key(log),
        _validate_gemini_key(log),
    )

    log(
        f"[{elapsed()}s] Schema={schema} | "
        f"Groq={'✓' if groq_ok else '✗'} | "
        f"OpenRouter={'✓' if or_ok else '✗'} | "
        f"GitHub={'✓' if github_ok else '✗'} | "
        f"Gemini={'✓' if gemini_ok else '✗'}"
    )

    if not any([groq_ok, or_ok, github_ok, gemini_ok]):
        error("No working AI providers found. Please verify your API keys in Appwrite.")
        return {
            "status": "error",
            "reason": "no_ai_providers",
        }"""

text = text.replace(old_startup_check, new_startup_check)

with open('fashion_news_bot_final.py', 'w', encoding='utf-8') as f:
    f.write(text)

with open('main.py', 'w', encoding='utf-8') as f:
    f.write(text)

print("Multi-provider AI race features applied successfully to both final files.")
