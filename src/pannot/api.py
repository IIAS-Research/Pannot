"""Client for the OpenAI-compatible API format used by the annotation pipeline."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

Message = Mapping[str, str]
JsonSchema = Mapping[str, Any]


class AnnotationError(Exception):
    """Base error raised for an unusable LLM annotation response."""


class InvalidResponseError(AnnotationError):
    """The LLM returned a response that cannot be used safely."""


class ChatClient(Protocol):
    """Small injectable interface required by :class:`Annotator`."""

    def complete(
        self,
        messages: Sequence[Message],
        schema: JsonSchema,
        *,
        schema_name: str,
    ) -> object:
        """Return the decoded JSON response for one structured chat completion."""


def _strict_json(content: str) -> object:
    def reject_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")

    def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(
            content,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidResponseError("the model response is not strict JSON") from exc


class OpenAIChatClient:
    """Chat Completions client for local or remote OpenAI-compatible APIs.

    The class name describes the API format, not the hosting provider. Requests
    are sent to the required ``base_url`` and do not require OpenAI-hosted
    services.

    Automatic SDK retries are deliberately disabled. Transport and provider errors
    are allowed to propagate; only invalid model responses are retried by Annotator.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        qwen_non_thinking: bool = False,
    ) -> None:
        from openai import OpenAI

        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("base_url is required")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model is required")
        if api_key is not None and (
            not isinstance(api_key, str) or not api_key.strip()
        ):
            raise ValueError("api_key must be non-empty text when provided")
        if type(qwen_non_thinking) is not bool:
            raise TypeError("qwen_non_thinking must be a bool")
        self.model = model.strip()
        self._extra_body = (
            {"chat_template_kwargs": {"enable_thinking": False}}
            if qwen_non_thinking
            else None
        )
        self._client = OpenAI(
            base_url=base_url.strip(),
            api_key=api_key if api_key is not None else "not-required",
            max_retries=0,
        )

    def complete(
        self,
        messages: Sequence[Message],
        schema: JsonSchema,
        *,
        schema_name: str,
    ) -> object:
        if not schema_name.isidentifier():
            raise ValueError("schema_name must be a valid identifier")
        request: dict[str, Any] = {
            "model": self.model,
            "messages": [dict(message) for message in messages],
            "temperature": 0,
            "max_tokens": 12_288,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": schema_name,
                    "strict": True,
                    "schema": dict(schema),
                },
            },
        }
        if self._extra_body is not None:
            request["extra_body"] = self._extra_body
        response = self._client.chat.completions.create(**request)
        if len(response.choices) != 1 or response.choices[0].finish_reason != "stop":
            raise InvalidResponseError("the model did not return one complete response")
        message = response.choices[0].message
        if getattr(message, "refusal", None):
            raise InvalidResponseError("the model refused the request")
        if not isinstance(message.content, str):
            raise InvalidResponseError("the model response has no text content")
        return _strict_json(message.content)
