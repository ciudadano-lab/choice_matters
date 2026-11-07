import asyncio
import os
import httpx

from env_config import get_backend_setting

GROQ_API_KEY = get_backend_setting("GROQ_API_KEY", "")
GROQ_MODEL = get_backend_setting("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_PARSE_MODEL = get_backend_setting("GROQ_PARSE_MODEL", "openai/gpt-oss-20b")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

_CALL_CONFIG = {
    "parse": {"temperature": 0.15, "max_tokens": 700},
    "narration": {"temperature": 0.8, "max_tokens": 900},
    "summary": {"temperature": 0.15, "max_tokens": 500},
}


async def call_narrator(system_prompt: str, user_text: str, *, purpose: str = "narration") -> str:
    if not GROQ_API_KEY:
        return (
            "Fallback mode: no Groq API key was configured on the server. "
            "Proceeding with a deterministic fallback narrator. "
            "Next decision: perform the user's requested action using local logic."
        )

    config = _CALL_CONFIG.get(purpose)
    if config is None:
        raise ValueError(f"Unsupported Groq call purpose: {purpose}")

    model_candidates = [GROQ_MODEL]
    if purpose == "parse":
        model_candidates = [candidate for candidate in [GROQ_PARSE_MODEL, GROQ_MODEL] if candidate]
        model_candidates = list(dict.fromkeys(model_candidates))

    last_body = None
    last_error = None

    for model_name in model_candidates:
        request_body = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_text or "begin"},
            ],
            **config,
        }
        if "gpt-oss" in model_name:
            request_body["reasoning_effort"] = "low"
        if purpose == "parse":
            request_body["response_format"] = {"type": "json_object"}

        response = None
        async with httpx.AsyncClient(timeout=60) as client:
            for attempt in range(2):
                try:
                    response = await client.post(
                        GROQ_URL,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {GROQ_API_KEY}",
                        },
                        json=request_body,
                    )
                except httpx.TimeoutException as exc:
                    last_error = exc
                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue
                    raise RuntimeError("Groq request timed out after retry.") from exc

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue
                break

        if response is None:
            raise RuntimeError("Groq request failed without a response.") from last_error

        if response.status_code >= 400:
            body = response.text or "<empty response>"
            last_body = body
            if response.status_code in (401, 403):
                err = RuntimeError(f"Groq API key was rejected ({response.status_code}). Raw response:\n{body}")
                err.raw_response = body
                raise err
            if response.status_code == 429:
                return (
                    "Fallback mode: the narrator LLM quota was exceeded. "
                    "Proceeding with a deterministic fallback narrator. "
                    "Next decision: perform the user's requested action using local logic."
                )
            if response.status_code == 404 and model_name != model_candidates[-1]:
                continue
            err = RuntimeError(f"Groq request failed ({response.status_code}). Raw response:\n{body}")
            err.raw_response = body
            raise err

        try:
            data = response.json()
        except ValueError as exc:
            err = RuntimeError(f"Groq returned a non-JSON payload. Raw response:\n{response.text}")
            err.raw_response = response.text
            raise err from exc

        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            err = RuntimeError(f"Groq response did not contain expected text. Raw response:\n{response.text}")
            err.raw_response = response.text
            raise err from exc

    if last_body is not None:
        err = RuntimeError(f"Groq request failed for every model candidate. Raw response:\n{last_body}")
        err.raw_response = last_body
        raise err
    raise RuntimeError("Groq request failed without a response.") from last_error