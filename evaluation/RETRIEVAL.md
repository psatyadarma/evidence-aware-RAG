# Deterministic lexical retrieval baseline

Checkpoint 5's controlled semantic comparison is documented separately in
[`SEMANTIC_RETRIEVAL.md`](SEMANTIC_RETRIEVAL.md). This file preserves the frozen
Checkpoint 4 lexical design and results.

This checkpoint asks a deliberately narrow question: how reliably can an
untuned lexical retriever recover the observation IDs required by the fixed
benchmark? It does not assess answer correctness, answerability classification,
or generation.

## Retrieval-unit design

One retrieval unit represents exactly one normalized observation. The corpus is
not pre-aggregated for benchmark questions, so an aggregation over six age bands
still requires retrieval of six units. This preserves the meaning of the
observation-level evidence IDs fixed in the benchmark.

Each unit contains:

- the existing deterministic evidence ID;
- source and upstream record IDs;
- year, numeric value, raw published value, and availability status;
- merged record- and observation-level dimensions, including geography, age,
  sex, or series where present;
- the complete source provenance object; and
- deterministic searchable text made from dataset title, publisher, year,
  dimensions, and published value or unavailable marker.

For example, a unit is rendered in this form:

```text
Dataset: Singapore Residents By Planning Region, Age Group And Sex, End June, Annual.
Publisher: Singapore Department of Statistics (SingStat).
Year: 2025. Planning region: Central Region. Sex: Total.
Age group: 65 - 69 Years. Published value: 82880.
```

The committed corpus produces 27,584 units:

| Source | Units |
| --- | ---: |
| Indicators On Population, Annual | 2,204 |
| Singapore Residents By Planning Region, Age Group And Sex, End June, Annual | 2,100 |
| Resident Population by Planning Area/Subzone, Age Group and Sex (Census 2020) | 23,280 |
| **Total** | **27,584** |

Observed and `NOT_AVAILABLE` cells are both retained. An unavailable source cell
is evidence that a value was not published; it is not converted to zero.

## Lexical method

The baseline is cosine similarity over raw term-frequency vectors weighted by
smoothed inverse document frequency:

```text
idf(term) = log((document_count + 1) / (document_frequency + 1)) + 1
weight(term, document) = term_frequency * idf(term)
score(query, document) = cosine(query_vector, document_vector)
```

The implementation uses only the Python standard library. TF-IDF was selected
because it is transparent, deterministic, and gives rare years and place names
more influence than unweighted token overlap. It is an intentionally simple
baseline: there are no synonyms, field weights, query expansion, learned
parameters, or benchmark-specific rules. Equal scores are ordered by evidence
ID.

The retrieval interface is represented by a `Retriever` protocol whose only
operation is `retrieve(question, k)`. Callers receive ranked `RetrievalResult`
objects, and the TF-IDF implementation is only one conforming retriever.

## Normalization

Normalization is fixed and dependency-free:

- Unicode NFKC normalization and case folding;
- removal of commas between digits, so `2,025` becomes `2025`;
- removal of possessive endings;
- replacement of `&` with `and`;
- punctuation and hyphens used as token boundaries;
- integers, decimal numbers, year tokens, and both endpoints of age ranges
  retained; and
- conservative singularization of alphabetic `-s` and `-ies` endings, with
  explicit exceptions such as `census`, `series`, and `singapore`.

No stemming library, stop-word list, synonym table, or opaque NLP pipeline is
used.

## Evaluation protocol

For one example:

```text
Recall@k = unique required observation IDs in the top k
           / unique required observation IDs
```

For multi-evidence questions, partial recovery therefore receives fractional
credit. The overall and category results are arithmetic means across the 10
`ANSWERABLE` and 3 `PARTIALLY_ANSWERABLE` examples only. The remaining 10
`INSUFFICIENT_EVIDENCE` and 2 `OUT_OF_SCOPE` examples have no positive-evidence
retrieval score: their Recall@k fields are `null`. Their top ten results and the
ranks of any benchmark boundary references are still logged for diagnostic
inspection.

Run the fixed evaluation offline with:

```powershell
python scripts/evaluate_lexical_retrieval.py
```

The script validates the committed processed corpus and benchmark, rebuilds the
index, and atomically rewrites the detailed JSONL report and JSON summary.

## Baseline results

The following are measured results from the committed 25-question benchmark and
27,584-unit corpus. Thirteen evidence-bearing answerable or partially answerable
examples enter the means.

| Slice (positive examples only) | N | Recall@1 | Recall@3 | Recall@5 | Recall@10 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Overall | 13 | 0.154 | 0.282 | 0.359 | 0.410 |
| direct_lookup | 3 | 0.333 | 0.333 | 0.667 | 0.667 |
| comparison | 3 | 0.167 | 0.333 | 0.333 | 0.333 |
| aggregation | 2 | 0.000 | 0.083 | 0.083 | 0.167 |
| descriptive_trend | 2 | 0.250 | 0.250 | 0.250 | 0.250 |
| partially_answerable | 3 | 0.000 | 0.333 | 0.333 | 0.500 |

These figures are a baseline, not evidence that the system can answer 41% of
questions. Recall@10 is the mean fraction of required observation IDs retrieved,
and generation was not attempted.

## Representative successes and failures

- `q001` retrieves 2025 total population at rank 1.
- `q004` retrieves both 2024 and 2025 permanent-resident observations at ranks
  1 and 2, giving full Recall@3.
- `q011` retrieves both supported annual observations at ranks 2 and 3 even
  though the requested causal explanation is not supported.
- `q003` finds the requested Ang Mo Kio planning-area, female, 90-and-over cell
  at rank 5; a similarly worded subzone cell ranks first.
- Planning-area totals in `q002`, `q006`, and `q008` are crowded out by subzone
  and age/sex cells that share more surface tokens. The baseline has no concept
  of geographic hierarchy or the implicit request for `Total` dimensions.
- The five-region argmax in `q005` retrieves none of the five total cells in the
  top ten. Words such as “largest” do not select totals, while rare age-band
  terms can dominate cosine similarity.
- The six-band 65-and-over aggregation in `q007` recovers only two required age
  bands by rank 10. A flat top-k ranking cannot infer and exhaustively enumerate
  the component bands implied by “65 years and over.”
- In `q009`, the 2025 endpoint ranks first but the 2020 endpoint is crowded out.
  The token `2020` appears throughout the large census source and consequently
  carries less IDF weight than `2025`.

These are observed error modes, not reasons to relabel the benchmark or add
question-specific rules.

## Unsupported and out-of-scope behavior

The diagnostic rows demonstrate why relevance is not sufficiency:

- `q014` asks why resident population fell. A 2021 resident-population
  observation ranks second, but no retrieved observation establishes a cause.
- `q023` asks about household income in Tampines. The retriever returns Tampines
  population cells because the place and year overlap; the corpus contains no
  household-income attribute.
- `q024` asks about the “best schools.” Generic planning-area and year terms
  still produce population results, despite the corpus containing no school
  quality evidence.
- `q025` returns nearby unavailable Changi Bay subzone cells, while the referenced
  planning-area total is not in the top ten. Even an exact topical match would
  establish only that the published value is unavailable, not a resident count.

No threshold or answerability decision is inferred from these scores in this
checkpoint.

## Known weaknesses

Cosine TF-IDF treats the searchable representation as an unstructured bag of
words. It does not understand geography levels, dimension defaults, temporal
endpoint completeness, age-range composition, comparisons, causal language, or
whether an attribute is absent from the corpus. The repeated dataset and
publisher boilerplate also contributes to document length and similarity. These
limitations make it a useful transparent reference point for a later semantic
retrieval experiment, not a production retriever.
