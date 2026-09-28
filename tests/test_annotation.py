from __future__ import annotations

import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from pannot import Annotator, Entity, InvalidResponseError, OpenAIChatClient


class FakeClient:
    def __init__(self, *responses: object) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[object, object, str]] = []

    def complete(self, messages, schema, *, schema_name):
        self.calls.append((messages, schema, schema_name))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class AnnotationTest(unittest.TestCase):
    def test_unique_exact_mention_is_grounded_in_the_source(self) -> None:
        client = FakeClient({"entities": [{"label": "NOM", "text": "Dupont"}]})

        entities = Annotator(client).annotate("Dr Dupont.")

        self.assertEqual(entities, [Entity(3, 9, "NOM")])
        self.assertEqual(len(client.calls), 1)

    def test_invalid_client_response_is_retried_once(self) -> None:
        client = FakeClient(
            InvalidResponseError("invalid first response"),
            InvalidResponseError("invalid second response"),
        )

        with self.assertRaisesRegex(InvalidResponseError, "after one retry"):
            Annotator(client).annotate("Dupont")

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0], client.calls[1])

    def test_invalid_payload_gets_one_corrective_retry(self) -> None:
        client = FakeClient({"wrong": []}, {"entities": []})

        self.assertEqual(Annotator(client).annotate("Texte"), [])

        retry_messages = client.calls[1][0]
        self.assertEqual(retry_messages[-2]["role"], "assistant")
        self.assertEqual(retry_messages[-1]["role"], "user")

    def test_transport_error_is_not_retried(self) -> None:
        client = FakeClient(RuntimeError("transport failure"))

        with self.assertRaisesRegex(RuntimeError, "transport failure"):
            Annotator(client).annotate("Dupont")

        self.assertEqual(len(client.calls), 1)


class OpenAIClientTest(unittest.TestCase):
    def test_request_disables_sdk_retries_and_qwen_thinking(self) -> None:
        create = Mock(
            return_value=SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(content='{"entities":[]}', refusal=None),
                    )
                ]
            )
        )
        sdk = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        constructor = Mock(return_value=sdk)
        openai_module = ModuleType("openai")
        openai_module.OpenAI = constructor  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"openai": openai_module}):
            client = OpenAIChatClient(
                base_url="http://localhost:8000/v1",
                model="Qwen/Qwen3.8-27B",
                qwen_non_thinking=True,
            )
        result = client.complete(
            [{"role": "user", "content": "test"}],
            {"type": "object"},
            schema_name="test_schema",
        )

        self.assertEqual(result, {"entities": []})
        self.assertEqual(constructor.call_args.kwargs["max_retries"], 0)
        request = create.call_args.kwargs
        self.assertEqual(request["temperature"], 0)
        self.assertEqual(request["max_tokens"], 12_288)
        self.assertEqual(
            request["extra_body"],
            {"chat_template_kwargs": {"enable_thinking": False}},
        )


if __name__ == "__main__":
    unittest.main()
