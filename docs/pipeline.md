# Processing pipeline

Pannot helps create a pseudo-annotated corpus for training a smaller named
entity recognition (NER) model. It uses an LLM during corpus preparation: the
LLM proposes mentions, while Pannot validates and grounds them in the source
text.

The result is a set of candidate pseudo-annotations. They are not gold-standard
annotations, and they do not pseudonymize or rewrite the documents.

## Place in a training workflow

A typical workflow is:

1. collect a representative set of authorized French clinical documents;
2. use Pannot to create pseudo-annotations;
3. review and correct the annotations, and measure their quality;
4. store the approved annotations in the format required by the training code;
5. train and evaluate a smaller NER model;
6. use that smaller model for repeated inference.

Pannot covers the second step and returns annotations in memory. The calling
project handles corpus storage, review, data splits, model training, and
evaluation.

## Processing one document

The normal path is:

```text
document -> extraction -> source grounding
         -> optional text repair and re-grounding
         -> optional ambiguity selection -> Entity objects
```

### 1. Build the instructions

When an `Annotator` is created, Pannot combines:

- the packaged prompt for the current stage;
- the packaged generic French annotation rules;
- optional `local_instructions` supplied by the caller.

Pannot builds separate system prompts for extraction, text repair, and
ambiguity selection. Local instructions are included in all three.

### 2. Extract candidate mentions

**Input:** the complete, unchanged document and the annotation instructions.

**Model output:** a strict JSON object containing `label` and `text` pairs:

```json
{"entities":[{"label":"NOM","text":"Dupont"}]}
```

The model is asked to copy text from the document and return one row for each
occurrence. It does not return offsets. The schema accepts only Pannot's
thirteen labels, exact object keys, non-empty text, and at most 2,000 rows.

### 3. Validate and ground mentions in the source

**Input:** the proposed mentions and the original document.

**Output:** confirmed spans plus any candidates that still require a decision.

Python code validates the response shape, labels, and text. It then searches
the unchanged source document. Pannot prefers word-bounded exact matches. It
can also find controlled case and whitespace variants, and it uses an
unbounded exact substring only when no bounded exact match exists.

Pannot calculates half-open offsets `[start, end)` itself. The LLM never
supplies trusted offsets.

An extracted text group is accepted without another model call only when it has
one label, its row count matches the number of bounded exact occurrences, no
extra variant exists, and none of its spans overlaps any other proposed span.
Mismatched occurrence counts, competing labels, variants, and overlaps are
kept as candidates.

### 4. Repair text that is absent from the document

This stage runs only when the initial extraction attempt returns mention text
that cannot be found in the source.

Pannot creates a short ranked list of exact source passages for each absent
mention. One repair call receives the document, the first extraction, and these
candidates. For each mention, the model can select one listed passage or
`null`. It cannot invent a replacement outside the list or change other
mentions.

The repair is bounded to at most 32 absent mentions and 8 candidates per
mention. A selected passage replaces the invalid text. `null` removes only that
mention. Pannot then grounds the repaired extraction again.

### 5. Resolve ambiguous occurrences

This stage runs only when source grounding cannot make a safe decision.

For each ambiguous offset, Pannot sends:

- the complete document;
- source-derived `start`, `end`, and text;
- nearby left and right context;
- the allowed label choice identifiers.

The model selects one allowed identifier or `null` for each occurrence. It
cannot invent a new span, text, or label. `null` omits that occurrence.

### 6. Validate and return entities

Pannot rejects missing or extra response keys, unknown choices, malformed
entries, and overlapping final spans. It removes duplicates, sorts the spans,
and returns a `list[Entity]`.

Each immutable `Entity` contains:

- `start`: the first character offset;
- `end`: the exclusive end offset;
- `label`: one supported entity label.

The source text is unchanged. `Entity` does not contain confidence,
provenance, or a copy of the mention text.

## Model calls and retries

The shortest path uses one model call for extraction. An absent mention can add
one repair call. Ambiguity can add one selection call. Selection can still
follow repair when the repaired mention has several possible positions.

Extraction and selection each allow at most one retry:

- if no usable JSON payload is available, Pannot repeats the same request;
- if a decoded payload fails local validation, the retry includes the rejected
  payload and a short validation error.

An absent mention in the initial extraction attempt follows the
source-constrained repair path instead of the ordinary validation retry. The
repair call itself is not retried.

Transport and provider errors are passed directly to the caller.
`OpenAIChatClient` also disables automatic SDK retries. Pannot does not switch
models, split the document, or return an empty annotation to hide a failure.

Each model stage that needs context receives the full document. Pannot does not
split long documents automatically, so one call to `annotate()` can send the
same text more than once.

## Guarantees and limits

Every returned entity:

- points to a span in the original document;
- uses one of the supported labels;
- does not overlap another returned entity.

These checks cannot prove that a label is correct or that the LLM found every
entity. Pseudo-annotations should be reviewed on representative documents and
compared with expert annotations before they are used to train a smaller model.
