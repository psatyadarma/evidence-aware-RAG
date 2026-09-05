# Evidence-Aware RAG for Singapore Population Data

This project investigates whether explicit evidence-sufficiency checking reduces unsupported answers compared with vanilla retrieval-augmented generation (RAG) and retrieval-score thresholding. It is an experimentally evaluated system, not a general-purpose chatbot.

The initial domain is Singapore population and demographic statistics from a small, documented collection of official sources. The system answers only from retained evidence and makes abstention behaviour measurable.

## Frozen comparison

1. **Ungated RAG** sends frozen structured-retrieval evidence to the shared baseline generator.
2. **Threshold RAG** applies one development-selected maximum BGE-cosine threshold to that same evidence before the same generator.
3. **Evidence-aware RAG** uses the frozen deterministic sufficiency gate, deterministic computation, and grounded-generation v2.

## Smallest viable architecture

`app/models.py` contains strict domain contracts shared across all stages. `app/config.py` owns environment configuration. Deterministic ingestion, three retrieval methods, the frozen deterministic answerability gate, deterministic computation, and schema-constrained grounded answer generation are implemented. The earlier LLM evidence-sufficiency classifiers remain frozen experiments and do not control the selected pipeline. API delivery, optional verification, and system comparisons remain deferred. Evaluation is isolated in `evaluation/` so system behaviour and metrics can evolve independently of the demo interface.

The intended dependency direction is:

`source data -> ingestion -> evidence -> retrieval -> answerability -> generation -> optional verification`

All three experimental systems share the same corpus and structured answer-evidence retriever. This is a complete-policy comparison: System C also includes deterministic computation and its evidence-aware generator, so gate behavior is reported separately rather than claiming a one-variable experiment.

## Dependency decisions at this checkpoint

- **Pydantic 2** validates structured evidence, decisions, answers, and benchmark records. Strict schemas make malformed model responses fail visibly.
- **pydantic-settings** provides typed, prefixed environment configuration and `.env` support without custom parsing.
- **pytest** tests project-owned validation and configuration behaviour.
- **NumPy 2.0.2** stores normalized float32 embeddings and performs exact dot-product retrieval.
- **sentence-transformers 3.4.1** loads the single pinned local embedding model and owns its PyTorch/Transformers dependencies.

FastAPI, an LLM SDK, a vector database, pandas, and Docker runtime dependencies remain deferred. The Checkpoint 9 OpenAI adapter uses the Python standard library plus the existing Pydantic contract, so it adds no dependency. Exact in-memory NumPy search is sufficient for the 27,584-unit corpus, so FAISS and Chroma are unnecessary here.

## Current status

Checkpoint 11 freezes the final comparison protocol. Checkpoint 10 selects the frozen deterministic answerability classifier as the
gate, computes lookup/difference/comparison/sum/argmax/trend facts without an
LLM, and sends only `ANSWERABLE` or `PARTIALLY_ANSWERABLE` cases to a grounded
generator. Full abstentions never call the model. On the exposed 25-question
development set, the final authorized prompt produced 13/13 valid answers, the
gate avoided 12 calls, deterministic computation was 13/13, and the manual
claim audit found no unsupported claims. These are development results, not an
unbiased estimate; the separate frozen 45-question benchmark has not been
evaluated.

Checkpoint 6's accepted structured Recall@1/3/5/10 of
0.605/0.931/0.987/1.000, and all lexical and semantic measurements, are now
explicitly development-set results.
See [`data/README.md`](data/README.md) for the knowledge boundary,
[`evaluation/README.md`](evaluation/README.md) for benchmark definitions,
[`evaluation/RETRIEVAL.md`](evaluation/RETRIEVAL.md) for the frozen lexical
baseline, [`evaluation/SEMANTIC_RETRIEVAL.md`](evaluation/SEMANTIC_RETRIEVAL.md)
for the semantic experiment, and
[`evaluation/STRUCTURED_RETRIEVAL.md`](evaluation/STRUCTURED_RETRIEVAL.md) for
the structured method and three-way development analysis, and
[`evaluation/HELDOUT_BENCHMARK.md`](evaluation/HELDOUT_BENCHMARK.md) for the
frozen benchmark split. The answerability protocol and deterministic
development results are in
[`evaluation/ANSWERABILITY.md`](evaluation/ANSWERABILITY.md). The LLM
classifier design and live-evaluation status are in
[`evaluation/LLM_ANSWERABILITY.md`](evaluation/LLM_ANSWERABILITY.md). The final
v2 contract repair and v1/v2 development comparison are in
[`evaluation/LLM_ANSWERABILITY_V2.md`](evaluation/LLM_ANSWERABILITY_V2.md).
The selected gated computation/generation path and dev-only audit are in
[`evaluation/GROUNDED_GENERATION.md`](evaluation/GROUNDED_GENERATION.md).
The controlled A/B/C development comparison, threshold, metrics, artifact
hashes, and one-shot policy are preregistered in
[`evaluation/FINAL_EVALUATION_PROTOCOL.md`](evaluation/FINAL_EVALUATION_PROTOCOL.md).

The project still does **not** implement hybrid retrieval, an API, automated
free-text judging, held-out evaluation, or a UI. The frozen Checkpoint 9 LLM classifier
v2 remains at 0.720 development accuracy; it is not the selected gate.

## Local setup and tests

Python 3.9 or newer is required.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest
```

Copy `.env.example` to `.env` for local configuration. Secrets belong only in `.env` or process environment variables and must not be committed.
The retrieval threshold has no default because its scale depends on the eventual retriever; evaluation will sweep explicitly configured values rather than rely on an unexplained constant.

## Refreshing the reviewed data snapshot

```bash
python scripts/download_data.py
```

Cached raw responses are checksum-verified and reused. Pass `--force` only for an intentional upstream refresh; schema changes fail loudly and require review.

## Next checkpoint

After protocol review, execute the dedicated final command exactly once on the
reserved benchmark and report the observed results without tuning or manual
prediction correction.

## Documentation still to be completed

Later checkpoints will add answerability and generation experiments, Docker
instructions, production considerations, discarded approaches, and disclosure
of coding-assistant use. Only actually executed experiments will be reported.
