# Migrating to Another Host

Move the crawled corpus and Neo4j graph to a new VM or laptop. The git repo carries **code only**; data travels in a migration tarball.

## What moves

| Asset | In git? | How to move |
|-------|---------|-------------|
| Application code | yes | `git clone` |
| `capco_articles/` | no | Inside migration tarball |
| Neo4j database | no | `neo4j.dump` inside tarball |
| `.env` (secrets) | no | Secure copy separately |
| Embedding model (`EMBEDDING_MODEL_DIR`) | no | Copy directory or re-download |

Approximate sizes on a full corpus: articles ~130 MB, Neo4j dump ~100–300 MB compressed in the bundle.

## Source — export

```bash
./graph.sh stop
chmod +x scripts/migrate_export.sh scripts/migrate_import.sh
./scripts/migrate_export.sh
# → migration-export/capco-migration-<timestamp>.tgz
```

Then copy over a secure channel:

1. The migration `.tgz`
2. Your `.env`
3. The embedding model directory named by `EMBEDDING_MODEL_DIR`

## Target — prerequisites

- Docker Engine 24+ / Compose v2
- [uv](https://docs.astral.sh/uv/) and Node.js
- This repository checked out
- Open ports as needed: `7474`, `7687`, `8000`, `8080`

## Target — import

```bash
cp /secure/path/.env .env
# Set EMBEDDING_MODEL_DIR to the path on this machine

./scripts/migrate_import.sh /path/to/capco-migration-*.tgz

./graph.sh setup
./graph.sh start

uv run python ingest_graph.py --dry-run
```

`ingest_graph.py` stores **repo-relative** paths in `.graph_ingest_state.json`. Older absolute-path state is rewritten on first load.

## Password note

`NEO4J_PASSWORD` in `.env` is applied only when Neo4j starts against an **empty** volume. A restored dump keeps the password that was active when the dump was created. Use the same password on the target (or change it inside Neo4j after restore).

## Alternative: re-extract instead of dumping Neo4j

If you only copy `capco_articles/` (or restore articles from the tarball but skip Neo4j):

```bash
./graph.sh start
uv run python ingest_graph.py
```

This re-runs LLM extraction for articles not marked `completed` and consumes API quota.

## Smoke checks

```bash
./graph.sh status
curl -fsS http://127.0.0.1:8000/health
# UI:    http://localhost:8080
# Neo4j: http://localhost:7474
```
