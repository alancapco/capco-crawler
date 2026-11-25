#!/usr/bin/env python3
"""Upload Capco articles into the LLM Graph Builder and extract a knowledge graph."""

import argparse
import base64
import json
import os
import sys
import time
import uuid
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
DEFAULT_ARTICLES = ROOT / "capco_articles" / "articles"
STATE_PATH = ROOT / "capco_articles" / ".graph_ingest_state.json"
ENV_PATH = ROOT / ".env"
CHUNK_BYTES = 1024 * 1024
MODEL_ID = "capco_compatible"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def state_key(path: Path) -> str:
    """Store portable relative paths so ingest state survives a VM move."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def normalize_state(raw: dict) -> dict:
    """Rewrite absolute or legacy keys to ROOT-relative paths. Persists if changed."""
    normalized: dict = {}
    changed = False
    for key, value in raw.items():
        path = Path(key)
        if not path.is_absolute():
            new_key = Path(key).as_posix()
        else:
            try:
                new_key = path.resolve().relative_to(ROOT).as_posix()
            except ValueError:
                # Fall back to filename under articles/ or pdfs/ when the old host path is gone.
                name = value.get("file_name") if isinstance(value, dict) else None
                name = name or path.name
                candidate = ROOT / "capco_articles" / "articles" / name
                if not candidate.exists():
                    candidate = ROOT / "capco_articles" / "pdfs" / name
                new_key = state_key(candidate) if candidate.exists() else path.name
                changed = True
        if new_key != key:
            changed = True
        # Prefer completed over failed if the same article appears twice after rewrite.
        existing = normalized.get(new_key)
        if existing and existing.get("status") == "completed" and value.get("status") != "completed":
            continue
        normalized[new_key] = value
    if changed or len(normalized) != len(raw):
        save_state(normalized)
    return normalized


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    return normalize_state(json.loads(STATE_PATH.read_text(encoding="utf-8")))


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def credentials() -> dict:
    password = env("NEO4J_PASSWORD", "capco-graph-change-me")
    return {
        "uri": env("NEO4J_URI", "bolt://localhost:7687"),
        "userName": env("NEO4J_USERNAME", "neo4j"),
        "password": base64.b64encode(password.encode("utf-8")).decode("ascii"),
        "database": env("NEO4J_DATABASE", "neo4j"),
    }


def api_url() -> str:
    return env("GRAPH_API_URL", "http://localhost:8000").rstrip("/")


def post_form(path: str, data: dict, files=None, timeout=120) -> dict:
    response = requests.post(f"{api_url()}{path}", data=data, files=files, timeout=timeout)
    try:
        body = response.json()
    except ValueError:
        body = {"status": "Failed", "message": response.text[:500]}
    if response.status_code >= 400 or body.get("status") != "Success":
        message = body.get("message") or body.get("error") or response.text[:500]
        raise RuntimeError(f"{path} failed ({response.status_code}): {message}")
    return body


def upload_file(article: Path, creds: dict) -> None:
    payload = article.read_bytes()
    total = max(1, (len(payload) + CHUNK_BYTES - 1) // CHUNK_BYTES)
    upload_id = str(uuid.uuid4())
    for index in range(total):
        chunk = payload[index * CHUNK_BYTES : (index + 1) * CHUNK_BYTES]
        form = {
            **creds,
            "chunkNumber": str(index + 1),
            "totalChunks": str(total),
            "originalname": article.name,
            "model": MODEL_ID,
            "uploadId": upload_id,
        }
        files = {"file": (article.name, chunk, "application/octet-stream")}
        post_form("/upload", form, files=files, timeout=180)


def extract_file(article: Path, creds: dict) -> dict:
    form = {
        **creds,
        "model": MODEL_ID,
        "file_name": article.name,
        "source_type": "local file",
        "token_chunk_size": env("GRAPH_TOKEN_CHUNK_SIZE", "500"),
        "chunk_overlap": env("GRAPH_CHUNK_OVERLAP", "20"),
        "chunks_to_combine": env("GRAPH_CHUNKS_TO_COMBINE", "1"),
        "embedding_provider": "sentence-transformer",
        "embedding_model": env("EMBEDDING_MODEL", "BAAI/bge-m3"),
        "additional_instructions": env(
            "GRAPH_ADDITIONAL_INSTRUCTIONS",
            "Extract organizations, regulations, products, markets, and the relationships described in this Capco research article.",
        ),
    }
    timeout = int(env("GRAPH_EXTRACT_TIMEOUT", "3600"))
    return post_form("/extract", form, timeout=timeout)


def collect_articles(directory: Path, include_pdfs: bool) -> list[Path]:
    articles = sorted(directory.glob("*.md"))
    if include_pdfs:
        pdf_dir = directory.parent / "pdfs"
        articles.extend(sorted(pdf_dir.glob("*.pdf")))
    return articles


def main() -> int:
    load_env(ENV_PATH)
    parser = argparse.ArgumentParser(description="Build a knowledge graph from capco_articles.")
    parser.add_argument("--articles-dir", type=Path, default=DEFAULT_ARTICLES)
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of new articles to process. 0 means all.")
    parser.add_argument("--include-pdfs", action="store_true", help="Also upload PDFs from capco_articles/pdfs.")
    parser.add_argument("--dry-run", action="store_true", help="List articles that would be processed.")
    args = parser.parse_args()

    if not args.articles_dir.is_dir():
        print(f"Articles directory not found: {args.articles_dir}", file=sys.stderr)
        return 1

    state = load_state()
    pending = [
        path
        for path in collect_articles(args.articles_dir, args.include_pdfs)
        if state.get(state_key(path), {}).get("status") != "completed"
    ]
    if args.limit > 0:
        pending = pending[: args.limit]

    print(f"Articles to extract: {len(pending)}")
    print(f"Model id: {MODEL_ID}")
    print(f"LLM: {env('LLM_MODEL', '(set LLM_MODEL in .env)')} @ {env('LLM_BASE_URL', '(set LLM_BASE_URL in .env)')}")
    if args.dry_run:
        for path in pending:
            print(path.name)
        return 0

    try:
        health = requests.get(f"{api_url()}/health", timeout=10)
        health.raise_for_status()
    except requests.RequestException as exc:
        print(f"Graph backend is not reachable at {api_url()}: {exc}", file=sys.stderr)
        print("Start it with: ./graph.sh start", file=sys.stderr)
        return 1

    creds = credentials()
    completed = 0
    failed = 0
    for index, article in enumerate(pending, 1):
        print(f"\n[{index}/{len(pending)}] {article.name}")
        started = time.time()
        key = state_key(article)
        try:
            upload_file(article, creds)
            result = extract_file(article, creds)
            data = result.get("data") or {}
            state[key] = {
                "status": "completed",
                "file_name": article.name,
                "nodeCount": data.get("nodeCount"),
                "relationshipCount": data.get("relationshipCount"),
                "elapsed_seconds": round(time.time() - started, 1),
            }
            save_state(state)
            completed += 1
            print(f"   completed nodes={data.get('nodeCount')} relationships={data.get('relationshipCount')}")
        except Exception as exc:
            state[key] = {"status": "failed", "file_name": article.name, "error": str(exc)}
            save_state(state)
            failed += 1
            print(f"   failed: {exc}")

    print(f"\nDone. completed={completed} failed={failed}")
    print("Graph UI: http://localhost:8080")
    print("Neo4j Browser: http://localhost:7474")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
