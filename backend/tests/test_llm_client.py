import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_client


class DummyClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        class FakeResponse:
            status_code = 429
            text = '{"error":{"message":"quota exceeded"}}'

        return FakeResponse()


def test_call_narrator_returns_fallback_on_quota_error():
    llm_client.GEMINI_API_KEY = "fake-key"

    with patch("llm_client.httpx.AsyncClient", return_value=DummyClient()):
        result = asyncio.run(llm_client.call_narrator("prompt", "take action"))

    assert "fallback mode" in result.lower()
    assert "next decision" in result.lower()
