# MemorieDen (minimal, dockerized)

Minimal single-user memory store + full-text search service.

- SQLite + **FTS5** for search
- FastAPI (JSON API)
- Persistent data via bind-mount on SSD: `/mnt/m2/memorieden` (recommended)
- Designed to run on **arm64** (no native ML deps by default)

## Quick start

```bash
cd /home/radxa/.openclaw/workspace/memorieden_docker
# SSD-backed data dir (recommended)
sudo mkdir -p /mnt/m2/memorieden

docker compose up --build -d
curl -s http://localhost:18001/health | jq
```

## API

Auth (optional): set `MEMORIEDEN_API_KEY` and send `X-API-Key: ...`.

### `GET /health`

```bash
curl -s http://localhost:18001/health
```

### `POST /memories/add`

```bash
curl -s http://localhost:18001/memories/add \
  -H 'content-type: application/json' \
  -d '{"content":"I like SQLite FTS5", "title":"note", "source":"manual", "user_id":"default"}' | jq
```

### `POST /memories/search` (FTS)

FTS5 query syntax is supported (quotes, AND/OR, NEAR, prefix `foo*`, etc.).

```bash
curl -s http://localhost:18001/memories/search \
  -H 'content-type: application/json' \
  -d '{"query":"sqlite", "limit": 10}' | jq
```

### `GET /memories/all`

```bash
curl -s 'http://localhost:18001/memories/all?limit=50' | jq
```

### `POST /memories/add_document`

Convenience endpoint for bulk-ish ingestion (one document per call).

```bash
curl -s http://localhost:18001/memories/add_document \
  -H 'content-type: application/json' \
  -d '{"title":"doc", "source":"import", "content":"# Hello\nThis is a document."}' | jq
```

## Import existing OpenClaw memories

This imports:
- `MEMORY.md`
- `memory/*.md`

Run on the host (writes into `/mnt/m2/memorieden/memorieden.sqlite3`):

```bash
cd /home/radxa/.openclaw/workspace/memorieden_docker
python3 scripts/import_openclaw_memories.py \
  --db /mnt/m2/memorieden/memorieden.sqlite3 \
  --workspace /home/radxa/.openclaw/workspace \
  --user-id default
```

Then search for something you know exists:

```bash
curl -s http://localhost:18001/memories/search \
  -H 'content-type: application/json' \
  -d '{"query":"OpenClaw", "limit": 5}' | jq
```

## Notes

- Database path is controlled by `MEMORIEDEN_DB` (default: `/data/memorieden.sqlite3`).
- This is intentionally single-user/single-tenant, but `user_id` is supported as an optional field for future partitioning.

## Optional embeddings search (not enabled)

If you want semantic search later, the lightest path is usually:
- `sentence-transformers` (or `fastembed`) + `faiss-cpu`

These add size and build time and may complicate arm64 builds, so they are **not included** in this minimal version.
