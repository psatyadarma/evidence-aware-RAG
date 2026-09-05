# Structured retrieval experiment

Checkpoint 6 asks whether the corpus's explicit schema is a stronger retrieval
signal than generic text similarity. The lexical and semantic implementations,
27,584 observation units, fixed benchmark, evidence IDs, and accepted result
files were left unchanged. The new method is deterministic, retrieval-only,
and independent of benchmark ground truth.

## Why test structure

The BGE experiment underperformed TF-IDF at every cutoff. The flattened corpus
contains thousands of near-duplicate observation sentences; the decisive facts
are often exact dimensions such as series, year, geographic level, age band,
and sex. A single dense query vector also has no mechanism for expanding a
predicate such as `65+` into six rows or an argmax into all five candidates.
This does not make semantic retrieval generally inferior. It motivates testing
whether exact schema interpretation is a better fit for this corpus.

## Typed query representation

`StructuredQuery` records:

- measure status (`resolved`, `unsupported`, or `unknown`) and an exact corpus
  series when resolved;
- ordered years;
- resolved geographic level, candidate entities, whether the level is
  ambiguous, and whether every entity at a level is requested;
- an age predicate (`total`, closed range, lower-bounded, or under a threshold)
  and the actual published bands satisfying it;
- ordered sex values;
- the requested operation and a separate descriptive evidence operation, so a
  causal request can retain `causal_explanation` while selecting its observable
  before/after rows;
- flags showing when age or sex was defaulted to a published total; and
- non-classifying diagnostics such as unsupported attribute, absent exact
  match, ambiguous geography, or unsupported causal evidence.

The representation contains question semantics only. It has no fields for
question identifiers, expected responses, labels, or expected evidence.

## Deterministic interpretation rules

All valid series, geography names, parent planning areas, and age labels are
derived from retrieval-unit metadata when the retriever is built.

1. Text matching is Unicode NFKC-normalized, case-folded, strips possessives,
   turns punctuation into spaces, and uses token-boundary phrase matching.
2. Four-digit years beginning `19` or `20` are retained in mention order.
   Two endpoints expand to every intervening year only when the wording says
   `each year`, `every year`, or `annually`; otherwise they stay endpoints.
3. Series names use longest exact normalized corpus-phrase matching. Generic
   aliases cover residents, permanent residents, non-residents, and citizens.
   A bare population request maps to the corpus's `Total Population` series.
4. Income, school quality/schools, education, and health terms are marked as
   unsupported attributes. Other unresolved measures remain `unknown`.
5. `female/women/girls` and `male/men/boys` map to the published sex values;
   `both sexes` and `all sexes` map to `Total`.
6. `largest`, `highest`, and `most` map to argmax. Difference and trend phrases,
   multiple entities/sexes/years, and multi-band ages determine the descriptive
   evidence operation. `why`, `cause`, and `reason` mark a causal request without
   claiming causal support.

These are generic language rules. They do not inspect the fixed questions or
their expected evidence.

## Geography resolution

The four levels are national, planning region, planning area, and subzone.
Explicit `planning region`, `planning area`, or `subzone` wording takes
precedence. Otherwise, exact place names are resolved through the metadata
registry. A planning-area filter requires a planning-area row and cannot admit
a same-named subzone. Subzones retain their parent planning area.

If a name exists at more than one level and no level is stated, all exact
candidate interpretations are exposed with `geography_ambiguous=true`; the
retriever does not silently select one. If the stated level conflicts with a
known name, retrieval returns no rows and records
`geography_level_name_mismatch`. An argmax over a stated level with no named
entity selects every entity at that level.

## Age predicates and total defaults

Published labels are parsed at startup. For example, `65 - 69 Years` becomes
the closed interval `[65, 69]`, while `90 Years & Over` becomes `[90, infinity)`.
An unrecognised corpus label fails construction rather than disappearing.

`65+`, `65 years and over`, and `at least 65` become a lower-bounded predicate.
A published band is selected only when it is wholly contained in the predicate;
closed-range endpoints are inclusive. Thus `65+` selects six bands from 65–69
through 90+, and `0–9` selects 0–4 and 5–9. A predicate that cannot be represented
exactly by whole published bands receives a diagnostic; partial cells are not
treated as exact evidence.

For resident-count questions at region, area, or subzone level, omitted age and
sex map to the source's published `Total` dimensions. This is a domain-level
aggregate default, not a question-specific shortcut. It does not apply to
national indicator series, non-count measures, or explicitly stated dimensions.

## Selection and ordering

Only exact metadata matches are returned. Comparisons return all operands,
differences and trends return requested available endpoints, sums return every
component age observation, and argmax returns every candidate observation. The
retriever never computes a sum, difference, winner, trend, premise truth, or
answer, and it never creates synthetic evidence.

Every exact match receives score `1.0`. Ordering is transparent and stable:
question year order, question geography-candidate order, canonical geography
name, resolved age-band order, question sex order, then evidence ID. A requested
year that is absent has no substitute. In a partly available multi-year request,
the available exact rows remain in the result set.

## Results

The same 13 `ANSWERABLE` or `PARTIALLY_ANSWERABLE` examples enter Recall@k.
The remaining 12 examples are logged with `null` Recall values.

| Method | R@1 | R@3 | R@5 | R@10 |
| --- | ---: | ---: | ---: | ---: |
| TF-IDF lexical | 0.154 | 0.282 | 0.359 | 0.410 |
| BGE semantic | 0.051 | 0.167 | 0.256 | 0.346 |
| Structured | 0.605 | 0.931 | 0.987 | 1.000 |

The full structured result-set recovery is `1.000`: every required observation
for all 13 positive examples is present before top-k truncation. R@1 is lower
than full recovery by design when a valid operation needs two, five, or six
observations.

### Positive results by category

| Category | N | R@1 | R@3 | R@5 | R@10 | Full recovery |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| direct_lookup | 3 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 |
| comparison | 3 | 0.400 | 0.867 | 1.000 | 1.000 | 1.000 |
| aggregation | 2 | 0.333 | 0.750 | 0.917 | 1.000 | 1.000 |
| descriptive_trend | 2 | 0.500 | 1.000 | 1.000 | 1.000 | 1.000 |
| partially_answerable | 3 | 0.667 | 1.000 | 1.000 | 1.000 | 1.000 |

The machine-readable report contains per-question parses, exact diagnostics,
selected IDs, ranks, Recall@k, and full recovery. The positive per-question full
recovery is 1.0 in all 13 cases.

## Failure analysis

The method fixes the observed planning-area/subzone confusion by filtering on
level, selects implicit total age and sex cells, requires exact years, keeps
resident and permanent-resident series distinct, expands 65+ into all six
published bands, returns all five region totals for argmax, and returns both
available trend endpoints. It also preserves observation IDs, unavailable
markers, and complete provenance.

It does not establish causality, determine whether a premise is true, calculate
numeric outputs, interpret an unavailable marker's reason, or make an
answerability decision. Vocabulary coverage remains deliberately small.
Non-aligned age predicates cannot be reconstructed from aggregated bands.
Ambiguous geographic names remain ambiguous unless a level is supplied. Missing
years are not imputed, nearest-neighbour substituted, or predicted. Requests
with too few constraints may return broad exact sets, ordered deterministically
but not semantically ranked.

## Diagnostic examples without positive scoring

| Behaviour | Examples | Structured result |
| --- | --- | --- |
| Causal request with descriptive rows | 3 unsupported-causality cases | Two exact observations each; causal diagnostic |
| Requested year absent | Tampines 2024; North Region 2018; Simei 2021 | No exact rows; no nearest-year substitution |
| Geographic temporal gap | Bedok annually, 2019–2025 | Only the exact 2020 census row available |
| False premise | National 2024–2025; Yishun vs Tampines | Both exact operands; no premise judgement |
| Unsupported attribute | Household income; school quality | No rows; unsupported attribute exposed |
| Published unavailable value | Changi Bay 2020 | Both exact ambiguous-level cells unless level is stated; `NOT_AVAILABLE` retained |

These outcomes are retrieval diagnostics only. Recall@k remains null for all 12
because this checkpoint does not reinterpret them as positive retrieval cases.

## Reproduction and outputs

```powershell
python scripts/evaluate_structured_retrieval.py
python -m pytest
```

The script writes:

- `evaluation/results/structured_retrieval_report.jsonl`;
- `evaluation/results/structured_retrieval_summary.json`; and
- `evaluation/results/three_way_retrieval_comparison.json`.

No dependency was added. The method uses the existing Pydantic contracts and
Python standard library, with no TF-IDF, embedding, vector database, LLM, or
network access.
