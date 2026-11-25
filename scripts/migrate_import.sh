#!/usr/bin/env bash
# Restore a migration bundle created by scripts/migrate_export.sh onto this machine.
#
#   ./scripts/migrate_import.sh /path/to/capco-migration-YYYYMMDD.tgz
#
# Prerequisites on the target VM:
#   - Docker + Compose v2
#   - uv, Node.js (for ./graph.sh setup)
#   - This git checkout of capco-crawler
#   - .env already in place (copied securely) with EMBEDDING_MODEL_DIR pointing at a local model
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUNDLE="${1:-}"

log() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ -n "$BUNDLE" ] || die "usage: ./scripts/migrate_import.sh /path/to/capco-migration-*.tgz"
[ -f "$BUNDLE" ] || die "bundle not found: $BUNDLE"
command -v docker >/dev/null || die "docker is required"
command -v tar >/dev/null || die "tar is required"
[ -f "$ROOT/.env" ] || die ".env missing. Copy it securely first (see MIGRATION.md)."

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/capco-migrate.XXXXXX")"
cleanup() { rm -rf "$WORKDIR"; }
trap cleanup EXIT

log "=== Capco crawler migration import ==="
log "Extracting $BUNDLE"
tar -C "$WORKDIR" -xzf "$BUNDLE"
[ -f "$WORKDIR/MANIFEST.txt" ] && { log "--- MANIFEST ---"; cat "$WORKDIR/MANIFEST.txt"; log "---------------"; }

# 1) Articles
[ -f "$WORKDIR/capco_articles.tgz" ] || die "capco_articles.tgz missing from bundle"
log "Restoring capco_articles/"
mkdir -p "$ROOT/capco_articles"
tar -C "$ROOT" -xzf "$WORKDIR/capco_articles.tgz"

# Normalize ingest-state keys to this host (no-op if already relative)
if [ -f "$ROOT/capco_articles/.graph_ingest_state.json" ]; then
  log "Normalizing ingest state paths"
  (cd "$ROOT" && uv run python -c 'from ingest_graph import load_state; s=load_state(); print(f"state entries: {len(s)}")')
fi

# 2) Neo4j volume
log "Preparing Neo4j volume"
(cd "$ROOT" && docker compose stop neo4j >/dev/null 2>&1 || true)
# Remove any existing container so we can replace volume contents cleanly
if docker ps -aq --filter name=capco-graph-neo4j | grep -q .; then
  docker ps -aq --filter name=capco-graph-neo4j | while read -r id; do docker rm -f "$id" >/dev/null; done
fi

# Ensure the named volume exists (Compose project name defaults to the directory name)
(cd "$ROOT" && docker compose create neo4j >/dev/null)
PROJECT="$(basename "$ROOT")"
VOLUME_NAME="$(docker volume ls -q | grep -E "^(${PROJECT}|capco-crawler)_neo4j_data$" | head -n1 || true)"
[ -n "$VOLUME_NAME" ] || die "could not resolve neo4j_data volume name"

if [ -f "$WORKDIR/neo4j.dump" ]; then
  log "Loading Neo4j dump into volume $VOLUME_NAME"
  docker run --rm \
    -v "$VOLUME_NAME:/data" \
    alpine:3.20 \
    sh -c 'rm -rf /data/*'
  docker run --rm \
    --user neo4j \
    -v "$VOLUME_NAME:/data" \
    -v "$WORKDIR:/backup" \
    neo4j:5.26-community \
    neo4j-admin database load neo4j --from-path=/backup --overwrite-destination=true
elif [ -f "$WORKDIR/neo4j_data.tgz" ]; then
  log "Restoring raw Neo4j volume tarball into $VOLUME_NAME"
  docker run --rm \
    -v "$VOLUME_NAME:/data" \
    -v "$WORKDIR:/backup" \
    alpine:3.20 \
    sh -c 'rm -rf /data/* && tar -C /data -xzf /backup/neo4j_data.tgz'
else
  die "bundle has neither neo4j.dump nor neo4j_data.tgz"
fi

log "Starting Neo4j"
(cd "$ROOT" && docker compose up -d --wait neo4j)

log ""
log "Import complete."
log "Next on this VM:"
log "  1. Confirm EMBEDDING_MODEL_DIR in .env points at a local BAAI/bge-m3 model"
log "  2. ./graph.sh setup   # once"
log "  3. ./graph.sh start"
log "  4. uv run python ingest_graph.py --dry-run   # should skip already-completed articles"
log "  5. Open http://localhost:8080 and http://localhost:7474"
