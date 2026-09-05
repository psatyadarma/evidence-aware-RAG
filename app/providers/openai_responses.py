"""Minimal OpenAI Responses API transport for structured classification."""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Optional

from app.llm_answerability import (
    ProviderCallError,
    ProviderRequest,
    ProviderResponse,
)


OPENAI_RESPONSES_ENDPOINT = "https://api.openai.com/v1/responses"


class OpenAIResponsesProvider:
    """Provider adapter; no other application module knows the HTTP schema."""

    provider_name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout_seconds: float = 60.0,
        endpoint: str = OPENAI_RESPONSES_ENDPOINT,
        opener: Optional[Callable[..., Any]] = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("OpenAI API key must not be empty")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._api_key = api_key
        self.requested_model = model
        self.timeout_seconds = timeout_seconds
        self.endpoint = endpoint
        self._opener = opener or urllib.request.urlopen

    @staticmethod
    def _extract_output_text(payload: dict[str, Any]) -> str:
        texts: list[str] = []
        for item in payload.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    value = content.get("text")
                    if isinstance(value, str):
                        texts.append(value)
                elif isinstance(content, dict) and content.get("type") == "refusal":
                    raise ProviderCallError(
                        "model refused the structured classification request",
                        retryable=False,
                    )
        if not texts:
            raise ProviderCallError(
                "OpenAI response contained no output_text", retryable=True
            )
        return "".join(texts)

    def generate(self, request: ProviderRequest) -> ProviderResponse:
        body = {
            "model": self.requested_model,
            "instructions": request.system_prompt,
            "input": request.user_prompt,
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
            "store": False,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "evidence_sufficiency_decision",
                    "description": "A concise evidence-sufficiency classification.",
                    "schema": request.output_schema,
                    "strict": True,
                }
            },
        }
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=encoded,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        started = time.perf_counter()
        try:
            with self._opener(
                http_request, timeout=self.timeout_seconds
            ) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code < 600
            raise ProviderCallError(
                f"OpenAI HTTP error {exc.code}", retryable=retryable
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise ProviderCallError(
                f"OpenAI transport error: {type(exc).__name__}", retryable=True
            ) from exc
        latency_ms = (time.perf_counter() - started) * 1_000

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderCallError(
                "OpenAI returned a malformed JSON envelope", retryable=True
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderCallError(
                "OpenAI returned a non-object response", retryable=True
            )
        if payload.get("status") not in {None, "completed"}:
            raise ProviderCallError(
                f"OpenAI response status was {payload.get('status')!r}",
                retryable=True,
            )

        usage = payload.get("usage") or {}
        input_details = usage.get("input_tokens_details") or {}
        return ProviderResponse(
            output_text=self._extract_output_text(payload),
            model=str(payload.get("model") or self.requested_model),
            response_id=(str(payload["id"]) if payload.get("id") else None),
            input_tokens=int(usage.get("input_tokens") or 0),
            cached_input_tokens=int(input_details.get("cached_tokens") or 0),
            output_tokens=int(usage.get("output_tokens") or 0),
            latency_ms=latency_ms,
        )
