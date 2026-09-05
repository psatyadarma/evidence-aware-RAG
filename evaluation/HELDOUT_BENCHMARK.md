# Development and held-out benchmark split

Checkpoint 7 freezes a second benchmark before answerability or generation is
implemented. The existing 25 examples remain unchanged and are now explicitly
the **development benchmark**. The new 45-example **held-out test benchmark** is
reserved for final evaluation and was not passed to any retriever in this
checkpoint.

This split matters because retrieval architecture was informed by errors on the
original questions even though no question IDs or expected evidence were placed
in application code. Development-set performance is useful for debugging, but
it is not by itself an unbiased final estimate.

## Development benchmark

Files: `benchmark_seed.jsonl` and canonical `benchmark.jsonl`.

- 25 human-reviewed examples;
- remains the default for all lexical, semantic, and structured development
  evaluation scripts;
- may be used to design and debug later answerability and generation stages;
- all accepted retrieval numbers are development-set results.

| Answerability | Count |
| --- | ---: |
| ANSWERABLE | 10 |
| PARTIALLY_ANSWERABLE | 3 |
| INSUFFICIENT_EVIDENCE | 10 |
| OUT_OF_SCOPE | 2 |

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

## Held-out test benchmark

Files: human-reviewable `heldout_benchmark_seed.jsonl`, canonical
`heldout_benchmark.jsonl`, and `heldout_benchmark.freeze.json`.

- 45 newly written examples;
- created before answerability and generation development;
- uses the same strict schema, source snapshot, evidence identifiers,
  computation types, and knowledge boundary as development;
- reserved for final system evaluation;
- not a default input to any development evaluation script;
- no lexical, semantic, or structured retrieval metrics have been calculated
  for it.

| Answerability | Count |
| --- | ---: |
| ANSWERABLE | 16 |
| PARTIALLY_ANSWERABLE | 9 |
| INSUFFICIENT_EVIDENCE | 14 |
| OUT_OF_SCOPE | 6 |

| Category | Count |
| --- | ---: |
| direct_lookup | 5 |
| comparison | 4 |
| aggregation | 4 |
| descriptive_trend | 3 |
| partially_answerable | 9 |
| unsupported_causality | 4 |
| temporal_mismatch | 3 |
| geographic_mismatch | 2 |
| unavailable_value | 2 |
| false_premise | 3 |
| out_of_scope | 6 |

## How the questions differ

The test questions are not entity/year substitutions of development wording.
They introduce new combinations such as citizen and non-resident population,
median age, density and dependency ratios; female regional totals; male
planning-area totals; the 10–19, under-15, 20–34 and 75+ ranges; an explicit
subzone; longer historical intervals; forecasts; and compound
population-plus-out-of-domain requests.

Wording was individually reviewed for plausible analyst or citizen intent.
Some questions explicitly name aggregation bands, some use conversational
phrasing, and others request qualified numerical differences. They were not
written as parser exploits or paraphrases of individual development items.

## Deterministic ground truth

No model determined facts, labels, evidence, or arithmetic. The build process:

1. loads all committed processed observations and stable evidence IDs;
2. validates every held-out record with the existing strict benchmark schema;
3. requires every cited evidence ID to resolve against the corpus;
4. recomputes each difference, sum, comparison, or argmax from source values;
5. verifies direct numeric answers against their sole source observation;
6. verifies declared unavailable temporal/geographic targets still resolve to
   zero corpus observations;
7. preserves regional rounding qualifications and literal `NOT_AVAILABLE`
   cells; and
8. writes canonical JSONL before calculating its checksum.

Unsupported examples distinguish a missing year or geographic granularity from
an attribute outside the selected corpus. Descriptive observations may be cited
for causal or false-premise cases because they establish the operands, but they
do not make the causal claim supported.

## Leakage and near-duplicate checks

The deterministic split analysis checks:

- duplicate IDs across development and held-out sets;
- identical normalized questions;
- questions identical after replacing known corpus geography names and years
  with placeholders; and
- exact duplicate non-empty required-evidence sets across the two sets.

The frozen set has zero findings in all three overlap analyses, no duplicate
IDs, and no internally duplicated normalized questions. This is an intentionally
simple leakage guard, not a semantic-similarity claim.

Application modules are scanned to ensure they do not reference held-out files.
Tests also assert that all three retrieval evaluation scripts still point to
`evaluation/benchmark.jsonl`.

## Freeze metadata

The canonical held-out benchmark SHA-256 is:

```text
99eb8be69c33d1744ae15c8891bec5f7e963142e334172753b4fe39361947065
```

The manifest stores this checksum, the seed checksum, distributions, example
count, human-review status, zero overlap counts, and an explicit
`retrieval_performance_evaluated: false` marker. Tests fail if the benchmark or
seed changes without an intentional rebuild and review.

Ground-truth validation can be reproduced without running retrieval:

```powershell
python scripts/build_heldout_benchmark.py
python -m pytest
```

The build command does not instantiate or call a retriever and reports no
performance measurement.

## Difficult labelling decisions

- Questions combining a supported observation with a missing year, forecast,
  causal request, income, or education attribute are `PARTIALLY_ANSWERABLE`.
- Pure causal requests are `INSUFFICIENT_EVIDENCE` even when before/after values
  are available, because the retained observations are descriptive.
- False-premise requests are `INSUFFICIENT_EVIDENCE`; their operands are retained
  so the premise can eventually be checked without granting causal support.
- A qualified request for a published regional numerical difference is
  answerable when both values are available and rounding/boundary limitations
  are stated. A request for the cause or unqualified underlying change is not.
- Annual planning-area or subzone requests are geographic/temporal gaps rather
  than out-of-scope attributes: the geography exists in the census snapshot but
  not at the requested annual coverage.
- Literal `-` cells are `unavailable_value`, never numeric zero.
- Ethnicity, employment, housing prices, life expectancy, household composition,
  and election participation are `OUT_OF_SCOPE` for this deliberately small
  corpus even if official data may exist elsewhere.
