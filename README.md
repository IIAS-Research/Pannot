# Pannot

Pannot uses an LLM to pseudo-annotate French clinical documents. Its main
purpose is to create or expand a corpus that can be reviewed and then used to
train a smaller, specialized NER model.

The LLM proposes labels and mention text. Pannot validates these proposals,
calculates source-backed offsets, and uses a constrained selection call when a
mapping is ambiguous. The LLM is used during corpus preparation; the smaller
model is intended for repeated processing of new documents.

Pannot handles only the pseudo-annotation step. It does not manage the corpus,
train or evaluate the downstream model, or guarantee document
pseudonymization.

## Installation from source

Python 3.11 or later is required.

Pannot is not available on PyPI. Clone the repository and install it in a
virtual environment:

    git clone https://github.com/IIAS-Research/pannot.git
    cd pannot
    python3 -m venv .venv
    source .venv/bin/activate
    python -m pip install .

## Quick start

Create the client and annotator, then pass text directly to the annotator:

    from pannot import Annotator, OpenAIChatClient

    client = OpenAIChatClient(
        base_url="http://localhost:8000/v1",
        model="Qwen/Qwen3.8-27B",
        qwen_non_thinking=True,
    )
    annotator = Annotator(client)

    text = "Mme Dupont a été reçue le 3 mai 2024."
    entities = annotator.annotate(text)

    for entity in entities:
        print(entity.label, entity.start, entity.end, text[entity.start:entity.end])

`OpenAIChatClient` works with OpenAI-compatible endpoints, including local
servers such as vLLM. It uses the `openai` Python package as an HTTP client, but
it does not require an OpenAI-hosted service.

`annotate()` returns a list of immutable `Entity` objects. Pannot does not create
files or store the text or annotations.

To provide local conventions, pass their content to the annotator:

    from pathlib import Path

    instructions = Path("profiles/reims.md").read_text(encoding="utf-8")
    annotator = Annotator(client, local_instructions=instructions)

The files `profiles/reims.md` and `profiles/parhaf.md` are examples of local
conventions. Pannot never applies them automatically. Copy and adapt the one
that best matches your context. These profiles are written in French because
their content is sent to the model as instructions for French clinical text.

If a service requires an API key, pass it explicitly with the `api_key`
argument. Pannot does not read it from the environment automatically.

## Processing pipeline

For each document, Pannot:

1. combines the task prompt, generic annotation rules, and optional local rules;
2. asks the LLM for structured `label` and `text` pairs, without offsets;
3. validates each proposal and finds its positions in the source text;
4. uses a limited repair call that can select only source-derived candidates
   when proposed text is absent from the document;
5. uses a selection call for unresolved occurrence counts, conflicting labels,
   variant matches, or overlapping candidates;
6. returns sorted, non-overlapping `Entity` objects with source-backed offsets.

The repair and selection calls run only when needed. See the
[processing pipeline](docs/pipeline.md) for the complete behavior, including
validation and retries.

## Documentation

The [full documentation](https://iias-research.github.io/pannot/) covers:

- [the purpose and scope of Pannot](https://iias-research.github.io/pannot/);
- [the internal processing pipeline](https://iias-research.github.io/pannot/pipeline/);
- [the Python API and returned objects](https://iias-research.github.io/pannot/usage/);
- [how to define local conventions](https://iias-research.github.io/pannot/local-adaptation/).

To build the documentation locally:

    python -m pip install ".[docs]"
    python -m mkdocs serve

## Medical data

Pannot sends the document text, annotation instructions, and any retry context
to the configured endpoint. The workflow can remain on local infrastructure
when this endpoint runs the model locally and does not forward requests. Before
processing clinical data, check that the service is authorized and that its
hosting and security meet your requirements. Pannot does not write the document
or returned annotations to disk. The calling application remains responsible
for storing them, if needed.

## License

Pannot is licensed under the GNU General Public License version 3 only
(`GPL-3.0-only`). See [LICENSE](LICENSE).

Datasets, models, and other third-party resources keep their own licenses.
