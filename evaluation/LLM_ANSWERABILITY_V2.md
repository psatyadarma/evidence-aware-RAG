# LLM evidence sufficiency: v2 final development iteration

Checkpoint 9 v2 is the final prompt/schema iteration before an architecture
decision. It preserves the accepted v1 prompt, code, report, summary, token,
cost, and latency artifacts. It uses the same frozen structured retriever,
25-question development benchmark, pinned `gpt-4.1-mini-2025-04-14` model,
temperature 0, 1,000-token output cap, and maximum of two attempts. It does not
generate answers or evaluate the reserved test benchmark.

## 1. V1 root cause

V1 overloaded `evidence_ids` to mean only evidence that supports a claim and
also asked the model to emit the top-level class. Nine otherwise meaningful
outputs failed local Pydantic validation in two recurrent ways:

1. A model-selected `INSUFFICIENT_EVIDENCE` decision contained a supported
   descriptive subclaim or premise.
2. An unsupported or unavailable claim cited observations that supplied
   context, contradicted a premise, or explicitly established
   `NOT_AVAILABLE`; v1 rejected all such citations because they did not support
   the requested conclusion.

The mismatch was between evidence-reasoning semantics and the output contract,
not malformed JSON or retrieval failure. It also blurred descriptive premises
with claims materially requested by the user.

## 2. V2 schema and evidence relations

The model now returns `LlmEvidenceAssessmentV2`, containing only:

- `requested_claims` with a support state, concise reason, and typed evidence;
- `premises` with `SUPPORTED`, `CONTRADICTED`, or `UNKNOWN` status;
- controlled reason codes and a concise summary.

The model does not emit a top-level answerability label. Evidence references
use one of four explicit relations:

| Relation | Meaning |
| --- | --- |
| `SUPPORTS` | Direct support or a required deterministic operand |
| `CONTRADICTS` | Observation contradicts an asserted premise |
| `ESTABLISHES_UNAVAILABILITY` | A supplied `NOT_AVAILABLE` observation establishes that no numeric value is published |
| `CONTEXT` | Relevant descriptive context that does not support the requested conclusion |

Validation remains strict. Supported requested claims require `SUPPORTS`;
unsupported claims cannot use it; unavailable claims require
`ESTABLISHES_UNAVAILABILITY`; contradicted premises require `CONTRADICTS`; and
every referenced ID must be in the exact request payload. Schema-invalid output
still receives only the fixed bounded retry and then remains a model failure.

## 3. Requested claims, premises, and deterministic derivation

Descriptive premises no longer contribute to partial answerability. For a pure
causal request, the requested claim is the cause; an observed before/after
change or sex/geography comparison is a premise. A supported premise cannot
make the causal request partial.

Application code derives the existing class from requested claims only:

- all requested claims supported -> `ANSWERABLE`;
- multiple requested claims with mixed support -> `PARTIALLY_ANSWERABLE`;
- every requested claim out of scope -> `OUT_OF_SCOPE`;
- otherwise -> `INSUFFICIENT_EVIDENCE`.

The derivation has no benchmark-label access. Invalid claim output produces no
classification.

## 4. q013 caveat audit

The exact reconstructed v1 request contained the boundary caveat on both West
Region observations:

> 2019 uses URA Master Plan 2014 boundaries; 2020 onward uses Master Plan 2019 boundaries.

Therefore q013 was not an evidence-serialization defect. It remains a genuine
v1 and v2 source-caveat reasoning error. V2 did not receive a question-specific
patch; its prompt only states the general rule that supplied qualifications can
make an unqualified real-world conclusion unsupported even when published
arithmetic is possible.

## 5. Development results

| Metric | V1 | V2 |
| --- | ---: | ---: |
| End-to-end accuracy | 0.600 | 0.720 |
| Macro F1 | 0.603 | 0.714 |
| Structured-output failures | 9 | 3 |
| Valid predictions | 16 | 22 |
| Abstention precision | 1.000 | 1.000 |
| Abstention recall | 0.417 | 0.667 |
| Provider calls | 34 | 28 |
| Estimated cost | $0.0319 | $0.0311 |

The frozen deterministic baseline remains 25/25: accuracy 1.000 and macro F1
1.000. V2 improved substantially over v1 by matching the schema to valid
reasoning relations, but it remains materially worse than the deterministic
baseline on this narrow structured corpus.

### V2 per-class metrics

| Class | Support | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| `ANSWERABLE` | 10 | 0.909 | 1.000 | 0.952 |
| `PARTIALLY_ANSWERABLE` | 3 | 0.667 | 0.667 | 0.667 |
| `INSUFFICIENT_EVIDENCE` | 10 | 1.000 | 0.400 | 0.571 |
| `OUT_OF_SCOPE` | 2 | 0.500 | 1.000 | 0.667 |

### V2 confusion matrix

Rows are ground truth; columns are derived prediction.

| Actual \ Predicted | ANSWERABLE | PARTIAL | INSUFFICIENT | OUT OF SCOPE | MODEL FAILURE |
| --- | ---: | ---: | ---: | ---: | ---: |
| `ANSWERABLE` | 10 | 0 | 0 | 0 | 0 |
| `PARTIALLY_ANSWERABLE` | 1 | 2 | 0 | 0 | 0 |
| `INSUFFICIENT_EVIDENCE` | 0 | 1 | 4 | 2 | 3 |
| `OUT_OF_SCOPE` | 0 | 0 | 0 | 2 | 0 |

V2 made eight correct full-abstention predictions out of twelve required. No
substantive case was falsely abstained, so abstention precision is 1.000 and
recall is 0.667. Model failures are not treated as abstentions.

## 6. Remaining errors

Seven examples were incorrect end to end: four valid but wrong derived
predictions and three exhausted structured-output failures.

| ID | Outcome | Error category | Finding |
| --- | --- | --- | --- |
| q013 | `ANSWERABLE` vs partial | Source-caveat failure | The model saw the boundary caveat but treated the published difference as the true underlying change. |
| q016 | `PARTIALLY_ANSWERABLE` vs insufficient | Claim-decomposition failure | It correctly rejected the causal explanation but incorrectly promoted the observed female/male comparison from premise to a separate requested claim. |
| q017 | `OUT_OF_SCOPE` vs insufficient | Out-of-scope failure | It treated an in-domain unavailable year/granularity combination as an absent domain. |
| q018 | Model failure | Evidence-relation failure | It used `UNAVAILABLE` for a missing year without a supplied `NOT_AVAILABLE` row, so the required relation could not be cited. |
| q020 | `OUT_OF_SCOPE` vs insufficient | Out-of-scope failure | It treated a missing in-domain year at subzone granularity as out of scope. |
| q021 | Model failure | Evidence-relation failure | It used `SUPPORTS` on an unsupported requested claim instead of using the rows to contradict the premise. |
| q022 | Model failure | Evidence-relation failure | It used `SUPPORTS` on the unsupported claim and omitted the required `CONTRADICTS` relation for the contradicted premise. |

There were no remaining unsupported-causality acceptance errors: q016's causal
claim was explicitly unsupported. Its error was decomposition—counting the
descriptive premise as an independently requested answer component.

The immutable live report retains the taxonomy emitted by the runner. A
separate deterministic post-run analysis refines q016, q017, and q020 without
changing model output or predictions:
`results/development_llm_answerability_v2_error_analysis.json`.

## 7. Tokens, cost, and latency

| Measurement | V1 | V2 |
| --- | ---: | ---: |
| Input tokens | 53,845 | 54,143 |
| Cached input tokens | 0 | 0 |
| Output tokens | 6,488 | 5,902 |
| Provider calls | 34 | 28 |
| Estimated cost | $0.0319188 | $0.0311004 |
| Mean latency | 3,136.4 ms | 3,376.8 ms |
| Median latency | 2,974.7 ms | 3,367.6 ms |

The estimate applies the published GPT-4.1 mini rates current on 2026-09-05:
$0.40/M input, $0.10/M cached input, and $1.60/M output. The official model page
documents pricing, Structured Outputs, Responses API support, and the pinned
snapshot: [OpenAI Docs: GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini).

## 8. Prompt/schema identity and reproducibility

- Prompt/schema version: `evidence_sufficiency_zero_shot_v2`
- Prompt/schema SHA-256:
  `1e616f05cee98b98276432521969fad9fe0c5d034004a8d916dd5969e2b03b1a`
- Model: `gpt-4.1-mini-2025-04-14`
- Temperature: 0
- Maximum attempts: 2
- Maximum output tokens: 1,000
- Provider storage request: `store=false`

Official OpenAI documentation states that Structured Outputs constrain model
responses to a supplied JSON schema, while local semantic validators remain
necessary for relationships the schema cannot express:
[OpenAI Docs: Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
Hosted inference is still not perfectly repeatable at temperature 0.

## 9. Artifacts and conclusion

V1 remains in:

- `results/development_llm_answerability_report.jsonl`;
- `results/development_llm_answerability_summary.json`.

V2 is retained separately in:

- `results/development_llm_answerability_v2_report.jsonl`;
- `results/development_llm_answerability_v2_summary.json`;
- `results/development_llm_answerability_v2_error_analysis.json`.

The result supports a conservative architecture decision: deterministic rules
are preferable for the current structured corpus and known dimensions. The LLM
offers more flexible linguistic decomposition, but even after the principled
contract repair it introduces classification variability, domain-boundary
confusion, source-caveat errors, and schema failures. No further development
prompt tuning is authorized in this checkpoint.
