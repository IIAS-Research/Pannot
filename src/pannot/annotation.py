"""LLM extraction with deterministic grounding to source-text offsets."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass

from .api import ChatClient, InvalidResponseError, JsonSchema, Message

LABELS = (
    "NOM",
    "PRENOM",
    "DATE",
    "DATE_NAISSANCE",
    "IPP",
    "NDA",
    "SECU",
    "TEL",
    "MAIL",
    "ADRESSE",
    "VILLE",
    "ZIP",
    "HOPITAL",
)
_LABEL_SET = frozenset(LABELS)
_MAX_ENTITIES = 2_000

EXTRACTION_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["entities"],
    "properties": {
        "entities": {
            "type": "array",
            "maxItems": _MAX_ENTITIES,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["label", "text"],
                "properties": {
                    "label": {"type": "string", "enum": list(LABELS)},
                    "text": {"type": "string", "minLength": 1},
                },
            },
        }
    },
}

_EXTRACTION_PROMPT = (
    "Annote les entités demandées dans le document. Retourne uniquement un objet "
    'JSON {"entities":[{"label":"NOM","text":"passage exact"}]}. Copie chaque '
    "mention exactement depuis le document."
)
_VALIDATION_RETRY_PROMPT = (
    "La réponse précédente est invalide : {detail}. Régénère entièrement "
    "l'objet JSON demandé."
)


@dataclass(frozen=True, slots=True, order=True)
class Entity:
    """A half-open entity span in the unchanged source text."""

    start: int
    end: int
    label: str

    def __post_init__(self) -> None:
        if (
            type(self.start) is not int
            or type(self.end) is not int
            or self.start < 0
            or self.end <= self.start
        ):
            raise ValueError("entity offsets must define a non-empty half-open span")
        if self.label not in _LABEL_SET:
            raise ValueError(f"unknown entity label: {self.label!r}")


@dataclass(frozen=True, slots=True)
class _Mention:
    label: str
    text: str


def _is_word(character: str) -> bool:
    return bool(re.match(r"\w", character, flags=re.UNICODE))


def _exact_positions(source: str, value: str) -> set[tuple[int, int]]:
    left = r"(?<!\w)" if _is_word(value[0]) else ""
    right = r"(?!\w)" if _is_word(value[-1]) else ""
    pattern = re.compile(r"(?=(" + left + re.escape(value) + right + r"))")
    return {match.span(1) for match in pattern.finditer(source)}


def _mentions(payload: object) -> list[_Mention]:
    if not isinstance(payload, dict) or set(payload) != {"entities"}:
        raise InvalidResponseError("extraction must contain only entities")
    rows = payload["entities"]
    if not isinstance(rows, list) or len(rows) > _MAX_ENTITIES:
        raise InvalidResponseError("entities must be a bounded array")
    result: list[_Mention] = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"label", "text"}
            or not isinstance(row["label"], str)
            or row["label"] not in _LABEL_SET
            or not isinstance(row["text"], str)
            or not row["text"]
            or row["text"] != row["text"].strip()
        ):
            raise InvalidResponseError("invalid entity entry")
        result.append(_Mention(label=row["label"], text=row["text"]))
    return result


def _ground(payload: object, source: str) -> list[Entity]:
    result: list[Entity] = []
    for mention in _mentions(payload):
        positions = _exact_positions(source, mention.text)
        if len(positions) != 1:
            raise InvalidResponseError(
                "each extracted text must occur exactly once in the document"
            )
        start, end = next(iter(positions))
        result.append(Entity(start, end, mention.label))
    result = sorted(set(result))
    for index, first in enumerate(result):
        if any(
            first.start < second.end and second.start < first.end
            for second in result[index + 1 :]
        ):
            raise InvalidResponseError("extracted entities overlap")
    return result


def _validation_retry_messages(
    messages: Sequence[Message], payload: object, error: InvalidResponseError
) -> tuple[Message, ...]:
    detail = str(error)
    if len(detail) > 500:
        detail = detail[:497] + "..."
    return (
        *messages,
        {
            "role": "assistant",
            "content": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        },
        {
            "role": "user",
            "content": _VALIDATION_RETRY_PROMPT.format(detail=detail),
        },
    )


class Annotator:
    """Extract and ground clinical entities with an injectable chat client."""

    def __init__(self, client: ChatClient) -> None:
        self.client = client

    def annotate(self, text: str) -> list[Entity]:
        """Return non-overlapping offsets into text without changing the source."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        messages: tuple[Message, ...] = (
            {"role": "system", "content": _EXTRACTION_PROMPT},
            {"role": "user", "content": text},
        )
        request_messages = messages
        last_error: InvalidResponseError | None = None
        for attempt in range(2):
            try:
                payload = self.client.complete(
                    request_messages,
                    EXTRACTION_SCHEMA,
                    schema_name="entity_extraction",
                )
            except InvalidResponseError as exc:
                last_error = exc
                continue
            try:
                return _ground(payload, text)
            except InvalidResponseError as exc:
                last_error = exc
                if attempt == 0:
                    request_messages = _validation_retry_messages(
                        messages, payload, exc
                    )
        assert last_error is not None
        raise InvalidResponseError(
            f"entity_extraction remained invalid after one retry: {last_error}"
        ) from last_error
