# Development benchmark and deterministic metrics

The original benchmark was fixed before implementing retrieval or RAG so its questions reflect the reviewed corpus boundary rather than the strengths of a future model or retriever. It contains 25 explicitly curated questions and is now designated the **development benchmark** because retrieval architecture was informed by its failure analysis. No model is used to create ground truth.

`benchmark_seed.jsonl` is the human-reviewed source. `scripts/build_benchmark_seed.py` resolves and validates observation references, recomputes arithmetic, checks expected-absent targets, rejects duplicate IDs, and writes canonical `benchmark.jsonl`. Both files are retained so wording changes remain reviewable while the built artifact is reproducible.

Checkpoint 7 adds a separate 45-question held-out test benchmark, created and
frozen before answerability or generation development. It is reserved for final
evaluation and was not used to measure any retriever during its creation. See
[`HELDOUT_BENCHMARK.md`](HELDOUT_BENCHMARK.md) for distributions, ground-truth
construction, overlap checks, checksum, and difficult label decisions.

Checkpoint 8 adds a deterministic evidence-sufficiency baseline evaluated only
on this 25-question development set. See
[`ANSWERABILITY.md`](ANSWERABILITY.md) for claim support, decision rules,
development metrics, premise checks, and limitations.

Checkpoint 9 adds one provider-isolated LLM evidence-sufficiency classifier on
the same frozen structured evidence. Its prompt, typed output, retry/citation
policy, provider choice, and development-only reporting contract are documented
in [`LLM_ANSWERABILITY.md`](LLM_ANSWERABILITY.md). That v1 development run is
preserved as an accepted experimental iteration with 0.600 end-to-end accuracy,
16 valid predictions, and nine structured-output failures.

The accepted v1 run and final v2 contract repair are compared in
[`LLM_ANSWERABILITY_V2.md`](LLM_ANSWERABILITY_V2.md). V2 separates requested
claims from premises, types evidence relations, and derives the top-level class
deterministically. Its development accuracy is 0.720 versus v1's 0.600 and the
frozen deterministic baseline's 1.000. The reserved test benchmark remains
unevaluated.

Checkpoint 10 selects the frozen deterministic gate for the end-to-end path,
adds benchmark-independent deterministic computation, and permits grounded
generation only for `ANSWERABLE` and `PARTIALLY_ANSWERABLE`. Its two authorized
prompt iterations, dev-only results, claim audit, abstention/call accounting,
and cost limitations are documented in
[`GROUNDED_GENERATION.md`](GROUNDED_GENERATION.md). The reserved test benchmark
remains unevaluated.

Checkpoint 11 implements the ungated and scalar-threshold complete policies,
selects the single threshold on development only, freezes the A/B/C comparison,
and preregisters the one-shot final protocol. See
[`FINAL_EVALUATION_PROTOCOL.md`](FINAL_EVALUATION_PROTOCOL.md). The dedicated
final command exists but has not been executed; held-out performance remains
unseen.

## Distribution

| Category | Count |
| --- | ---: |
| direct_lookup | 3 |
| comparison | 3 |
| aggregation | 2 |
| descriptive_trend | 2 |
| partially_answerable | 3 |
| unsupported_causality | 3 |
| temporal_mismatch | 2 |
| geographic_mismatch | 2 |
| false_premise | 2 |
| unavailable_value | 1 |
| out_of_scope | 2 |

| Answerability | Count |
| --- | ---: |
| ANSWERABLE | 10 |
| PARTIALLY_ANSWERABLE | 3 |
| INSUFFICIENT_EVIDENCE | 10 |
| OUT_OF_SCOPE | 2 |

Categories describe the question or failure mode; answerability describes the required action given this corpus. A category therefore does not substitute for an answerability label.

## Answerability definitions

- `ANSWERABLE`: every factual claim needed for the requested answer is supported or deterministically derivable from available observations.
- `PARTIALLY_ANSWERABLE`: at least one requested claim is supported and at least one is not. The expected answer includes only the supported portion and explicitly identifies the limitation.
- `INSUFFICIENT_EVIDENCE`: the requested substantive answer requires full abstention. This includes missing years/geographies, unavailable cells, unsupported causal explanations, and false premises.
- `OUT_OF_SCOPE`: the requested subject is outside the three-source population corpus. It must fully abstain and cannot require corpus evidence.

For abstention metrics, only `INSUFFICIENT_EVIDENCE` and `OUT_OF_SCOPE` require full abstention. `PARTIALLY_ANSWERABLE` is not treated as full abstention.

## Benchmark schema

Each JSONL record contains:

- `id`, `question`, `category`, and `answerability`;
- an optional structured `expected_answer` with human-reviewed canonical text and optional numeric value/tolerance;
- `required_evidence`, the minimum observation IDs needed for the supported answer or to detect a boundary/false premise;
- structured `claims` marking each requested claim supported or unsupported;
- `requires_computation` and an optional checked `computation` recipe;
- `unavailable_targets` for temporal/geographic selectors that must resolve to no observation;
- review notes explaining important definitions or limitations.

Strict Pydantic validation rejects unknown fields and inconsistent combinations, including answerable examples without evidence, partial examples without mixed claim support, out-of-scope examples with corpus evidence, and unsupported computation types.

## Stable evidence references

Evidence is referenced at observation level, not merely by dataset. IDs are deterministically derived from the processed source ID, upstream record ID, year, and observation-level dimensions:

```text
population_indicators_annual:r2:y2025
resident_population_census_2020:r323:y2020:age_group=total:sex=total
```

The first identifies the 2025 observation in the Resident Population series. The second identifies the total-sex, total-age Tampines observation in Census 2020. The benchmark loader fails if any required ID disappears. Retrieval may later operate on larger passages, but each retrieved passage must expose the observation IDs it contains for Recall@k evaluation.

Temporal and geographic mismatch examples additionally use structured selectors expected to return zero matches. This makes labels fail validation if a later corpus revision adds the previously unavailable year/geography.

## Derived answers

Supported computation types are:

- `difference`: second observation minus first;
- `sum`: sum of all required observations;
- `comparison`: `first`, `second`, or `equal` for two observations;
- `argmax`: the evidence ID of the unique maximum.

The builder recomputes every stored result from committed processed data. A stored numeric expected answer must equal the recomputed numeric result. Direct numeric lookups with one evidence ID must equal that source observation. Canonical natural-language wording remains manually reviewed because string equality is not a defensible general answer-quality metric.

Rebuild and validate with:

```powershell
python scripts/build_benchmark_seed.py
python -m pytest
```

## Metric definitions

`evaluation/metrics.py` implements deterministic primitives only; it does not evaluate a system yet.

- **Answerability accuracy:** correct predicted class / all examples.
- **Per-class precision:** true positives / all predictions of that class. A zero denominator returns 0.
- **Per-class recall:** true positives / all ground-truth examples of that class. A zero denominator returns 0.
- **Macro F1:** unweighted mean of F1 across all four answerability labels, including labels with zero support in a slice.
- **Abstention precision:** correct full abstentions / all predicted abstentions.
- **Abstention recall:** correct full abstentions / all examples labelled `INSUFFICIENT_EVIDENCE` or `OUT_OF_SCOPE`.
- **Coverage:** non-abstained substantive responses / all questions. A non-abstained partial answer counts as covered.
- **Recall@k:** unique required evidence IDs present in the first `k` retrieved IDs / unique required evidence IDs. Examples with no required evidence are undefined and omitted from mean Recall@k.
- **Numeric correctness:** exact comparison unless the answer key declares an absolute tolerance; relative tolerance is zero. Free-text-only answers return “not automatically scored.”

Free-text answer correctness, citation entailment, and unsupported-claim assessment intentionally remain interfaces for later human or carefully designed evaluation. There is no LLM judge.

## Ambiguous cases and decisions

- The 2019/2020 West Region example is `PARTIALLY_ANSWERABLE`: both published totals and their arithmetic difference are available, but the values use different URA Master Plan boundaries, so the difference is not labelled an unqualified true population change.
- False-premise questions are `INSUFFICIENT_EVIDENCE`, with observations retained as required evidence so a system can reject the premise. Their `expected_answer` remains null because the requested causal answer requires abstention; a safe response may still state the correction.
- Temporal/geographic mismatch examples cite a nearby available observation because its provenance establishes source coverage, while their structured unavailable target captures the missing request.
- A literal census `-` is `NOT_AVAILABLE`, not zero. The benchmark does not infer whether it reflects suppression, non-applicability, or another publication convention because the corpus does not establish the reason.

## Retrieval baseline

Checkpoint 4 defines one retrieval unit per observation and evaluates a
deterministic cosine TF-IDF baseline. The complete design, measured Recall@k,
and error analysis are in [`RETRIEVAL.md`](RETRIEVAL.md). Detailed machine-readable
outputs are retained in `results/lexical_retrieval_report.jsonl` and
`results/lexical_retrieval_summary.json`.

Checkpoint 5 compares that frozen baseline with one pinned BGE semantic
retriever over the identical corpus and benchmark. See
[`SEMANTIC_RETRIEVAL.md`](SEMANTIC_RETRIEVAL.md) for model selection, artifact
validation, resource measurements, results, and failure analysis.

Checkpoint 6 adds an independent deterministic schema-aware method without
changing either frozen baseline. See
[`STRUCTURED_RETRIEVAL.md`](STRUCTURED_RETRIEVAL.md) for the typed query model,
normalisation rules, exact metadata filtering, three-way results, and diagnostic
behaviour on the 12 examples excluded from positive Recall@k.

## Known limitations

Both sets remain modest and are not statistically representative of all
population questions. Canonical answers have not undergone independent
annotation, and most natural-language correctness cannot be scored
automatically. Existing lexical, semantic, and structured measurements are
development-set evidence-retrieval results only; the held-out test set has not
been evaluated.
