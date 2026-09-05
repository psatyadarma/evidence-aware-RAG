# Gated deterministic computation and grounded generation

Checkpoint 10 implements the first end-to-end answer path, stopping before any
held-out evaluation or system comparison. The accepted lexical, semantic, and
structured retrievers; deterministic answerability gate; LLM classifier v1/v2;
development benchmark; and frozen reserved benchmark are unchanged.

## Selected pipeline

```text
question
  -> deterministic structured interpretation and retrieval
  -> frozen deterministic answerability gate
      -> INSUFFICIENT_EVIDENCE / OUT_OF_SCOPE: deterministic abstention, zero LLM calls
      -> ANSWERABLE / PARTIALLY_ANSWERABLE:
           gate-supported evidence IDs only
           -> deterministic computation
           -> typed verified fact + material qualifications + unsupported components
           -> schema-constrained grounded generator
           -> local semantic validation
```

The LLM cannot override the gate or calculate an answer. Application code has
no benchmark import and never reads an expected answer. A model or output
failure returns a null answer and explicit error after at most two attempts.

## Deterministic computation contract

`VerifiedComputation` contains one primary `VerifiedFact` and zero or more
`SourceQualification` records. The fact contains a stable fact ID, operation,
pre-rendered verified statement, numeric/categorical result, optional winning
evidence ID, ordered operands, and exact evidence IDs. Each operand retains its
source observation ID, year, numeric value, label, and dimensions.

The engine accepts only evidence IDs on gate-supported claims. Missing IDs,
`NOT_AVAILABLE`, non-finite values, wrong operand counts, ties in an argmax, and
unsupported operations fail explicitly.

| Operation | Deterministic rule |
| --- | --- |
| lookup | Return the single supported observation. |
| difference | Chronological cases use later minus earlier; same-year named comparisons use first requested entity minus second. |
| comparison | Compare exactly two ordered operands and return `first`, `second`, or `equal`, plus the signed second-minus-first gap. |
| sum | Sum every supplied supported operand. |
| argmax | Select the unique maximum from the complete supplied candidate set. |
| descriptive trend | Report start/end observations and later-minus-earlier change without causal interpretation. |

The partial 2020/2015 Tampines case illustrates why the operation is derived
from supported operands as well as the full query: its independently supported
clause has one 2020 observation and becomes a lookup, not an incomplete trend.

## Qualification propagation

Qualifications are selected from source IDs and evidence years, never question
IDs:

- planning-region observations retain nearest-10 rounding;
- a planning-region comparison crossing 2019/2020 retains the Master Plan
  2014 versus Master Plan 2019 boundary change and forbids presenting the
  published difference as an unqualified underlying change;
- Census observations retain the Census 2020 snapshot and Master Plan 2019
  geography scope;
- full abstentions on a literal `-` state that the value is not available and
  is not zero.

The validator requires exact material qualification strings in the user-facing
answer. It also rejects numeric tokens outside the supplied
question/fact/qualification/limitation package.

## Partial-answer plan

For `PARTIALLY_ANSWERABLE`, the context carries the fact computed only from
supported claim evidence and every unsupported component with a stable ID,
controlled reason codes, and a deterministic explanation. The generator must
answer the supported part and reproduce every unsupported explanation. It
cannot promote unsupported causal, historical, predictive, or
boundary-sensitive content into a factual claim.

## Grounded prompt and output schema

Both live iterations use the accepted `gpt-4.1-mini-2025-04-14` snapshot,
temperature 0, a 1,000-token cap, at most two attempts, `store=false`, and the
existing provider abstraction. Output contains a user-facing `answer`, exactly
one typed claim with exact fact/evidence/value fields, ordered qualification
IDs, and ordered unsupported-component IDs.

Local validation requires exact verified claim text in the claim and answer,
exact evidence/value correspondence, full fact coverage, exact
qualification/limitation text, and no new numeric token. Invalid output is
retried once and otherwise remains a null-answer failure.

The official model page confirms Responses API, Structured Outputs, and the
pinned snapshot support: [OpenAI Docs: GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini).
The Responses reference documents structured JSON output, `store`, temperature,
and output-token controls: [OpenAI Docs: Responses](https://developers.openai.com/api/reference/resources/responses/methods/create).

## Authorized prompt iterations

| Version | SHA-256 | Result |
| --- | --- | --- |
| `grounded_answer_generation_v1` | `f9d44bf1ffa8a8785829d8b7cfa52d37f1a1ed228a5d3af851c8aa2ce05d537b` | 0/13 valid; 26 calls including retries. Generic prompt/validator wording mismatch. |
| `grounded_answer_generation_v2` | `1189ce7c6a8ec5ce76fa951189d0f2a7717279969b91917a3cd975d75fc5c3ac` | 13/13 valid on first attempt. |

V1 is preserved rather than overwritten. Its invalid-output path failed to
retain successful HTTP-response telemetry, so its displayed zero token/cost
values mean unavailable telemetry, not zero spend. V2 repairs telemetry for
invalid outputs and makes the already enforced verbatim/cardinality contract
explicit. No third iteration was run.

## Development results

| Measurement | V2 result |
| --- | ---: |
| Development questions | 25 |
| Questions sent to generator | 13 |
| Calls avoided by deterministic gate | 12 |
| Provider calls including retries | 13 |
| Valid generated answers | 13/13 |
| Deterministic computation correctness | 13/13 (1.000) |
| Citation validity | 13/13 (1.000) |
| Required evidence coverage | 13/13 (1.000) |
| Material qualification retention | 13/13 (1.000) |
| Partial limitation retention | 3/3 |
| Full abstentions that called the model | 0 |
| Full abstentions with a numeric answer | 0 |
| Structurally unsupported typed claims | 0/13 |
| Manual unsupported claims | 0/13 |
| Input / output tokens | 14,419 / 2,238 |
| Estimated v2 cost | $0.0093484 |
| Mean / median latency | 2,780.9 / 2,544.8 ms |

The cost estimate uses the GPT-4.1 mini rates published on 2026-09-05:
$0.40/M input, $0.10/M cached input, and $1.60/M output. It excludes v1 because
v1 usage telemetry is unrecoverable.

## Claim-level audit and metric limits

All 13 retained v2 answers were manually reviewed against their facts,
evidence IDs, qualifications, and partial limitations. No answer introduced a
new factual, causal, predictive, arithmetic, or citation claim. The audit is in
`results/development_grounded_generation_v2_claim_audit.json`.

The machine unsupported-claim rate checks exact fact statements, fact/evidence
IDs, numeric and categorical values, fact coverage, and unapproved numeric
tokens. It cannot prove arbitrary free-text entailment. The manual audit covers
the retained small development run only. Neither is an LLM judge or semantic
similarity metric.

## Failure and abstention behavior

V1's 13 failures are retained with an explicit generic root-cause analysis. V2
had no failures. Provider transport errors and schema/semantic validation errors
remain distinguishable in attempt records. Exhaustion never fabricates an
answer.

Full abstentions are concise deterministic messages keyed to the gate reason.
They cover causal evidence gaps, missing years/geographies/operands, false
premises, out-of-domain attributes, ambiguity, and published `NOT_AVAILABLE`.
No abstention invokes the generator or returns a computed numeric answer.

## Future ungated comparison (not run)

The summary defines a paired future experiment using the same frozen split and
verified fact packages. The treatment is this gated pipeline; the
counterfactual sends all questions to a generator. It would compare unsupported
claims, full-abstention correctness, calls, token cost, and latency. This is
design-only here and was not run.

## Scope and limitations

All results are development results on 25 exposed questions. Exact answer
blocks deliberately prioritize auditability over conversational variety. The
reserved 45-question benchmark remains unopened by the runtime and unevaluated.
There is no threshold baseline, ungated/vanilla generation run, LLM judge, UI,
API, Docker setup, or fabricated result.
