# Local adaptation

The prompt has two parts:

- generic conventions included with the application and designed to work with
  any organization;
- an optional Markdown document that contains only local exceptions.

This structure lets you reuse the same pipeline without copying or rewriting
the main prompt.

## Create a profile

Create a Markdown file, such as `profiles/local.md`, and describe only the
rules that cannot be inferred from the generic conventions.

A useful local profile can explain:

- when a proper name refers to a building;
- the expected boundaries of an official site name;
- an ambiguous abbreviation used by the organization;
- a few fictional examples that show the exception.

Do not include:

- a copy of the general label definitions;
- technical instructions about the JSON format;
- rules without a clear local exception;
- real patient data or excerpts.

Local instructions are included in the extraction, text-repair, and ambiguity
selection prompts. They take priority when they clearly conflict with the
generic prompt. See the [processing pipeline](pipeline.md) for these stages.

## Use the profile

    from pathlib import Path
    from pannot import Annotator

    instructions = Path("profiles/local.md").read_text(encoding="utf-8")
    annotator = Annotator(client, local_instructions=instructions)
    entities = annotator.annotate(text)

The library receives the profile content as a string. It does not open the path
itself. `client` and `text` are defined as in the [usage guide](usage.md).

The `profiles/reims.md` and `profiles/parhaf.md` files show, respectively, how
to adapt Pannot to an organization and to a corpus's conventions. They are
examples and are never loaded automatically. The profiles are written in French
because their content is sent to the model as instructions for French clinical
text.

## Validate an adaptation

Before a large run, review the profile, test it on a few approved documents,
and manually check the exceptions that it adds. Then keep the exact profile
text in your application configuration.
