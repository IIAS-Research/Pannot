# Build training data for smaller clinical NER models

Pannot uses an LLM to create pseudo-annotations for French clinical documents.
A pseudo-annotation is a candidate entity annotation produced by a model
instead of a human annotator. After review and quality checks, the resulting
corpus can be used to train a smaller, specialized named entity recognition
(NER) model.

The LLM is used during corpus preparation. The smaller model is intended to
process new documents repeatedly without calling the LLM for every document.

```text
French clinical documents
          |
          v
Pannot + LLM
          |
          v
Pseudo-annotated corpus
          |
          v
Review and quality checks
          |
          v
Train and evaluate a smaller NER model
          |
          v
Repeated inference on new documents
```

Pannot covers the pseudo-annotation stage. The calling project remains
responsible for storing the corpus, reviewing annotations, creating data
splits, and training and evaluating the smaller model.

## Why use Pannot?

The approach separates two costs:

1. the one-time or occasional cost of preparing training annotations with an
   LLM;
2. the recurring cost of processing new documents with the smaller model.

Pannot helps reduce the manual work needed to start or extend a training
corpus. The pseudo-annotations are candidates, not gold-standard annotations.
Their quality must be measured on representative documents before they are
used for downstream training.

Pannot is based on four main choices:

- the LLM proposes labels and mention text, but does not return offsets;
- Python code finds each mention in the unchanged source text;
- optional local rules adapt the generic prompt without changing the code;
- the result is a list of `Entity` objects that a corpus-building application
  can store in its own training format.

## Processing summary

For each document:

1. Pannot builds stage-specific instructions from packaged prompts and optional
   local rules.
2. The LLM returns structured `label` and `text` pairs.
3. Pannot validates the response and computes source-text offsets.
4. If proposed text is absent, a limited repair call can select only from
   source-derived candidates.
5. If occurrences, labels, or overlaps are ambiguous, a selection call chooses
   from source-derived options.
6. Pannot validates the final spans and returns sorted, non-overlapping
   entities.

A simple document needs one model call. Repair, selection, and one retry per
extraction or selection stage are used only when required. Read the
[processing pipeline](pipeline.md) for the complete internal behavior.

## Scope

Pannot:

- creates candidate pseudo-annotations for downstream training;
- returns half-open offsets `[start, end)` in the source text;
- supports thirteen predefined clinical categories;
- supports local conventions without code changes.

Pannot does not:

- replace expert review or evaluation against gold annotations;
- guarantee that every identifying entity is found;
- pseudonymize a document collection;
- store or manage a corpus;
- train or evaluate the smaller model.

Start with the [usage guide](usage.md). If your institution has specific
conventions, also read the [local adaptation guide](local-adaptation.md).
