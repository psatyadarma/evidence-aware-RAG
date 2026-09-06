# Product Prototype Guide

## Scope

The Evidence-Aware Singapore Population Assistant is a small public-sector AI prototype over three selected official Singapore population datasets. It uses the frozen System C sequence: deterministic interpretation and structured retrieval, deterministic evidence-sufficiency checking, deterministic computation, and grounded generation only when the evidence gate allows it.

It is not a general statistical service. It does not add datasets, forecast missing values, or infer causes from descriptive observations.

## Local installation

Python 3.11 is recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

Set `RAG_MODEL_API_KEY` in `.env` or the process environment. Never commit `.env`.

Start the UI:

```powershell
python -m streamlit run streamlit_app.py
```

Then open `http://localhost:8501`. The UI validates the corpus at startup but performs no hosted inference until a submitted question passes the deterministic evidence gate.

## CLI and offline smoke check

Ask one question through the same production pipeline:

```powershell
python -m app.cli "What was Singapore's resident population in 2024?"
python -m app.cli --json "Why did Singapore's total population change in 2024?"
```

The first command needs `RAG_MODEL_API_KEY` if the gate permits generation. A deterministic refusal can complete without a key.

Validate imports, assets, schema, structured interpretation/retrieval, answerability, and computation without provider access:

```powershell
python -m app.smoke
```

The smoke check deliberately stops before grounded generation and reports `provider_called: false`.

## Docker

Build and run on port 8501:

```powershell
docker build -t evidence-aware-rag .
docker run --rm -p 8501:8501 -e RAG_MODEL_API_KEY=$env:RAG_MODEL_API_KEY evidence-aware-rag
```

Run the offline smoke check in the built image:

```powershell
docker run --rm --entrypoint python evidence-aware-rag -m app.smoke
```

The image uses pinned `python:3.11.13-slim-bookworm`, installs only runtime dependencies, runs as the non-root `appuser`, and receives credentials only through environment variables.

### Verified local container behavior

The Docker image was successfully built and smoke-tested locally. Runtime validation, deterministic CLI behavior, Streamlit startup, health checks, and API-key-enabled runtime execution were verified inside the container.

The verification covered:

- creation of the `evidence-aware-rag:latest` image;
- the provider-free `python -m app.smoke` command;
- Streamlit startup and an `ok` response from `http://localhost:8501/_stcore/health`;
- deterministic refusal without an API key; and
- one supported, non-benchmark generation request with configuration injected using `--env-file .env`.

`.env` is ignored by Git and excluded from the Docker build context. It is supplied only when starting a container. No API credential is copied into or baked into the image, and the tracked `.env.example` contains placeholders only.

## Required runtime assets

`runtime-assets.json` is the executable manifest. Exactly these processed snapshots are packaged:

- `data/processed/population_indicators_annual.jsonl`
- `data/processed/residents_by_planning_region_annual.jsonl`
- `data/processed/resident_population_census_2020.jsonl`

Startup validates their size, SHA-256 checksum, Pydantic schema, unique evidence IDs, and expected 27,584 retrieval units. Application source, `streamlit_app.py`, and the dependency metadata are also required.

The selected System C does not use BGE. The Docker context therefore excludes raw download responses, `data/embeddings`, evaluation benchmarks/results, tests, development scripts, caches, and local secrets.

## Environment variables

| Variable | Purpose | Default |
| --- | --- | --- |
| `RAG_MODEL_API_KEY` | OpenAI API authentication for allowed generation routes | unset |
| `RAG_MODEL_PROVIDER` | Must remain `openai` for the frozen prototype | `openai` |
| `RAG_MODEL_NAME` | Must remain `gpt-4.1-mini-2025-04-14` | frozen snapshot |
| `RAG_MODEL_TIMEOUT_SECONDS` | Provider transport timeout | `60` |
| `RAG_MODEL_MAX_ATTEMPTS` | Bounded generation attempts | `2` |
| `RAG_MODEL_MAX_OUTPUT_TOKENS` | Structured generation budget | `1000` |
| `RAG_LOG_LEVEL` | Application log level | `INFO` |

The UI never accepts an API key in a page field. With no key, deterministic abstentions still work; a supported generation route returns setup guidance rather than a fabricated answer.

## Troubleshooting

- **Startup validation failed:** restore `runtime-assets.json` and all three processed files. Do not bypass checksum or schema errors.
- **API key not configured:** set `RAG_MODEL_API_KEY` in `.env`, the shell, or the container environment, then restart.
- **Provider unavailable or timed out:** no fallback answer is fabricated. Retry later and inspect sanitized application logs.
- **Model response could not be validated:** the bounded schema validation rejected all attempts, so no answer is displayed.
- **Question refused:** inspect “Why the system refused” and “How this answer was produced.” Rephrasing may help, but cannot expand the corpus.
- **Docker command unavailable:** install or start Docker Desktop/Engine before building.

## Logging and privacy

INFO logs contain status, whether generation ran, and evidence counts; they omit full user questions, answers, API keys, and environment secrets. Development exception logs may contain technical stack traces but must not be exposed in the UI. Questions that pass the deterministic gate are sent to the configured model provider as part of the grounded generation request.

## Known limitations

- Only three selected population/demographic sources are represented.
- Planning areas and subzones are Census-2020 snapshots; planning-region data begins in 2019.
- Descriptive data cannot establish causality or justify forecasts.
- Deterministic semantic parsing has incomplete coverage and can misinterpret unfamiliar measures, wording, or ranges.
- Conservative abstention is common, and partial-answer handling remains limited.
- Wider deployment requires additional schemas, accessibility/security review, monitoring, and user research.
- This is a prototype, not an authoritative statistical service.
