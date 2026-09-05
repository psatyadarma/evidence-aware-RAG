# Semantic retrieval experiment

Checkpoint 5 compares one small semantic retriever with the frozen cosine
TF-IDF baseline. Both methods use exactly the same 27,584 observation-level
retrieval units, human-readable text, evidence IDs, corpus, benchmark, positive
evaluation cohort, and Recall@k implementation. No benchmark labels or lexical
retrieval behavior were changed.

## Model selection

The sole model is
[`BAAI/bge-small-en-v1.5`](https://huggingface.co/BAAI/bge-small-en-v1.5),
pinned to revision
`5c38ec7c405ec4b44b94cc5a9bb96e735b38267a`.

It was selected because it is an English, retrieval-oriented sentence embedding
model small enough for local CPU use. The published model has 33.4 million
parameters, a 133 MB safetensors weight file, 384-dimensional output, and an MIT
license. A small local model avoids sending government data or benchmark
questions to an external inference API and keeps this prototype reproducible.

The official retrieval instruction is applied to questions only:

```text
Represent this sentence for searching relevant passages: {question}
```

Retrieval-unit text is embedded unchanged and receives no prefix, following the
model publisher's recommendation. Both document and query vectors are
L2-normalized. Exact dot product is therefore cosine similarity.

## Dependencies

Two direct dependencies were added and pinned:

- `numpy==2.0.2` for the float32 matrix, normalization, exact dot products, and
  deterministic ranking indices;
- `sentence-transformers==3.4.1` for loading and encoding with the pinned model.

Important installed transitive dependencies include CPU PyTorch 2.8.0,
Transformers 4.57.6, Hugging Face Hub 0.36.2, tokenizers 0.22.2,
safetensors 0.7.0, SciPy 1.13.1, and scikit-learn 1.6.1. These are not declared
again because sentence-transformers owns them. No vector database, FAISS,
LangChain, or LlamaIndex is used.

## Artifact design and validation

The default artifact directory is
`data/embeddings/bge-small-en-v1.5/` and contains:

- `embeddings.npy`: `(27584, 384)` normalized float32 matrix;
- `evidence_ids.json`: evidence IDs in matrix-row order;
- `metadata.json`: strict schema version, exact model revision, encoder version,
  query/document formatting, normalization and dtype, dimension and count,
  build timestamp/duration/device, and SHA-256 values.

There are separate hashes for the complete retrieval-unit corpus, the exact
`evidence ID + searchable text` sequence, the matrix file, and the evidence-ID
file. Loading fails before retrieval if the model, revision, query format,
corpus, text, count, dimension, dtype, IDs, file integrity, finite-value check,
or unit-vector check differs.

The generated artifact is deliberately ignored by Git. It is 44,338,732 bytes
(about 42.3 MiB), while the raw matrix occupies 42,369,024 bytes (about
40.4 MiB) in memory. Committing a readily reproducible binary would add
repository weight without adding reviewable source or ground truth. The pinned
build command and validation metadata are retained instead.

## Reproduction

From a clean environment after installing the project:

```powershell
python scripts/download_data.py
python scripts/build_embeddings.py
python scripts/evaluate_lexical_retrieval.py
python scripts/evaluate_semantic_retrieval.py
```

`build_embeddings.py` downloads only the pinned model revision when it is not
already cached. If a valid current artifact exists, it is checksum-validated
and reused; `--force` performs an intentional rebuild. Unit tests use fake
vectors and never download the real model. Semantic evaluation uses the local
model cache only, so an omitted build step fails instead of silently fetching
state.

## Measured resource footprint

Measurements were collected on Windows build 26100, Python 3.9.4, CPU-only
execution on an Intel64 Family 6 Model 142 processor, with NumPy 2.0.2 and
sentence-transformers 3.4.1.

| Measurement | Value |
| --- | ---: |
| Corpus vectors | 27,584 |
| Dimensions | 384 |
| Matrix memory | 42,369,024 bytes (40.4 MiB) |
| Complete artifact | 44,338,732 bytes (42.3 MiB) |
| Corpus encoding + artifact write | 769.1 s |
| Mean warm query embedding + exact top-10 retrieval | 32.7 ms |

The query measurement averages the fixed 25 questions after one warm-up. It
excludes model loading and artifact checksum validation and is a basic local
observation, not a portable performance guarantee. The build duration likewise
starts after model loading, so it excludes the one-time download and load.

## Lexical versus semantic results

Only the same 13 `ANSWERABLE` or `PARTIALLY_ANSWERABLE` examples enter the
positive-evidence means. Recall remains undefined for the other 12 examples.

| Method | Recall@1 | Recall@3 | Recall@5 | Recall@10 |
| --- | ---: | ---: | ---: | ---: |
| TF-IDF lexical | 0.154 | 0.282 | 0.359 | 0.410 |
| BGE semantic | 0.051 | 0.167 | 0.256 | 0.346 |

The semantic retriever is worse at every measured cutoff on this benchmark.
This result does not show that embeddings are generally inferior; it shows that
this single untuned dense model over these flat observation texts does not beat
the lexical baseline.

### Results by category

| Category | N | Method | R@1 | R@3 | R@5 | R@10 |
| --- | ---: | --- | ---: | ---: | ---: | ---: |
| direct_lookup | 3 | Lexical | 0.333 | 0.333 | 0.667 | 0.667 |
|  |  | Semantic | 0.000 | 0.333 | 0.333 | 0.333 |
| comparison | 3 | Lexical | 0.167 | 0.333 | 0.333 | 0.333 |
|  |  | Semantic | 0.000 | 0.167 | 0.333 | 0.333 |
| aggregation | 2 | Lexical | 0.000 | 0.083 | 0.083 | 0.167 |
|  |  | Semantic | 0.083 | 0.083 | 0.167 | 0.250 |
| descriptive_trend | 2 | Lexical | 0.250 | 0.250 | 0.250 | 0.250 |
|  |  | Semantic | 0.250 | 0.250 | 0.500 | 0.500 |
| partially_answerable | 3 | Lexical | 0.000 | 0.333 | 0.333 | 0.500 |
|  |  | Semantic | 0.000 | 0.000 | 0.000 | 0.333 |

## Failure comparison

Semantic retrieval improved two important cases at Recall@10:

- `q007`, the Central Region 65-and-over aggregation, improved from 2/6 to 3/6
  required bands. The 90+, 65–69, and 75–79 observations ranked 1, 4, and 7.
  The remaining three bands were still absent, so embeddings did not solve
  exhaustive age expansion.
- `q010`, the North-East Region 2021–2025 trend, improved from 0/2 to 1/2. The
  correct 2021 total ranked first, but the 2025 endpoint was absent from the top
  ten.

Important failures remained or regressed:

- **Implicit totals and geographic hierarchy:** the Tampines planning-area total
  in `q002` remained absent. Subzone observations occupied the top ten. The five
  planning-region totals in `q005` and both planning-area totals in `q006` also
  remained absent.
- **Age semantics:** `q008` still retrieved neither required 0–4 nor 5–9
  planning-area female cell. It returned Ang Mo Kio subzone cells from unrelated
  age bands. `q007` improved only partially.
- **Temporal precision:** the correct 2025 total population in `q001` moved from
  rank 1 to rank 3 behind 2021 and 2022. The two `q004` endpoints moved from
  ranks 1/2 to 3/5. In `q009`, semantic retrieval found 2020 but lost 2025,
  leaving Recall@10 unchanged at 1/2.
- **Similar series:** permanent-resident observations often outranked resident
  population observations. In `q011`, the required rows moved from ranks 2/3 to
  8/10.
- **Regressions:** `q003` lost its previously rank-5 planning-area observation,
  and `q013` lost its previously rank-7 West Region observation.
- **Aggregation:** although the category improved from 0.167 to 0.250 at
  Recall@10, only three of eight required observations across its two questions
  were recovered. Semantic similarity alone is not a substitute for structured
  range expansion or query decomposition.

## Questions without positive evidence

The 10 `INSUFFICIENT_EVIDENCE` and 2 `OUT_OF_SCOPE` examples are logged but not
scored. Their Recall@k values are `null`.

- `q014` retrieved the 2021 and 2020 resident-population observations at ranks 1
  and 4. They establish the values but do not explain why the population fell.
- `q015` retrieved the 2021 Central Region 0–4 observation at rank 1 but not the
  2025 endpoint and, in either case, no causal explanation.
- `q018` asks for unavailable 2018 regional data; the nearest 2019 total ranked
  first despite not answering the requested year.
- `q023` asks for household income but returns Tampines population subzone rows.
- `q024` asks about school quality but still returns planning-area population
  rows.
- `q025` returns many Changi Bay age-band rows, but not the requested unavailable
  total row in the top ten; none supplies a resident count.

These cases reinforce that a high dense similarity score signals topical or
linguistic relatedness, not evidence sufficiency or answerability.

## Outputs and limitations

Machine-readable outputs are:

- `evaluation/results/semantic_retrieval_report.jsonl` for every question;
- `evaluation/results/semantic_retrieval_summary.json` for aggregates and
  resource measurements;
- `evaluation/results/retrieval_comparison.json` for the overall side-by-side
  result.

The experiment covers one small embedding model, one fixed text representation,
one query instruction, and a small benchmark. It does not test alternate models,
hybrid retrieval, metadata filtering, query decomposition, age expansion,
reranking, thresholds, answerability, or generation. Absolute similarity values
are not interpreted as confidence or sufficiency.
