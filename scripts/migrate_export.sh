#!/usr/bin/env bash
# Export everything needed to move this stack to another VM.
#
#   ./scripts/migrate_export.sh [output-dir]
#
# Produces a directory (default: ./migration-export) containing:
#   - capco_articles.tgz          crawled markdown/json/pdfs + ingest state
#   - neo4j.dump                  Neo4j database dump (requires a temporary Neo4j start)
#   - neo4j_data.tgz              fallback raw volume archive if dump is unavailable
#   - env.example                 sanitized env template (no secrets)
#   - MANIFEST.txt                sizes, versions, restore instructions pointer
#
# Secrets (.env, API keys) are NOT copied. Transfer .env separately over a secure channel.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/migration-export}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORKDIR="$OUT/work-$STAMP"

log() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "docker is required"
command -v tar >/dev/null || die "tar is required"

mkdir -p "$WORKDIR" "$OUT"
cd "$ROOT"

log "=== Capco crawler migration export ($STAMP) ==="

# 1) Articles + PDFs + ingest state (portable relative paths after ingest_graph normalize)
if [ -d "$ROOT/capco_articles" ]; then
  log "Packing capco_articles/"
  # Drop the bloated crawl_summary if present; articles/*.json already hold full content.
  tar -C "$ROOT" \
    --exclude='capco_articles/.DS_Store' \
    --exclude='capco_articles/**/.DS_Store' \
    -czf "$WORKDIR/capco_articles.tgz" \
    capco_articles
else
  die "capco_articles/ not found"
fi

# 2) Neo4j: prefer an offline dump; fall back to a volume tarball
VOLUME_NAME="$(docker volume ls -q --filter name=capco-crawler_neo4j_data | head -n1 || true)"
if [ -z "$VOLUME_NAME" ]; then
  VOLUME_NAME="$(docker volume ls -q | grep -E 'capco.*neo4j_data$' | head -n1 || true)"
fi

dump_neo4j() {
  [ -n "$VOLUME_NAME" ] || return 1
  log "Stopping Neo4j containers for a consistent dump"
  (cd "$ROOT" && docker compose stop neo4j >/dev/null 2>&1 || true)
  docker rm -f capco-graph-neo4j >/dev/null 2>&1 || true
  while IFS= read -r id; do
    [ -n "$id" ] && docker rm -f "$id" >/dev/null 2>&1 || true
  done < <(docker ps -aq --filter name=capco-graph-neo4j 2>/dev/null || true)

  log "Creating Neo4j dump from volume $VOLUME_NAME"
  # neo4j-admin dump needs the DBMS offline and /data mounted at the image default.
  docker run --rm \
    --user neo4j \
    -v "$VOLUME_NAME:/data" \
    -v "$WORKDIR:/backup" \
    neo4j:5.26-community \
    neo4j-admin database dump neo4j --to-path=/backup --overwrite-destination=true
  [ -f "$WORKDIR/neo4j.dump" ]
}

if dump_neo4j; then
  log "Neo4j dump ready: neo4j.dump"
else
  log "neo4j-admin dump unavailable; packing raw volume as neo4j_data.tgz"
  [ -n "$VOLUME_NAME" ] || die "no Neo4j data volume found (is the graph stack unused?)"
  docker run --rm \
    -v "$VOLUME_NAME:/data:ro" \
    -v "$WORKDIR:/backup" \
    alpine:3.20 \
    tar -C /data -czf /backup/neo4j_data.tgz .
fi

# 3) Sanitized env template (never copy live secrets into the export)
cp "$ROOT/.env.example" "$WORKDIR/env.example"
if [ -f "$ROOT/.env" ]; then
  # Record which keys exist so the target VM knows what to fill — values redacted.
  {
    echo "# Keys present on source host (values redacted). Copy secrets via a secure channel."
    while IFS= read -r line || [ -n "$line" ]; do
      case "$line" in ''|'#'*) continue ;; *=*) ;; *) continue ;; esac
      key="${line%%=*}"
      key="${key//[[:space:]]/}"
      printf '%s=<redacted>\n' "$key"
    done < "$ROOT/.env"
  } > "$WORKDIR/env.keys.txt"
fi

# 4) Manifest
{
  echo "exported_at=$STAMP"
  echo "source_host=$(hostname 2>/dev/null || echo unknown)"
  echo "project_root=$ROOT"
  echo "neo4j_image=neo4j:5.26-community"
  echo "neo4j_volume=${VOLUME_NAME:-none}"
  echo "articles_archive=capco_articles.tgz"
  if [ -f "$WORKDIR/neo4j.dump" ]; then
    echo "neo4j_archive=neo4j.dump"
  else
    echo "neo4j_archive=neo4j_data.tgz"
  fi
  echo "embedding_note=Copy EMBEDDING_MODEL_DIR (BAAI/bge-m3) separately; set path in .env on the target."
  echo "secrets_note=.env is NOT included. Transfer it securely and set EMBEDDING_MODEL_DIR for the new host."
  echo "restore=See MIGRATION.md and ./scripts/migrate_import.sh"
  if command -v du >/dev/null; then
    echo "--- sizes ---"
    du -h "$WORKDIR"/* 2>/dev/null || true
  fi
} > "$WORKDIR/MANIFEST.txt"

# 5) Bundle
BUNDLE="$OUT/capco-migration-$STAMP.tgz"
tar -C "$WORKDIR" -czf "$BUNDLE" .
rm -rf "$WORKDIR"

log ""
log "Export complete: $BUNDLE"
log "Next:"
log "  1. Securely copy .env and the embedding model directory to the new VM"
log "  2. Copy $BUNDLE to the new VM"
log "  3. On the new VM: ./scripts/migrate_import.sh /path/to/capco-migration-*.tgz"
log "  4. Follow MIGRATION.md"
