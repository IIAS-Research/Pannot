# Usage

Pannot processes one document at a time during corpus preparation. A
corpus-building application calls `annotate()` for each document and stores the
returned entities in the format required by its training pipeline. Pannot does
not store or manage the corpus itself.

## Requirements

- Python 3.11 or later;
- an API that supports the `/v1/chat/completions` endpoint;
- a model and server that support structured outputs through
  `response_format` with `json_schema`.

Pannot does not fall back to free-form JSON. It rejects any response that does not
match the schema.

## Installation

Clone the repository, create a virtual environment, and install Pannot from the
repository root:

    git clone https://github.com/IIAS-Research/pannot.git
    cd pannot
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install .

The project is not published on PyPI.

## Create the annotator

`OpenAIChatClient` uses the `openai` Python package as an HTTP client because
many local model servers support the OpenAI-compatible Chat Completions API
format. Its name describes this format, not the hosting provider. It does not
require an OpenAI-hosted service. Pannot sends model requests to the configured
endpoint.

This example connects to Qwen served locally:

    from pannot import Annotator, OpenAIChatClient

    client = OpenAIChatClient(
        base_url="http://localhost:8000/v1",
        model="Qwen/Qwen3.8-27B",
        qwen_non_thinking=True,
    )
    annotator = Annotator(client)

This configuration sends model requests to `localhost`. When this server runs
the model locally and does not forward requests, the workflow can remain on
local infrastructure. It does not require an OpenAI API key.

`qwen_non_thinking=True` sends
`chat_template_kwargs.enable_thinking=false` to vLLM. Leave out this option if
the server does not support that parameter.

For any compatible service that requires an API key, provide it directly.
Pannot does not read it from the environment automatically:

    import os

    client = OpenAIChatClient(
        base_url="https://example.org/v1",
        model="model-name",
        api_key=os.environ["LLM_API_KEY"],
    )

The URL and API key are security-sensitive settings. Before you use a
remote service, make sure it is authorized to receive the data you send.

## Annotate text

    text = "Mme Dupont a été reçue le 3 mai 2024."
    entities = annotator.annotate(text)

    for entity in entities:
        mention = text[entity.start:entity.end]
        print(entity.label, entity.start, entity.end, mention)

`annotate()` returns a `list[Entity]`. Each `Entity` is an immutable object
with `start`, `end`, and `label` fields. The offsets use half-open intervals:
`start` is included and `end` is excluded.

Pannot does not create files or store the text or entities. The calling
application decides whether to store them.

See the [processing pipeline](pipeline.md) for the extraction, source grounding,
repair, ambiguity selection, and validation steps.

The available labels are:

- `NOM`, `PRENOM`;
- `DATE`, `DATE_NAISSANCE`;
- `IPP`, `NDA`, `SECU`;
- `TEL`, `MAIL`;
- `ADRESSE`, `VILLE`, `ZIP`;
- `HOPITAL`.

To add conventions for a specific organization or corpus, see the
[local adaptation guide](local-adaptation.md).

## Errors and model calls

Pannot rejects a response if it does not match the schema, contains invalid
choices, or produces overlapping final entities. Extraction and selection can
each be retried once. An absent mention in the initial extraction attempt
follows the limited, source-constrained repair path instead of the ordinary
extraction retry.

Repair and ambiguity selection add model calls only when needed. Pannot may
therefore send the same text to the same endpoint several times during one call
to `annotate()`. The repair response itself is not retried. See the
[processing pipeline](pipeline.md) for the exact branches.

Pannot passes transport and provider errors to the calling application without
an automatic retry. It does not change the model, split the text, or return an
empty annotation to hide a failure. The calling application decides how to
handle the exception. When you work with clinical data, do not enable detailed
HTTP logs in the SDK.
