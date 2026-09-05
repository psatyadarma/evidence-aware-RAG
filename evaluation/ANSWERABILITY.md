# Deterministic answerability baseline

Checkpoint 8 evaluates whether transparent schema-aware rules can determine
evidence sufficiency without an LLM. It uses the question, the frozen structured
interpretation and retrieval result, corpus metadata, and limited deterministic
comparisons. It does not generate an answer.

All numbers in this document are **development-set results** over the frozen
25-question development benchmark. The separate frozen test set was not loaded
by the evaluation runner and no test-set performance was measured.

## Relevance is not sufficiency

A retrieved row can be topically relevant while still being insufficient. Two
before/after population values do not explain a cause; one endpoint does not
support a trend; historical observations do not support a forecast; an exact
cell containing `NOT_AVAILABLE` is not a numeric answer; and one regional row
does not support an argmax over every region.

The baseline therefore evaluates exact operand coverage and claim type rather
than treating retrieval presence or score as confidence.

## Decision and claim schemas

`DeterministicAnswerabilityDecision` contains:

- exactly one existing classification: `ANSWERABLE`,
  `PARTIALLY_ANSWERABLE`, `INSUFFICIENT_EVIDENCE`, or `OUT_OF_SCOPE`;
- one or more typed requested claims;
- deterministic reason codes;
- preserved retrieved evidence IDs;
- the complete structured query; and
- an optional machine-checkable premise comparison.

No numerical confidence is emitted.

Claims use the kinds `descriptive`, `causal`, `forecast`, `out_of_domain`, and
`underlying_change`. Each has one support state: `SUPPORTED`, `UNSUPPORTED`,
`UNAVAILABLE`, `OUT_OF_SCOPE`, or `AMBIGUOUS`. Supported claims must cite source
evidence. Unsupported causal or external claims do not borrow descriptive rows
as proof, although those rows remain visible at decision level.

## Deterministic protocol

1. Run the frozen structured interpreter and exact structured retriever.
2. Determine the number of required operands from years, geography candidates,
   requested level, published age bands, sex values, and operation.
3. Require every operand to be present and numeric. Comparisons, differences,
   sums, trends, and argmax are sufficient only when their complete comparison
   set is available.
4. Distinguish a requested year absent from the applicable source from an exact
   observation whose status is `NOT_AVAILABLE`.
5. Treat material unresolved geography ambiguity as
   `INSUFFICIENT_EVIDENCE`. A cross-level comparison also receives
   `INCOMPATIBLE_COMPARISON`.
6. Treat a primary unsupported attribute such as income, schools, health,
   housing, employment, ethnicity, household composition, or opinion as
   `OUT_OF_SCOPE`. A missing row alone never implies out of scope.
7. Treat forecasts or projections without published future observations as
   insufficient; never extrapolate.
8. Split only explicit compound request boundaries. If a separately requested
   descriptive clause is supported but a causal, forecast, missing-period, or
   external-attribute clause is unsupported, classify the whole request as
   `PARTIALLY_ANSWERABLE`.
9. Treat an incomplete atomic trend as insufficient, not partial merely because
   one time point happens to exist.
10. Mark a 2019-to-2020-or-later planning-region comparison with a boundary
    caveat. A qualified request for the published numerical difference remains
    answerable. A request for the unqualified true/underlying change gains an
    unsupported claim.

Reasons include evidence completeness, unsupported attribute, missing year or
geography, unavailable value, missing causal evidence, prediction request,
false premise, incompatible comparison, partial support, ambiguous geography,
boundary-definition change, missing operands, and unresolved interpretation.

## Causality and false premises

`why`, `cause`, `reason`, and related wording create a causal claim. Population
observations are descriptive, so that claim is unsupported even when all
before/after rows exist.

For causal questions with an explicit directional premise, the baseline may
sum matching operands and compare them deterministically:

- temporal increase/decrease premises compare the two requested years;
- `more than` and `outnumber` premises compare the two requested geographies or
  sex groups.

A contradiction adds `FALSE_PREMISE`. This check does not perform general
natural-language fact checking and does not turn the unavailable causal
explanation into a supported claim.

## Label policy

- `ANSWERABLE`: every requested claim is supported by complete compatible
  observations and allowed deterministic operations.
- `PARTIALLY_ANSWERABLE`: at least one independently meaningful requested claim
  is supported and at least one is not.
- `INSUFFICIENT_EVIDENCE`: the request is in domain, but no requested claim is
  fully supportable because evidence is missing, unavailable, ambiguous,
  incompatible, predictive, causal, or premise-dependent.
- `OUT_OF_SCOPE`: every requested claim concerns an attribute outside the
  retained corpus.

## Development results

| Metric | Value |
| --- | ---: |
| Accuracy | 1.000 |
| Macro F1 | 1.000 |
| Abstention precision | 1.000 |
| Abstention recall | 1.000 |

| Class | Support | Precision | Recall | F1 |
| --- | ---: | ---: | ---: | ---: |
| ANSWERABLE | 10 | 1.000 | 1.000 | 1.000 |
| PARTIALLY_ANSWERABLE | 3 | 1.000 | 1.000 | 1.000 |
| INSUFFICIENT_EVIDENCE | 10 | 1.000 | 1.000 | 1.000 |
| OUT_OF_SCOPE | 2 | 1.000 | 1.000 | 1.000 |

Confusion matrix, rows actual and columns predicted:

| Actual \ Predicted | ANSWERABLE | PARTIAL | INSUFFICIENT | OUT OF SCOPE |
| --- | ---: | ---: | ---: | ---: |
| ANSWERABLE | 10 | 0 | 0 | 0 |
| PARTIALLY_ANSWERABLE | 0 | 3 | 0 | 0 |
| INSUFFICIENT_EVIDENCE | 0 | 0 | 10 | 0 |
| OUT_OF_SCOPE | 0 | 0 | 0 | 2 |

Full abstention means a prediction of `INSUFFICIENT_EVIDENCE` or
`OUT_OF_SCOPE`. All 12 required development abstentions were predicted, with no
false abstentions.

## Error analysis and limitations

The finalized development evaluation has zero errors, so there are no
per-question error records. The machine-readable summary retains an empty
`errors` array, and the evaluator is prepared to record the structured parse,
evidence IDs, reasons, and a general failure category for every future error.

This perfect development result must not be read as an unbiased performance
estimate. The set is small and exposed, and its documented failure modes
informed the schema rules. The held-out set exists specifically to measure
generalization only after the full protocol is frozen.

Known limitations include a finite deterministic vocabulary, conservative
clause splitting, no broad coreference resolution, limited premise forms, no
causal data, no extrapolation, no reconstruction of non-aligned age predicates,
and no general compatibility reasoning beyond encoded corpus definitions. The
baseline assesses support structure; it does not produce prose, verify generated
claims, call an LLM, or select a similarity threshold.

## Reproduction

```powershell
python scripts/evaluate_answerability.py
python -m pytest
```

Outputs:

- `evaluation/results/development_answerability_report.jsonl`;
- `evaluation/results/development_answerability_summary.json`.

No new dependency is required.
