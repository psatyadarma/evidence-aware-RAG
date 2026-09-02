# Evidence-Aware RAG for Singapore Population Data

This project investigates whether explicit evidence-sufficiency checking reduces unsupported answers compared with vanilla retrieval-augmented generation (RAG) and retrieval-score thresholding. It is an experimentally evaluated system, not a general-purpose chatbot.

The initial domain is Singapore population and demographic statistics from a small, documented collection of official sources. The system will answer only from retained evidence and will make abstention behaviour measurable.

## Planned comparison

1. **Vanilla RAG** retrieves evidence and generates a cited answer.
2. **Threshold RAG** abstains when a configurable retrieval score does not pass a threshold.
3. **Evidence-aware RAG** decomposes the requested answer into required claims, checks each claim against retrieved evidence, and returns `ANSWERABLE`, `PARTIALLY_ANSWERABLE`, `INSUFFICIENT_EVIDENCE`, or `OUT_OF_SCOPE` before generation.

## Smallest viable architecture

`app/models.py` contains strict domain contracts shared across all stages. `app/config.py` owns environment configuration. The remaining modules are deliberately empty boundaries for ingestion, retrieval, answerability, generation, optional verification, API delivery, and pipeline orchestration. Evaluation is isolated in `evaluation/` so system behaviour and metrics can evolve independently of the demo interface.

The intended dependency direction is:

`source data -> ingestion -> evidence -> retrieval -> answerability -> generation -> optional verification`

All three experimental systems will share the same corpus, retriever, generation interface, and benchmark wherever possible. This isolates the abstention strategy as the main experimental variable.

## Dependency decisions at this checkpoint

- **Pydantic 2** validates structured evidence, decisions, answers, and benchmark records. Strict schemas make malformed model responses fail visibly.
- **pydantic-settings** provides typed, prefixed environment configuration and `.env` support without custom parsing.
- **pytest** tests project-owned validation and configuration behaviour.

FastAPI, an LLM SDK, an embedding model, a vector index, pandas, and Docker runtime dependencies are intentionally deferred until their corresponding implementation phase. This avoids choosing infrastructure before corpus size and source format are known. A simple in-memory or NumPy-backed exact search should be evaluated before adding FAISS or Chroma; for a small corpus, exact search is easier to inspect and reproduce.

## Current status

Checkpoint 1 defines the package skeleton, domain schemas, and configuration only. It does **not** download data, retrieve documents, call an LLM, expose an API, contain a benchmark, or report experimental results.

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

## Next checkpoint

Select and document a small set of official Singapore population sources, implement reproducible ingestion into the `Document` and `Evidence` schemas, and add provenance-focused ingestion tests. Dataset download should begin only after the proposed sources and licences are reviewed.

## Documentation still to be completed

Later checkpoints will add data provenance and licensing, benchmark construction, evaluation methodology, verified experimental results, error analysis, limitations, Docker instructions, production considerations, discarded approaches, and disclosure of coding-assistant use. No result values will be added until experiments are run.
