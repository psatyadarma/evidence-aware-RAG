# LLM evidence-sufficiency classifier

Checkpoint 9 adds one hosted-model classifier over the frozen structured
retriever. It classifies evidence sufficiency only: it does not generate a
natural-language answer, perform RAG, apply a similarity threshold, use an LLM
judge, or evaluate the reserved test split.

## Fixed experimental path

For every development question, the path is:

```text
question
  -> frozen deterministic structured interpretation
  -> frozen structured retrieval
  -> compact evidence-sufficiency context
  -> one LLM classification
```

The frozen deterministic classifier is run alongside the LLM for the
per-question comparison. Neither `app/answerability.py` nor
`app/structured_retrieval.py` was changed in this checkpoint. Ground-truth
labels, required evidence, canonical answers, and benchmark notes are not sent
to the model.

## Provider and model

The experiment uses OpenAI's Responses API with the pinned
`gpt-4.1-mini-2025-04-14` snapshot. It was selected because this is a focused
instruction-following and classification task, the model supports Structured
Outputs and temperature 0, and a snapshot is more reproducible than a moving
alias. The official model page characterizes GPT-4.1 mini as a smaller, faster
model with low latency and lists the snapshot, Responses API support, and
Structured Outputs support: [GPT-4.1 mini model](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

As of 2026-09-05, published standard pricing is USD 0.40 per million input
tokens, USD 0.10 per million cached input tokens, and USD 1.60 per million
output tokens. The evaluator records actual API token counts and applies those
rates. Expected latency is interactive, seconds-scale hosted inference rather
than local deterministic execution, but no precise latency or SLA is assumed;
the live report measures mean and median request latency.

Provider code is confined to `app/providers/openai_responses.py`. The
application-facing classifier depends only on the `EvidenceSufficiencyProvider`
protocol. The adapter uses Python's standard-library HTTPS client, so no OpenAI
SDK or other runtime dependency was added. Credentials come only from
`RAG_MODEL_API_KEY`; the key is never serialized to an evaluation artifact.

## Prompt and evidence contract

The single zero-shot prompt is version
`evidence_sufficiency_zero_shot_v1`. Its template/schema SHA-256 is:

```text
993d98497b58700f9dc5bb0c6296bbccca762d5d66d54a8db0630c5e6a60670c
```

The prompt defines all four existing labels and distinguishes relevance from
sufficiency. It prohibits unsupported inference, causality from descriptive
observations, extrapolation from historical values, zero substitution for
missing or `NOT_AVAILABLE` observations, acceptance of contradicted premises,
and silent combination of incompatible definitions. It defines partial
answerability only for independently useful supported and unsupported
subrequests, and distinguishes an absent corpus attribute (`OUT_OF_SCOPE`) from
missing in-domain evidence (`INSUFFICIENT_EVIDENCE`). It requests concise
structured justification, not hidden or long-form chain-of-thought.

The model receives canonical JSON containing:

- the user question;
- the frozen `StructuredQuery` and retrieval diagnostics;
- only the exact retrieved observations, with evidence ID, source and dataset,
  publisher, year, dimensions, numeric/raw value, and status;
- the three-source corpus scope and only material qualifications: coverage,
  rounding, the 2019/2020 planning-boundary change, and unavailable markers.

Evidence JSON is serialized with sorted keys and compact separators. Repeated
construction from the same question and frozen artifacts is byte-identical.

## Typed output and citation validation

`LlmEvidenceSufficiencyDecision` is a strict Pydantic schema with:

- exactly one existing `AnswerabilityClassification`;
- one to eight concise claim assessments;
- claim support state (`SUPPORTED`, `UNSUPPORTED`, `UNAVAILABLE`,
  `OUT_OF_SCOPE`, or `AMBIGUOUS`);
- supporting evidence IDs for supported claims only;
- decision-level evidence IDs, including observations used to detect a false
  premise or limitation;
- controlled reason codes and one concise rationale.

Validators enforce class/claim consistency. `ANSWERABLE` requires every claim
to be supported; `PARTIALLY_ANSWERABLE` requires mixed support;
`OUT_OF_SCOPE` requires every claim to be out of scope; and
`INSUFFICIENT_EVIDENCE` cannot contain a supported claim. All claim citations
must also occur in the decision citation list. After Pydantic parsing, every
cited ID is checked against the exact IDs supplied in that request. A
nonexistent citation invalidates the whole attempt.

The REST request uses strict JSON Schema Structured Outputs, `temperature=0`,
`max_output_tokens=1000`, no tools, and `store=false`. Official OpenAI
documentation describes the Responses API JSON-schema format and its strict
schema behavior: [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Failure and retry policy

The default is at most two attempts per question.

- Timeouts, network failures, rate limits, server errors, malformed JSON,
  schema-invalid output, unknown labels, missing fields, inconsistent
  claim/class combinations, and invalid citations are eligible for the bounded
  retry.
- Authentication and other nonretryable client errors stop immediately.
- A refusal is explicit and nonretryable.
- Exhausted attempts produce a typed failure with a null LLM prediction. A
  failure is never converted into an answerability label or counted as an
  abstention.

If failures occur, the confusion matrix gains a `MODEL_FAILURE` column. They
count as incorrect for end-to-end accuracy and as false negatives for the
affected class, while adding no false positive to any answerability label.

## Prompt iteration discipline

Only zero-shot prompt version 1 has been implemented. There are no
benchmark-specific examples, question IDs, copied phrases, or per-question
patches. V1 was run once and is preserved unchanged as the accepted first
experimental iteration. Its general contract failures motivated the separately
versioned v2 schema documented in `LLM_ANSWERABILITY_V2.md`.

## Development comparison status

The frozen deterministic result remains:

| Method | Accuracy | Macro F1 | Abstention precision | Abstention recall |
| --- | ---: | ---: | ---: | ---: |
| Deterministic | 1.000 | 1.000 | 1.000 | 1.000 |
| LLM evidence classifier v1 | 0.600 | 0.603 | 1.000 | 0.417 |

The accepted v1 run produced 16 valid predictions and nine structured-output
failures across 34 provider calls. Fifteen of 25 examples were correct end to
end; one valid prediction was incorrect. It used 53,845 input and 6,488 output
tokens at an estimated cost of $0.0319188. Mean/median latency was
3,136.4/2,974.7 ms. The immutable machine-readable report and summary retain
the complete per-question details.

When a credential is available, run exactly:

```powershell
$env:RAG_MODEL_API_KEY = "<key>"
python scripts/evaluate_llm_answerability.py --live
```

The explicit `--live` acknowledgement prevents accidental paid calls. The
script loads only `evaluation/benchmark.jsonl` and writes:

- `evaluation/results/development_llm_answerability_report.jsonl`;
- `evaluation/results/development_llm_answerability_summary.json`.

Each report row includes the benchmark ID, ground truth, deterministic and LLM
predictions, their agreement/comparison outcome, supplied and cited evidence
IDs, LLM claim structure, reason codes, error category, and provider attempts.
The summary includes accuracy, macro and per-class metrics, confusion matrix,
abstention metrics, every error, calls, actual model identifier, tokens,
estimated cost, and mean/median latency.

## Privacy and reproducibility

Only public aggregate Singapore government observations and the evaluation
question are sent to the provider. No personal records or API key enter result
artifacts. The request sets `store=false`. OpenAI states that API data is not
used to train models unless the customer opts in, while default abuse-monitoring
logs may retain customer content for up to 30 days; eligible organizations may
have additional retention controls: [OpenAI API data controls](https://developers.openai.com/api/docs/guides/your-data).

The snapshot ID, prompt version/hash, temperature, token cap, attempt cap,
actual response model ID, and per-call metadata are recorded. Even with a model
snapshot and temperature 0, hosted inference is not guaranteed to be perfectly
repeatable because serving infrastructure and provider behavior can change.
Results must therefore be treated as one development run, not an immutable
property of the model.

## Current verification

Offline provider-mock tests cover prompt construction, deterministic evidence
serialization, typed parsing, valid and partial classifications, malformed
JSON, unknown labels, missing fields, nonexistent citations, `NOT_AVAILABLE`,
timeouts, retry exhaustion, nonretryable provider errors, provider request
shape, failure-aware metrics, and development/test-split isolation. These tests
make no network calls. The later v2 suite extends this coverage without altering
the v1 implementation or result artifacts.
