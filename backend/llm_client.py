import os
import httpx

from env_config import get_backend_setting

GROQ_API_KEY = get_backend_setting("GROQ_API_KEY", "")
GROQ_MODEL = get_backend_setting("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


async def call_narrator(system_prompt: str, user_text: str) -> str:
    if not GROQ_API_KEY:
        return (
            "Fallback mode: no Groq API key was configured on the server. "
            "Proceeding with a deterministic fallback narrator. "
            "Next decision: perform the user's requested action using local logic."
        )

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            GROQ_URL,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_API_KEY}",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text or "begin"},
                ],
                "temperature": 0.8,
                "max_tokens": 1000,
            },
        )

    if response.status_code >= 400:
        body = response.text or "<empty response>"
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