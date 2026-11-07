import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import llm_client


class DummyClient:
    last_request = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, *args, **kwargs):
        type(self).last_request = kwargs
        class FakeResponse:
            status_code = 429
            text = '{"error":{"message":"quota exceeded"}}'

        return FakeResponse()


def test_call_narrator_returns_fallback_on_quota_error():
    llm_client.GROQ_API_KEY = "fake-key"

    with patch("llm_client.httpx.AsyncClient", return_value=DummyClient()):
        result = asyncio.run(llm_client.call_narrator("prompt", "take action"))

    assert "fallback mode" in result.lower()
    assert "next decision" in result.lower()


def test_parse_call_uses_low_variance_json_configuration():
    llm_client.GROQ_API_KEY = "fake-key"

    with patch("llm_client.httpx.AsyncClient", return_value=DummyClient()):
        asyncio.run(llm_client.call_narrator("prompt", "take action", purpose="parse"))

    request = DummyClient.last_request["json"]
    assert request["model"] == llm_client.GROQ_PARSE_MODEL
    assert request["temperature"] == 0.15
    assert request["max_tokens"] == 700
    assert request["response_format"] == {"type": "json_object"}
    assert request.get("reasoning_effort") == "low"


def test_narration_call_uses_creative_configuration_without_json_mode():
    llm_client.GROQ_API_KEY = "fake-key"

    with patch("llm_client.httpx.AsyncClient", return_value=DummyClient()):
        asyncio.run(llm_client.call_narrator("prompt", "narrate", purpose="narration"))

    request = DummyClient.last_request["json"]
    assert request["model"] == llm_client.GROQ_MODEL
    assert request["temperature"] == 0.8
    assert request["max_tokens"] == 900
    assert request.get("reasoning_effort") == "low"
    assert "response_format" not in request


def test_summary_call_uses_small_low_variance_configuration():
    llm_client.GROQ_API_KEY = "fake-key"

    with patch("llm_client.httpx.AsyncClient", return_value=DummyClient()):
        asyncio.run(llm_client.call_narrator("prompt", "summarize", purpose="summary"))

    request = DummyClient.last_request["json"]
    assert request["temperature"] == 0.15
    assert request["max_tokens"] == 500
    assert request.get("reasoning_effort") == "low"
    assert "response_format" not in request


def test_parse_call_retries_with_main_model_when_parse_model_is_unavailable():
    llm_client.GROQ_API_KEY = "fake-key"
    llm_client.GROQ_MODEL = "openai/gpt-oss-120b"
    llm_client.GROQ_PARSE_MODEL = "openai/gpt-oss-20b"

    class FirstParseFailure:
        status_code = 404
        text = '{"error":{"message":"The model `openai/gpt-oss-20b` does not exist or you do not have access to it."}}'

    class SecondParseSuccess:
        status_code = 200
        text = '{"choices":[{"message":{"content":"ok"}}]}'

        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    class RetryClient:
        calls = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            self.__class__.calls.append(kwargs["json"]["model"])
            if len(self.__class__.calls) == 1:
                return FirstParseFailure()
            return SecondParseSuccess()

    with patch("llm_client.httpx.AsyncClient", return_value=RetryClient()):
        result = asyncio.run(llm_client.call_narrator("prompt", "take action", purpose="parse"))

    assert result == "ok"
    assert RetryClient.calls == ["openai/gpt-oss-20b", "openai/gpt-oss-120b"]
