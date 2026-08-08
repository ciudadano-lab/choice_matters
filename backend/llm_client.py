"""
Narrator LLM call, async so a slow response from one room doesn't stall
the websocket event loop for every other room.

Uses Gemini via the Google Generative AI API.
"""

import os
import httpx
from dotenv import load_dotenv
from pathlib import Path

env_path = Path(__file__).resolve().parent / ".env"
load_dotenv(env_path)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Allow overriding the model via env var for flexibility (e.g. GEMINI_MODEL=gemini-3.5-flash)
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


async def call_narrator(system_prompt: str, user_text: str) -> str:
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "No GEMINI_API_KEY set on the server. Add it to backend/.env "
            "(see .env.example) and restart the server."
        )

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{GEMINI_URL}?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": f"{system_prompt}\n\nUser input:\n{user_text}"}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.8,
                    "maxOutputTokens": 1000,
                },
            },
        )

    if response.status_code >= 400:
        body = response.text or "<empty response>"
        if response.status_code in (401, 403):
            err = RuntimeError(f"Gemini API key was rejected ({response.status_code}). Raw response:\n{body}")
            err.raw_response = body
            raise err
        if response.status_code == 429:
            # Quota exceeded — return a deterministic fallback so the
            # application can continue operating without the LLM.
            return (
                "Fallback mode: the narrator LLM quota was exceeded. "
                "Proceeding with a deterministic fallback narrator. "
                "Next decision: perform the user's requested action using local logic."
            )
        err = RuntimeError(f"Gemini request failed ({response.status_code}). Raw response:\n{body}")
        err.raw_response = body
        raise err

    try:
        data = response.json()
    except ValueError as exc:
        err = RuntimeError(f"Gemini returned a non-JSON payload. Raw response:\n{response.text}")
        err.raw_response = response.text
        raise err from exc

    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        err = RuntimeError(f"Gemini response did not contain the expected text block. Raw response:\n{response.text}")
        err.raw_response = response.text
        raise err from exc


# --- To use Anthropic's Claude instead of DeepSeek ---------------------------
# import anthropic
# ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
# client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)
# async def call_narrator(system_prompt: str, user_text: str) -> str:
#     msg = await client.messages.create(
#         model="claude-sonnet-4-6",
#         max_tokens=1000,
#         system=system_prompt,
#         messages=[{"role": "user", "content": user_text}],
#     )
#     return "".join(block.text for block in msg.content if block.type == "text")
# -------------------------------------------------------------------------------
