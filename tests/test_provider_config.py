"""Provider credentials and nonstandard LLM options must be explicit."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import httpx

from backend.core import config, embeddings, query


class ProviderConfigurationTests(unittest.TestCase):
    def test_default_llm_request_omits_vendor_specific_thinking_option(self) -> None:
        captured: list[dict] = []

        def reply(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return httpx.Response(
                200,
                json={
                    "id": "test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "ok"},
                            "finish_reason": "stop",
                        }
                    ],
                },
            )

        real_chat = query.ChatOpenAI
        with httpx.Client(transport=httpx.MockTransport(reply)) as client:
            with (
                patch.object(config, "LLM_API_KEY", "test-key"),
                patch.object(config, "LLM_BASE_URL", "https://example.invalid/v1"),
                patch.object(config, "LLM_MODEL", "test-model"),
                patch.object(config, "LLM_DISABLE_THINKING", False),
                patch.object(config, "LLM_TIMEOUT_SECONDS", 7.0),
                patch.object(config, "LLM_MAX_RETRIES", 0),
                patch.object(query, "ChatOpenAI", side_effect=lambda **kwargs: real_chat(**kwargs, http_client=client)),
            ):
                answer = query.get_llm().invoke("hello")

        self.assertEqual(answer.content, "ok")
        self.assertEqual(len(captured), 1)
        self.assertNotIn("chat_template_kwargs", captured[0])

    def test_remote_embedding_requires_separate_provider_key_and_url(self) -> None:
        embeddings.get_embeddings.cache_clear()
        with (
            patch.object(config, "EMBEDDING_PROVIDER", "openai"),
            patch.object(config, "EMBEDDING_API_KEY", "embedding-only"),
            patch.object(config, "EMBEDDING_BASE_URL", "https://embeddings.example.invalid/v1"),
            patch.object(config, "LLM_API_KEY", "llm-secret"),
            patch("langchain_openai.OpenAIEmbeddings") as factory,
        ):
            embeddings.get_embeddings()
        kwargs = factory.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "embedding-only")
        self.assertEqual(kwargs["base_url"], "https://embeddings.example.invalid/v1")
        self.assertNotIn("llm-secret", kwargs.values())
        embeddings.get_embeddings.cache_clear()

    def test_remote_embedding_without_key_fails_closed(self) -> None:
        with patch.object(config, "EMBEDDING_API_KEY", ""):
            with self.assertRaises(RuntimeError):
                config.require_embedding_api_key()


if __name__ == "__main__":
    unittest.main()
