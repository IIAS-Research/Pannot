from __future__ import annotations

import json
import sys
import unittest
from importlib.resources import files
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
    def annotator(self, client: FakeClient) -> Annotator:
        return Annotator(client)

    def test_unique_exact_mention_is_localized_without_selection(self) -> None:
        client = FakeClient({"entities": [{"label": "NOM", "text": "Dupont"}]})

        entities = self.annotator(client).annotate("Dr Dupont.")

        self.assertEqual(entities, [Entity(3, 9, "NOM")])
        self.assertEqual(len(client.calls), 1)

    def test_system_prompt_is_composed_from_packaged_resources(self) -> None:
        local_instructions = 'Règle locale avec {"format":"{exact}"}.'
        client = FakeClient({"entities": []})

        Annotator(
            client,
            local_instructions=f"  {local_instructions}  ",
        ).annotate("Texte")

        prompt_directory = files("pannot").joinpath("prompts")

        def read_prompt(name: str) -> str:
            prompt_path = prompt_directory.joinpath(name)
            return prompt_path.read_text(encoding="utf-8").strip()

        expected = "\n\n".join(
            (
                read_prompt("extraction_fr.md"),
                read_prompt("generic_fr.md"),
                read_prompt("local_priority_fr.md") + "\n" + local_instructions,
            )
        )
        messages = client.calls[0][0]
        self.assertEqual(messages[0], {"role": "system", "content": expected})
        self.assertEqual(messages[1], {"role": "user", "content": "Texte"})

    def test_repeated_mention_is_selected_per_occurrence(self) -> None:
        client = FakeClient(
            {"entities": [{"label": "NOM", "text": "Dupont"}]},
            {"o1": "o1c1", "o2": "o2c1"},
        )

        entities = self.annotator(client).annotate("Dupont puis Dupont")

        self.assertEqual(
            entities,
            [Entity(0, 6, "NOM"), Entity(12, 18, "NOM")],
        )
        self.assertEqual(
            [call[2] for call in client.calls],
            ["entity_extraction", "entity_selection"],
        )

    def test_selection_resolves_competing_labels(self) -> None:
        client = FakeClient(
            {
                "entities": [
                    {"label": "NOM", "text": "Camille"},
                    {"label": "PRENOM", "text": "Camille"},
                ]
            },
            {"o1": "o1c2"},
        )

        entities = self.annotator(client).annotate("Camille arrive.")

        self.assertEqual(entities, [Entity(0, 7, "PRENOM")])

    def test_case_and_whitespace_variant_keeps_source_offsets(self) -> None:
        client = FakeClient(
            {"entities": [{"label": "NOM", "text": "DU PONT"}]},
            {"o1": "o1c1"},
        )

        entities = self.annotator(client).annotate("Mme Du\tpont")

        self.assertEqual(entities, [Entity(4, 11, "NOM")])

    def test_casefold_does_not_match_part_of_an_expanded_character(self) -> None:
        client = FakeClient(
            {"entities": [{"label": "NOM", "text": "s"}]},
            {"m1": None},
        )

        self.assertEqual(self.annotator(client).annotate("ß"), [])
        self.assertEqual(len(client.calls), 2)

    def test_invalid_client_response_is_retried_once_with_the_same_request(self) -> None:
        client = FakeClient(
            InvalidResponseError("invalid first response"),
            InvalidResponseError("invalid second response"),
        )

        with self.assertRaisesRegex(InvalidResponseError, "after one retry"):
            self.annotator(client).annotate("Dupont")

        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.calls[0], client.calls[1])

    def test_validation_retry_uses_packaged_prompt(self) -> None:
        client = FakeClient({"wrong": []}, {"entities": []})

        self.assertEqual(self.annotator(client).annotate("Texte"), [])

        messages = client.calls[1][0]
        retry_template = (
            files("pannot")
            .joinpath("prompts", "validation_retry_fr.md")
            .read_text(encoding="utf-8")
            .strip()
        )
        expected = retry_template.replace(
            "<<VALIDATION_ERROR>>",
            "extraction must contain only entities",
        )
        self.assertEqual(
            messages[-2],
            {"role": "assistant", "content": '{"wrong":[]}'},
        )
        self.assertEqual(messages[-1], {"role": "user", "content": expected})

    def test_absent_text_is_repaired_from_bounded_exact_candidates(self) -> None:
        client = FakeClient(
            {"entities": [{"label": "NOM", "text": "Rodiak"}]},
            {"m1": "Rodiac"},
        )

        entities = self.annotator(client).annotate("Rodiac")

        self.assertEqual(entities, [Entity(0, 6, "NOM")])
        self.assertEqual(len(client.calls), 2)
        _messages, schema, schema_name = client.calls[1]
        self.assertEqual(schema_name, "entity_text_repair")
        self.assertEqual(schema["properties"]["m1"]["enum"], ["Rodiac", None])
        repair_input = json.loads(client.calls[1][0][-1]["content"])
        self.assertEqual(
            repair_input["first_extraction"],
            {"entities": [{"label": "NOM", "text": "Rodiak"}]},
        )

    def test_absent_text_null_omits_only_that_mention(self) -> None:
        client = FakeClient(
            {
                "entities": [
                    {"label": "NOM", "text": "Dupont"},
                    {"label": "NOM", "text": "Rodiak"},
                ]
            },
            {"m1": None},
        )

        entities = self.annotator(client).annotate("Dupont et Rodiac")

        self.assertEqual(entities, [Entity(0, 6, "NOM")])
        self.assertEqual(len(client.calls), 2)

    def test_absent_text_repair_can_be_followed_by_normal_selection(self) -> None:
        client = FakeClient(
            {"entities": [{"label": "NOM", "text": "Rodiak"}]},
            {"m1": "Rodiac"},
            {"o1": "o1c1", "o2": "o2c1"},
        )

        entities = self.annotator(client).annotate("Rodiac puis Rodiac")

        self.assertEqual(
            entities,
            [Entity(0, 6, "NOM"), Entity(12, 18, "NOM")],
        )
        self.assertEqual(
            [call[2] for call in client.calls],
            ["entity_extraction", "entity_text_repair", "entity_selection"],
        )

    def test_non_response_error_is_not_retried(self) -> None:
        client = FakeClient(RuntimeError("transport failure"))

        with self.assertRaisesRegex(RuntimeError, "transport failure"):
            self.annotator(client).annotate("Dupont")

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
