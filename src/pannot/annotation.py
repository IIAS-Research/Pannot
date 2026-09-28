"""LLM extraction and deterministic grounding to source-text offsets."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import cache
from importlib.resources import files
from typing import TypeVar

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
_CONTEXT_CHARS = 24
_MAX_REPAIR_MENTIONS = 32
_MAX_REPAIR_CANDIDATES = 8
_MAX_REPAIR_NGRAM_TOKENS = 6
_MIN_REPAIR_SIMILARITY = 0.55
_VALIDATION_ERROR_MARKER = "<<VALIDATION_ERROR>>"

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


@dataclass(frozen=True, slots=True, order=True)
class Entity:
    """A half-open ``[start, end)`` entity span in the unchanged source text."""

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


@dataclass(frozen=True, slots=True)
class _Choice:
    id: str
    entity: Entity


@dataclass(frozen=True, slots=True)
class _Occurrence:
    key: str
    start: int
    end: int
    choices: tuple[_Choice, ...]


@dataclass(frozen=True, slots=True)
class _Plan:
    kept: tuple[Entity, ...]
    occurrences: tuple[_Occurrence, ...]


@dataclass(frozen=True, slots=True)
class _TextRepair:
    key: str
    index: int
    mention: _Mention
    candidates: tuple[str, ...]


class _AbsentTextError(InvalidResponseError):
    def __init__(self, indices: tuple[int, ...]) -> None:
        self.indices = indices
        super().__init__("one or more extracted texts are absent from the document")


@cache
def _prompt(name: str) -> str:
    prompt_path = files("pannot").joinpath("prompts", name)
    return prompt_path.read_text(encoding="utf-8").strip()


def _compose_prompt(instruction: str, generic_prompt: str) -> str:
    return "\n\n".join((instruction.strip(), generic_prompt.strip()))

def _validation_retry_prompt(detail: str) -> str:
    template = _prompt("validation_retry_fr.md")
    prefix, marker, suffix = template.partition(_VALIDATION_ERROR_MARKER)
    if not marker or _VALIDATION_ERROR_MARKER in suffix:
        raise RuntimeError(
            "validation retry prompt must contain its marker exactly once"
        )
    return prefix + detail + suffix


def _is_word(character: str) -> bool:
    return bool(re.match(r"\w", character, flags=re.UNICODE))


def _exact_positions(source: str, value: str, *, bounded: bool) -> set[tuple[int, int]]:
    left = r"(?<!\w)" if bounded and _is_word(value[0]) else ""
    right = r"(?!\w)" if bounded and _is_word(value[-1]) else ""
    pattern = re.compile(r"(?=(" + left + re.escape(value) + right + r"))")
    return {match.span(1) for match in pattern.finditer(source)}


def _compact_casefold(value: str) -> str:
    return "".join(character.casefold() for character in value if not character.isspace())


def _variant_positions(source: str, value: str) -> set[tuple[int, int]]:
    compact_source: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(source):
        if character.isspace():
            continue
        folded = character.casefold()
        compact_source.extend(folded)
        offsets.extend([index] * len(folded))

    needle = _compact_casefold(value)
    if not needle:
        return set()
    haystack = "".join(compact_source)
    positions: set[tuple[int, int]] = set()
    start_at = 0
    while (match_start := haystack.find(needle, start_at)) >= 0:
        match_end = match_start + len(needle)
        start = offsets[match_start]
        end = offsets[match_end - 1] + 1
        if _compact_casefold(source[start:end]) != needle:
            start_at = match_start + 1
            continue
        if not (
            (_is_word(value[0]) and start > 0 and _is_word(source[start - 1]))
            or (_is_word(value[-1]) and end < len(source) and _is_word(source[end]))
        ):
            positions.add((start, end))
        start_at = match_start + 1
    return positions


def _overlap(first: Entity, second: Entity) -> bool:
    return first.start < second.end and second.start < first.end


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


def _make_plan(payload: object, source: str) -> _Plan:
    mentions = _mentions(payload)
    grouped: dict[str, list[str]] = defaultdict(list)
    for mention in mentions:
        grouped[mention.text].append(mention.label)

    kept: set[Entity] = set()
    candidates: set[Entity] = set()
    located: dict[str, tuple[set[tuple[int, int]], set[tuple[int, int]]]] = {}
    absent: set[str] = set()
    for value in grouped:
        exact = _exact_positions(source, value, bounded=True)
        variants = _variant_positions(source, value)
        positions = exact | variants
        if not exact:
            positions |= _exact_positions(source, value, bounded=False)
        if not positions:
            absent.add(value)
            continue
        located[value] = exact, positions

    if absent:
        indices = tuple(index for index, mention in enumerate(mentions) if mention.text in absent)
        raise _AbsentTextError(indices)

    for value, row_labels in grouped.items():
        labels = set(row_labels)
        exact, positions = located[value]
        if len(labels) == 1 and len(row_labels) == len(exact) and positions == exact:
            label = next(iter(labels))
            kept.update(Entity(start, end, label) for start, end in exact)
        else:
            candidates.update(
                Entity(start, end, label)
                for start, end in positions
                for label in labels
            )

    all_spans = sorted(kept | candidates)
    ambiguous_kept: set[Entity] = set()
    for index, span in enumerate(all_spans):
        if span in kept and (
            span in candidates
            or any(
                index != other and _overlap(span, candidate)
                for other, candidate in enumerate(all_spans)
            )
        ):
            ambiguous_kept.add(span)
    kept.difference_update(ambiguous_kept)
    candidates.update(ambiguous_kept)

    by_offsets: dict[tuple[int, int], list[Entity]] = defaultdict(list)
    for entity in sorted(candidates):
        by_offsets[entity.start, entity.end].append(entity)
    occurrences: list[_Occurrence] = []
    for occurrence_index, ((start, end), entities) in enumerate(sorted(by_offsets.items()), 1):
        key = f"o{occurrence_index}"
        choices = tuple(
            _Choice(id=f"{key}c{choice_index}", entity=entity)
            for choice_index, entity in enumerate(sorted(entities, key=lambda item: item.label), 1)
        )
        occurrences.append(_Occurrence(key=key, start=start, end=end, choices=choices))
    return _Plan(kept=tuple(sorted(kept)), occurrences=tuple(occurrences))


def _repair_candidates(source: str, invalid_text: str) -> tuple[str, ...]:
    """Return a small ranked set of exact token n-grams from ``source``."""

    token_pattern = re.compile(r"\w+(?:[^\w\s]+\w+)*", flags=re.UNICODE)
    source_tokens = tuple(token_pattern.finditer(source))
    target_tokens = tuple(token_pattern.finditer(invalid_text))
    target_size = min(max(1, len(target_tokens)), _MAX_REPAIR_NGRAM_TOKENS)
    sizes = range(max(1, target_size - 1), min(_MAX_REPAIR_NGRAM_TOKENS, target_size + 1) + 1)
    normalized_target = " ".join(invalid_text.casefold().split())
    ranked: dict[str, tuple[float, int, int]] = {}
    for size in sizes:
        for start_index in range(len(source_tokens) - size + 1):
            first = source_tokens[start_index]
            last = source_tokens[start_index + size - 1]
            candidate = source[first.start() : last.end()]
            normalized_candidate = " ".join(candidate.casefold().split())
            similarity = SequenceMatcher(
                None, normalized_target, normalized_candidate, autojunk=False
            ).ratio()
            if similarity < _MIN_REPAIR_SIMILARITY:
                continue
            rank = (similarity, -abs(len(candidate) - len(invalid_text)), -first.start())
            if candidate not in ranked or rank > ranked[candidate]:
                ranked[candidate] = rank
    return tuple(
        candidate
        for candidate, _rank in sorted(
            ranked.items(),
            key=lambda item: (*(-value for value in item[1]), item[0]),
        )[:_MAX_REPAIR_CANDIDATES]
    )


def _text_repairs(
    payload: object, source: str, error: _AbsentTextError
) -> tuple[_TextRepair, ...]:
    mentions = _mentions(payload)
    if len(error.indices) > _MAX_REPAIR_MENTIONS:
        raise InvalidResponseError("too many absent mentions to repair in one bounded request")
    return tuple(
        _TextRepair(
            key=f"m{repair_index}",
            index=mention_index,
            mention=mentions[mention_index],
            candidates=_repair_candidates(source, mentions[mention_index].text),
        )
        for repair_index, mention_index in enumerate(error.indices, 1)
    )


def _text_repair_schema(repairs: Sequence[_TextRepair]) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [repair.key for repair in repairs],
        "properties": {
            repair.key: {
                "type": ["string", "null"],
                "enum": [*repair.candidates, None],
            }
            for repair in repairs
        },
    }


def _text_repair_input(
    source: str, payload: object, repairs: Sequence[_TextRepair]
) -> str:
    return json.dumps(
        {
            "document": source,
            "first_extraction": payload,
            "invalid_mentions": [
                {
                    "key": repair.key,
                    "label": repair.mention.label,
                    "text": repair.mention.text,
                    "candidates": list(repair.candidates),
                }
                for repair in repairs
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _apply_text_repairs(
    payload: object, selection: object, repairs: Sequence[_TextRepair]
) -> dict[str, object]:
    mentions = _mentions(payload)
    expected = {repair.key for repair in repairs}
    if not isinstance(selection, dict) or set(selection) != expected:
        raise InvalidResponseError("text repair must contain every mention key exactly once")
    replacements: dict[int, str | None] = {}
    for repair in repairs:
        value = selection[repair.key]
        if value is not None and (
            not isinstance(value, str) or value not in repair.candidates
        ):
            raise InvalidResponseError("text repair contains an invalid candidate")
        replacements[repair.index] = value
    return {
        "entities": [
            {"label": mention.label, "text": replacements.get(index, mention.text)}
            for index, mention in enumerate(mentions)
            if index not in replacements or replacements[index] is not None
        ]
    }


def _selection_schema(plan: _Plan) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [occurrence.key for occurrence in plan.occurrences],
        "properties": {
            occurrence.key: {
                "type": ["string", "null"],
                "enum": [*[choice.id for choice in occurrence.choices], None],
            }
            for occurrence in plan.occurrences
        },
    }


def _selection_input(source: str, plan: _Plan) -> str:
    return json.dumps(
        {
            "document": source,
            "occurrences": [
                {
                    "key": occurrence.key,
                    "start": occurrence.start,
                    "end": occurrence.end,
                    "text": source[occurrence.start : occurrence.end],
                    "left": source[max(0, occurrence.start - _CONTEXT_CHARS) : occurrence.start],
                    "right": source[occurrence.end : occurrence.end + _CONTEXT_CHARS],
                    "choices": [
                        {"id": choice.id, "label": choice.entity.label}
                        for choice in occurrence.choices
                    ],
                }
                for occurrence in plan.occurrences
            ],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _selected_entities(payload: object, plan: _Plan) -> list[Entity]:
    expected = {occurrence.key for occurrence in plan.occurrences}
    if not isinstance(payload, dict) or set(payload) != expected:
        raise InvalidResponseError("selection must contain every occurrence key exactly once")
    selected = list(plan.kept)
    for occurrence in plan.occurrences:
        value = payload[occurrence.key]
        choices = {choice.id: choice.entity for choice in occurrence.choices}
        if value is None:
            continue
        if not isinstance(value, str) or value not in choices:
            raise InvalidResponseError("selection contains an invalid choice")
        selected.append(choices[value])
    result = sorted(set(selected))
    for index, first in enumerate(result):
        if any(_overlap(first, second) for second in result[index + 1 :]):
            raise InvalidResponseError("selected entities overlap")
    return result


T = TypeVar("T")


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
            "content": _validation_retry_prompt(detail),
        },
    )


class Annotator:
    """Extract and ground clinical entities with an injectable chat client."""

    def __init__(self, client: ChatClient) -> None:
        generic_prompt = _prompt("generic_fr.md")
        self.client = client
        self._extraction_prompt = _compose_prompt(
            _prompt("extraction_fr.md"), generic_prompt
        )
        self._selection_prompt = _compose_prompt(
            _prompt("selection_fr.md"), generic_prompt
        )
        self._text_repair_prompt = _compose_prompt(
            _prompt("text_repair_fr.md"), generic_prompt
        )
    def _request(
        self,
        messages: Sequence[Message],
        schema: JsonSchema,
        schema_name: str,
        validate: Callable[[object], T],
        repair_absent: Callable[[object, _AbsentTextError], T] | None = None,
    ) -> T:
        request_messages = tuple(messages)
        last_error: InvalidResponseError | None = None
        for attempt in range(2):
            try:
                payload = self.client.complete(
                    request_messages, schema, schema_name=schema_name
                )
            except InvalidResponseError as exc:
                last_error = exc
                continue
            try:
                return validate(payload)
            except InvalidResponseError as exc:
                last_error = exc
                if attempt == 0:
                    if isinstance(exc, _AbsentTextError) and repair_absent is not None:
                        return repair_absent(payload, exc)
                    request_messages = _validation_retry_messages(
                        messages, payload, exc
                    )
        assert last_error is not None
        raise InvalidResponseError(
            f"{schema_name} remained invalid after one retry: {last_error}"
        ) from last_error

    def _repair_absent_texts(
        self, text: str, payload: object, error: _AbsentTextError
    ) -> _Plan:
        repairs = _text_repairs(payload, text, error)
        messages = (
            {"role": "system", "content": self._text_repair_prompt},
            {"role": "user", "content": _text_repair_input(text, payload, repairs)},
        )
        try:
            selection = self.client.complete(
                messages,
                _text_repair_schema(repairs),
                schema_name="entity_text_repair",
            )
            repaired = _apply_text_repairs(payload, selection, repairs)
            return _make_plan(repaired, text)
        except InvalidResponseError as exc:
            raise InvalidResponseError(
                f"entity_extraction remained invalid after one retry: {exc}"
            ) from exc

    def annotate(self, text: str) -> list[Entity]:
        """Return non-overlapping offsets into ``text`` without changing the source."""
        if not isinstance(text, str):
            raise TypeError("text must be a string")
        extraction_messages = (
            {"role": "system", "content": self._extraction_prompt},
            {"role": "user", "content": text},
        )
        plan = self._request(
            extraction_messages,
            EXTRACTION_SCHEMA,
            "entity_extraction",
            lambda payload: _make_plan(payload, text),
            repair_absent=lambda payload, error: self._repair_absent_texts(
                text, payload, error
            ),
        )
        if not plan.occurrences:
            return list(plan.kept)

        selection_messages = (
            {"role": "system", "content": self._selection_prompt},
            {"role": "user", "content": _selection_input(text, plan)},
        )
        return self._request(
            selection_messages,
            _selection_schema(plan),
            "entity_selection",
            lambda payload: _selected_entities(payload, plan),
        )
