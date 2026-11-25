# Capco Intelligence Crawler

Crawl [Capco Intelligence](https://www.capco.com/intelligence/capco-intelligence) into local Markdown/JSON/PDF files, then extract a Neo4j knowledge graph with [llm-graph-builder](https://github.com/neo4j-labs/llm-graph-builder) and an OpenAI-compatible LLM.

## Architecture

Only **Neo4j** runs in Docker. The crawler, graph API, and UI run on the host so they can use a local or remote LLM and a local embedding model without baking secrets into images.

```
                    ┌─────────────────────────────────────────┐
  Capco.com  ──►    │  crawl_capco.py / start.py (Firecrawl)  │
                    └──────────────────┬──────────────────────┘
                                       │ writes
                                       ▼
                    ┌─────────────────────────────────────────┐
                    │  capco_articles/  (md + json + pdfs)    │
                    └──────────────────┬──────────────────────┘
                                       │ ingest_graph.py
                                       ▼
   ┌──────────────────┐    HTTP     ┌──────────────────────────┐
   │  Vite frontend   │ ◄─────────► │  FastAPI backend         │
   │  :8080           │             │  :8000  (vendor/…)       │
   └──────────────────┘             │  + local embeddings      │
                                    └────────────┬─────────────┘
                                                 │ Bolt
                                                 ▼
                                    ┌──────────────────────────┐
                                    │  Neo4j 5.26 + APOC       │
                                    │  Docker :7474 / :7687    │
                                    └──────────────────────────┘
```

| Layer | Component | Role |
|-------|-----------|------|
| Crawl | `crawl_capco.py`, `start.py` | Discover listing pages, scrape articles, download PDFs |
| Corpus | `capco_articles/` | Local store (not in git; created by the crawler) |
| Orchestration | `graph.sh` | Clone/patch upstream, write env files, start/stop processes |
| Graph API | `vendor/llm-graph-builder/backend` | Upload → chunk → LLM extract → embeddings → Neo4j |
| Graph UI | `vendor/llm-graph-builder/frontend` | Browse graph / chat over the corpus |
| Database | `docker-compose.yml` → Neo4j | Persistent graph + vector index |
| Ingest | `ingest_graph.py` | Batch-upload articles; tracks progress in `.graph_ingest_state.json` |
| Migrate | `scripts/migrate_*.sh` | Export/import articles + Neo4j dump to another host |

## Components (repo layout)

```
capco-crawler/
├── crawl_capco.py          # Firecrawl crawler (resume-safe)
├── start.py                # Interactive crawl launcher
├── check_status.py         # Corpus progress summary
├── ingest_graph.py         # Push articles into the graph builder API
├── graph.sh                # setup | start | stop | status | logs | restart
├── docker-compose.yml      # Neo4j only
├── scripts/
│   ├── patch_backend.py    # Upstream patches (embedding dim, LLM batching)
│   ├── migrate_export.sh   # Pack articles + neo4j.dump
│   └── migrate_import.sh   # Restore on another VM
├── .env.example            # Copy to .env and fill secrets
├── MIGRATION.md            # Step-by-step host migration
└── pyproject.toml          # Crawler deps (uv)
```

`vendor/` and `capco_articles/` are local-only (gitignored). `./graph.sh setup` clones the pinned llm-graph-builder revision into `vendor/`.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) (crawler uses Python ≥ 3.14; backend uses 3.12 via `graph.sh`)
- Docker Engine 24+ / Compose v2 (Neo4j)
- Node.js (frontend yarn install during setup)
- Firecrawl API key (crawl only)
- OpenAI-compatible chat API (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`)
- Local sentence-transformers model directory (`EMBEDDING_MODEL_DIR`, typically `BAAI/bge-m3`, 1024-d)

## Quick start

```bash
cp .env.example .env
# Edit: FIRECRAWL_API_KEY, LLM_*, EMBEDDING_MODEL_DIR, NEO4J_PASSWORD

uv sync
uv run python start.py                 # or: uv run python crawl_capco.py

./graph.sh setup                       # once
./graph.sh start                       # Neo4j + API :8000 + UI :8080
uv run python ingest_graph.py --limit 2
```

| Service | URL |
|---------|-----|
| Graph UI | http://localhost:8080 |
| Backend API docs | http://localhost:8000/docs |
| Neo4j Browser | http://localhost:7474 (`bolt://localhost:7687`, user `neo4j`) |

`./graph.sh status` / `logs backend|frontend|neo4j` / `restart` / `stop` manage the stack. Edit only the project `.env`; `graph.sh` regenerates vendor env files on each start.

## How the crawler works

1. **Discovery** — Walks intelligence listing pages (`TOTAL_PAGES`, default 47), collects unique article URLs into `article_links.json`.
2. **Scrape** — Each article is scraped via Firecrawl (Markdown + HTML + links). PDF links are downloaded into `capco_articles/pdfs/`.
3. **Persist** — Writes `articles/<title>_<hash>.{json,md}`. The hash is a stable SHA-256 prefix of the URL so resumes skip existing files.
4. **Summary** — Writes lightweight `crawl_summary.json` (metadata only; full bodies stay in per-article JSON).

Rate limits: ~1s between listing pages, ~2s between articles. Interrupt and re-run anytime.

```bash
uv run python check_status.py          # counts processed articles / PDFs
```

## How the knowledge graph works

1. `./graph.sh start` brings up Neo4j (Docker), patches and launches the FastAPI backend with your LLM + local embeddings, and starts the Vite UI.
2. The UI model id is fixed as **`capco_compatible`** (maps to `LLM_MODEL,LLM_BASE_URL,LLM_API_KEY` — values must not contain commas; include `/v1` in the base URL when required).
3. `ingest_graph.py` uploads each `*.md` (optional `--include-pdfs`), calls `/extract`, and records portable relative paths in `capco_articles/.graph_ingest_state.json`. Completed articles are skipped on later runs.
4. Embeddings load offline from `EMBEDDING_MODEL_DIR`. Changing dimension after ingest requires dropping the Neo4j vector index (or the `neo4j_data` volume).

```bash
uv run python ingest_graph.py --dry-run
uv run python ingest_graph.py                 # all pending
uv run python ingest_graph.py --limit 10
```

## Migrating to another system

Data (corpus + Neo4j) is **not** stored in git. Move it with the migration scripts. Full detail: [MIGRATION.md](MIGRATION.md).

**Source host**

```bash
./graph.sh stop
./scripts/migrate_export.sh
# → migration-export/capco-migration-<timestamp>.tgz
# Also copy .env and the EMBEDDING_MODEL_DIR tree over a secure channel
```

**Target host**

```bash
# Clone this repo, place .env, point EMBEDDING_MODEL_DIR at the new path
./scripts/migrate_import.sh /path/to/capco-migration-*.tgz
./graph.sh setup
./graph.sh start
uv run python ingest_graph.py --dry-run    # should skip already-completed articles
```

The export includes `capco_articles` and a Neo4j `neo4j.dump`. Secrets are never packed into the tarball.

## Configuration

See `.env.example`. Important keys:

| Variable | Purpose |
|----------|---------|
| `FIRECRAWL_API_KEY` | Crawler |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Graph extraction chat model |
| `LLM_MAX_CONCURRENCY` / `LLM_MAX_RETRIES` | Batch size and client retries |
| `EMBEDDING_MODEL_DIR` / `EMBEDDING_MODEL` / `EMBEDDING_DIMENSION` | Local embeddings |
| `NEO4J_PASSWORD` / `NEO4J_URI` | Docker Neo4j (password applies on first empty volume) |
| `GRAPH_API_URL` | Ingest client → backend (default `http://localhost:8000`) |

## Notes

- Docker is required for Neo4j only; do not expect a full-stack `docker compose up` for the UI/API.
- Articles without PDFs are still saved as web content.
- Long titles are truncated to 100 characters in filenames.
- Re-extracting the graph costs LLM tokens; prefer `migrate_export` / `migrate_import` when the graph already exists.
