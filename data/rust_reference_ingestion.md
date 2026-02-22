# Rust Reference SQLite Ingestion Notes

## Scope

- Database: `rust_reference.sqlite`
- Rank: 1 (sequential SQLite KB execution)
- Source: `https://github.com/rust-lang/reference.git`

## Build Command

```bash
uv run python scripts/sqlite_build_rust_reference.py
```

Optional pinned revision:

```bash
uv run python scripts/sqlite_build_rust_reference.py --reference-revision <commit-sha>
```

Optional semantic retrieval metadata profile:

```bash
uv run python scripts/sqlite_build_rust_reference.py \
  --retrieval-mode hybrid \
  --embedding-model-id Qwen/Qwen3-Embedding-4B \
  --reranker-model-id BAAI/bge-reranker-v2-m3
```

## Schema Migration Command

```bash
uv run python scripts/sqlite_migrate_rust_reference_schema.py
```

## Validation Command

```bash
uv run python scripts/sqlite_validate_rust_reference.py
```

## Smoke Command

```bash
uv run python scripts/sqlite_smoke_rust_reference.py
```

## Retrieval Evaluation Suite

Retrieval prompts and relevance judgments are stored in:

- `data/query_testsets/rust_reference_table1_retrieval_eval.yaml`

Run lexical/semantic/hybrid retrieval evaluation:

```bash
uv run python scripts/sqlite_eval_rust_reference_retrieval.py
```

Auto-start local backend during evaluation if unavailable:

```bash
uv run python scripts/sqlite_eval_rust_reference_retrieval.py --auto-start-local-backend
```

Run evaluation without enforcing numeric gates (diagnostics only):

```bash
uv run python scripts/sqlite_eval_rust_reference_retrieval.py --no-enforce-gates
```

Run interactive lexical/semantic/hybrid retrieval queries:

```bash
uv run python scripts/sqlite_query_rust_reference.py \
  --mode lexical \
  --query-text "What kinds of defensive programming are available in Rust?"
```

```bash
uv run python scripts/sqlite_query_rust_reference.py \
  --mode semantic \
  --query-text "How does Rust mitigate defensive error-handling risks?"
```

```bash
uv run python scripts/sqlite_query_rust_reference.py \
  --mode hybrid \
  --query-text "How can Table 1 defensive techniques be supported by Rust language features?"
```

When score breakdown is enabled (default), row projection output now includes
`evidence_trace` entries per row marker with `statement_id`, `source_anchor`, and
per-hit `contribution` values for explainability.

Hide score component fields from CLI output:

```bash
uv run python scripts/sqlite_query_rust_reference.py \
  --mode lexical \
  --query-text "defensive error handling" \
  --no-include-score-breakdown
```

Persist a full query-response review artifact to a specific file:

```bash
uv run python scripts/sqlite_query_rust_reference.py \
  --mode hybrid \
  --query-text "How should Rust code handle defensive error paths safely?" \
  --prompt-id RET-RESOLVE-001 \
  --save-response-path .cache/sqlite_kb/reports/rust_reference/query_reviews/ret-resolve-001-hybrid.json
```

Persist a full query-response review artifact into a directory with deterministic
`<timestamp>__<prompt_id>__<mode>.json` naming:

```bash
uv run python scripts/sqlite_query_rust_reference.py \
  --mode hybrid \
  --query-text "Which Rust constraints help avoid data races?" \
  --prompt-id RET-RESOLVE-005 \
  --save-response-dir .cache/sqlite_kb/reports/rust_reference/query_reviews
```

Capture a prompt-pack review bundle (`manifest.json` + one response file per
prompt/mode) for manual quality inspection:

```bash
uv run python scripts/sqlite_capture_rust_reference_query_reviews.py \
  --prompts-path data/query_testsets/rust_reference_table1_retrieval_eval.yaml \
  --prompt-ids RET-RESOLVE-001,RET-RESOLVE-002,RET-RESOLVE-003,RET-RESOLVE-004,RET-RESOLVE-005 \
  --modes hybrid \
  --bundle-id five-query-hybrid
```

Review bundle output under
`.cache/sqlite_kb/reports/rust_reference/query_reviews/<bundle-id>/` and inspect
results with `jq` (for example `jq '.query,.response.rows[:3]' <artifact.json>`).

Check semantic backend health before semantic/hybrid runs:

```bash
uv run python scripts/sqlite_check_semantic_backend.py \
  --embed-base-url http://127.0.0.1:8080 \
  --rerank-base-url http://127.0.0.1:8081 \
  --embed-model Qwen/Qwen3-Embedding-4B \
  --rerank-model BAAI/bge-reranker-v2-m3
```

Install optional local semantic runtime dependencies:

```bash
uv sync --extra semantic-local
```

Start local embedding+reranker services on loopback (python-local default):

```bash
uv run python scripts/sqlite_local_semantic_backend.py start \
  --embed-base-url http://127.0.0.1:8080 \
  --rerank-base-url http://127.0.0.1:8081 \
  --embed-model-id Qwen/Qwen3-Embedding-4B \
  --rerank-model-id BAAI/bge-reranker-v2-m3
```

For cold starts of large models, increase startup timeout explicitly:

```bash
uv run python scripts/sqlite_local_semantic_backend.py start \
  --startup-timeout-sec 420
```

Model cache directory used by local semantic backend:

```bash
uv run python scripts/sqlite_local_semantic_backend.py start \
  --model-cache-dir .cache/sqlite_kb/models/hf
```

Optional environment variable for cache path:

```bash
export RUST_REF_SEMANTIC_MODEL_CACHE_DIR=.cache/sqlite_kb/models/hf
export RUST_REF_SEMANTIC_TIMEOUT_SEC=60
export RUST_REF_LOCAL_BACKEND_ENGINE=python
export RUST_REF_SEMANTIC_BACKEND_PROFILE=python-local

# for CPU-only cold runs of Qwen3-Embedding-4B, increase timeout as needed
# export RUST_REF_SEMANTIC_TIMEOUT_SEC=240
```

Python-local is the default backend engine. Optional Docker fallback:

```bash
uv run python scripts/sqlite_local_semantic_backend.py start \
  --engine docker \
  --image ghcr.io/huggingface/text-embeddings-inference:cpu-latest
```

Stop local semantic backend services:

```bash
uv run python scripts/sqlite_local_semantic_backend.py stop
```

Backend troubleshooting:

```bash
# container/process status
uv run python scripts/sqlite_local_semantic_backend.py status

# endpoint/model preflight details
uv run python scripts/sqlite_check_semantic_backend.py \
  --embed-base-url http://127.0.0.1:8080 \
  --rerank-base-url http://127.0.0.1:8081
```

Materialize statement embeddings (recommended before semantic/hybrid eval):

```bash
uv run python scripts/sqlite_materialize_rust_reference_embeddings.py \
  --semantic-base-url http://127.0.0.1:8080 \
  --semantic-embed-base-url http://127.0.0.1:8080 \
  --semantic-rerank-base-url http://127.0.0.1:8081 \
  --embed-model-id Qwen/Qwen3-Embedding-4B
```

Materialization is full-corpus by default and fails if corpus coverage or cached
embedding coverage for the active model is below `COUNT(statements)`.
Long-running jobs emit JSONL checkpoints by default under
`.cache/sqlite_kb/reports/rust_reference/materialize_progress_<UTC>.jsonl`.
You can override path/interval:

```bash
uv run python scripts/sqlite_materialize_rust_reference_embeddings.py \
  --progress-log-path .cache/sqlite_kb/reports/rust_reference/materialize_progress_live.jsonl \
  --progress-interval-sec 30
```

For local scoped experiments only, you can bypass this strict parity check:

```bash
uv run python scripts/sqlite_materialize_rust_reference_embeddings.py \
  --row-marker 1a \
  --allow-partial-corpus
```

By default, semantic/hybrid query paths are materialize-first and fail with
`SEMANTIC_INDEX_INCOMPLETE` when embeddings are missing for the active model.
For local experimentation only, you can opt in to online corpus embedding:

```bash
uv run python scripts/sqlite_query_rust_reference.py \
  --mode semantic \
  --query-text "How does Rust support defensive programming?" \
  --allow-online-corpus-embedding
```

## Timestamping

- Snapshot timestamp is stored in `snapshots.fetched_at`.
- Document-level timestamp is stored in `source_documents.source_fetched_at`.
- Section and statement timestamps are stored in `sections.source_fetched_at` and `statements.source_fetched_at`.
- Table 1 row rationale timestamp is stored in `row_verdicts.rationale_timestamp`.
- Query-suite reports are written under `.cache/sqlite_kb/reports/rust_reference/`.

## CI Lane Commands

- Fast PR lane (lint + unit + smoke):

```bash
uv run python scripts/sqlite_ci_retrieval_pr_fast.py
```

- Semantic lane (backend preflight + embedding materialization + retrieval eval):

```bash
uv run python scripts/sqlite_ci_retrieval_semantic.py \
  --semantic-embed-base-url http://127.0.0.1:8080 \
  --semantic-rerank-base-url http://127.0.0.1:8081
```

The semantic lane auto-starts local backend services if preflight fails. Use
`--no-auto-start-local-backend` to require pre-existing backend processes.

By default, semantic lane auto-start uses `--local-backend-engine python`.
Set `--local-backend-engine docker` for explicit Docker fallback.

- Nightly full lane (fast checks + full semantic/hybrid lane):

```bash
uv run python scripts/sqlite_ci_retrieval_nightly_full.py
```
