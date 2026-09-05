# Final Evaluation Protocol — Checkpoint 11

Status: **frozen, not run**

Research question: Is semantic relevance alone sufficient to decide whether a RAG system should answer, or does explicit evidence-sufficiency reasoning provide safer and more reliable behavior?

**The 45-question held-out benchmark has not yet been evaluated under this protocol.**

## Controlled systems

All three systems construct their answer evidence with the same frozen deterministic structured retriever.

| System | Complete answering policy | Application gate |
| --- | --- | --- |
| A — ungated RAG | question → structured retrieval → shared baseline LLM generator | none; the model may independently abstain |
| B — similarity-threshold RAG | question → structured retrieval → frozen BGE score → scalar threshold → the same baseline generator | allow iff maximum score is at least `0.6387588977813721`; zero evidence always abstains |
| C — deterministic evidence-aware RAG | question → structured interpretation/retrieval → frozen deterministic answerability → frozen deterministic computation → frozen grounded-generation v2 | explicit evidence-sufficiency decision; full abstentions never call generation |

This is a comparison of three **complete answering policies**, not a one-variable causal experiment. System C also contains deterministic computation and evidence-aware generation because they are part of the selected architecture. A separate gate-only table reports ungated, semantic-threshold, and deterministic-sufficiency behavior.

## Frozen model and prompts

- Provider: OpenAI Responses API.
- Model snapshot: `gpt-4.1-mini-2025-04-14`.
- Temperature: `0`.
- Maximum output tokens: `1000`.
- Maximum attempts: `2`; invalid structured output or retryable provider errors consume an attempt and are retained in telemetry.
- Systems A and B share `baseline_structured_evidence_generation_v1`, SHA-256 `c4d00904dd052c6e0eae77f324d8b920167ce226c096da011e121eafd36bff19`.
- System C uses frozen `grounded_answer_generation_v2`, SHA-256 `1189ce7c6a8ec5ce76fa951189d0f2a7717279969b91917a3cd975d75fc5c3ac`.

The baseline receives only the question, structured evidence, and associated source qualifications. It never receives deterministic answerability, benchmark labels, expected answers, required-evidence ground truth, gate diagnostics, or System C verified-fact objects. It emits typed `ANSWER` or `ABSTAIN` output, and every citation ID is validated against supplied evidence. Development produced no generic prompt/schema defect, so the allowed correction was not used and no v2 baseline exists.

## Similarity score and threshold selection

For question embedding `q` and every structured-retrieved evidence embedding `eᵢ`, all normalized under the frozen BGE protocol:

`threshold_score = maxᵢ cosine(q, eᵢ)`

If structured retrieval returns no observations, the score is `NONE` and System B abstains without a model call. Equality passes: `score >= τ` allows generation.

The frozen encoder is `BAAI/bge-small-en-v1.5`, revision `5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`, 384 dimensions, CPU inference, normalized float32 embeddings, and query prefix `Represent this sentence for searching relevant passages:`.

Threshold selection used only the 25 development questions. `ANSWERABLE` and `PARTIALLY_ANSWERABLE` map to `ALLOW`; `INSUFFICIENT_EVIDENCE` and `OUT_OF_SCOPE` map to `ABSTAIN`. Candidates are the next representable float below the minimum observed score, each unique finite observed score, and the next representable float above the maximum. `NONE` scores always predict `ABSTAIN`.

The objective was maximum binary macro F1 across `ALLOW` and `ABSTAIN`, followed in order by higher abstention recall, higher threshold, and deterministic ascending candidate order. The selected threshold is `τ = 0.6387588977813721`, with development macro F1 `0.6880570409982175` and abstention recall `0.4166666666666667`. Finite scores overlapped: ALLOW `0.6387588977813721–0.7784090638160706`; ABSTAIN `0.67622971534729–0.7735425233840942`; five development examples had `NONE`.

No generation output, question-by-question judgment, or held-out information affected threshold selection. The complete candidate set and predictions are frozen in `evaluation/results/development_threshold_selection.json`.

## Metrics fixed before the final run

Full-abstention truth comprises `INSUFFICIENT_EVIDENCE` and `OUT_OF_SCOPE`; `PARTIALLY_ANSWERABLE` is an allow class.

- Coverage = substantive answers / all examples. Provider failures are not answers.
- Abstention rate = valid abstentions / all examples.
- Abstention precision = correct full abstentions / all valid abstentions.
- Abstention recall = correct full abstentions / all should-abstain examples.
- `unsupported_answer_rate_on_should_abstain` = substantive answers to unsupported requested claims / all should-abstain examples.
- End-to-end success = correct supported answers plus correct abstentions, divided by all examples. Provider failures remain failures.
- Answer quality reports numeric/computation correctness, categorical/comparison correctness where typed, citation-ID validity, required-evidence coverage, machine-checkable unsupported numeric/typed claims, and partial-answer success.
- Partial success requires the supported requested portion, no answer to the unsupported portion, and an explicit limitation.
- Cost uses actual input, cached-input, and output token telemetry. The preregistered estimate uses the model price recorded in the machine artifact. Provider calls include retries; calls avoided are application-gate suppressions.
- Latency reports mean and median generation latency. System B additionally reports BGE gate latency. Frozen System C did not separately instrument deterministic-gate latency; its hosted gate cost is zero.

System A model abstentions count as complete-policy abstentions. System B records threshold and later model abstentions separately and together. System C records deterministic-gate abstentions. The gate-only comparison excludes later model abstentions. Provider failures are neither safe abstentions nor factual answers and are always reported separately.

The automatic baseline correctness field is deliberately narrow: it recognizes expected typed numeric/categorical results and required evidence, not arbitrary prose semantics. The retained development comparison therefore also includes a clearly labeled manual audit; no automatic semantic score or LLM judge is fabricated. Final outputs receive no manual prediction correction.

## Frozen development findings

### Complete policies

| Metric | A | B | C |
| --- | ---: | ---: | ---: |
| Coverage | 0.44 | 0.48 | 0.52 |
| Abstention rate | 0.56 | 0.52 | 0.48 |
| Abstention precision | 0.8571 | 0.9231 | 1.0000 |
| Abstention recall | 1.0000 | 1.0000 | 1.0000 |
| Unsupported-answer rate on should-abstain | 0/12 (0.0000) | 0/12 (0.0000) | 0/12 (0.0000) |
| Expected-content accuracy among answered | 11/11 (1.0000) | 11/12 (0.9167) | 13/13 (1.0000) |
| Citation-ID validity | 11/11 | 12/12 | 13/13 |
| Required-evidence coverage count | 10 | 10 | 13 |
| Partial-answer success | 1/3 | 1/3 | 3/3 |
| Provider failures | 0 | 0 | 0 |

### Application gates only

| Gate | Generation allow rate | Gate abstention precision | Gate abstention recall |
| --- | ---: | ---: | ---: |
| A — ungated | 1.00 | not applicable | 0.0000 |
| B — scalar similarity | 0.80 | 1.0000 | 0.4167 |
| C — deterministic sufficiency | 0.52 | 1.0000 | 1.0000 |

### Calls, cost, and latency

| System | Provider calls | Calls avoided | Input/output tokens | Estimated USD | Mean/median generation ms | Mean/median gate ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| A | 25 | 0 | 20,965 / 2,414 | 0.0122484 | 2059.53 / 1884.35 | n/a |
| B | 20 | 5 | 18,249 / 2,267 | 0.0109268 | 2098.09 / 2024.22 | 25.95 / 25.87 |
| C | 13 | 12 | 14,419 / 2,238 | 0.0093484 | 2780.94 / 2544.75 | not separately instrumented |

These estimates use the recorded GPT-4.1 mini rates as of 2026-09-05: USD 0.40/M standard input, USD 0.10/M cached input, and USD 1.60/M output. The final run records actual model identifiers and token telemetry.

Development error analysis found no provider failures and no unsupported answers on should-abstain cases. System A unnecessarily abstained on `q011` and `q012`; System B did so on `q012`. Both achieved only one of three strict partial successes. System B's `q011` answer omitted supported comparison content. A and B's `q005` winner claim did not cite all five comparison operands. Both omitted a material Census/rounding qualification on seven answers (`q002`, `q003`, `q005`, `q006`, `q007`, `q008`, `q010`). System C had no recorded computation, citation, qualification, partial-answer, or routing errors. These observations are frozen and are not used to patch the systems.

## One-shot execution policy

The prepared command is:

```text
python scripts/evaluate_final_heldout.py --live
```

It validates the protocol checksum, every listed frozen artifact, and the held-out file checksum before loading any held-out record or making a provider call. It then writes a start marker, runs A, B, and C, and creates immutable per-system JSONL reports, JSON summaries, a combined comparison, and a completion marker. Any pre-existing target—including a start marker from a failed run—causes refusal. There is no ordinary overwrite flag.

After that command starts: no threshold, prompt, benchmark, retrieval, answerability, or manual prediction changes are permitted. Results are reported as observed. A genuine infrastructure failure must retain its start marker and any partial outputs; its cause must be documented before a separately versioned exceptional rerun is considered. Existing artifacts must never be deleted or overwritten to make such a rerun appear to be the first.

The machine-readable companion is `evaluation/final_evaluation_protocol.freeze.json`; its checksum is stored in `evaluation/final_evaluation_protocol.sha256`. The JSON is authoritative for executable paths, hashes, constants, and metric definitions.
