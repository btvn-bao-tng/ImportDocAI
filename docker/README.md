# ImportDoc AI — Docker

Production-shaped, self-contained stack: API, job worker, and the built SPA served by nginx
(which also reverse-proxies `/api` so the browser only talks to one origin).

```
docker/
├── backend.Dockerfile    # FastAPI API + worker (same image, different command)
├── frontend.Dockerfile   # Vite build -> nginx
├── nginx.conf            # /api proxy, SPA fallback, upload size, caching
├── docker-compose.yml    # backend, worker, frontend, app-data volume
└── .dockerignore         # build context = repo root
```

## Run

```sh
cd docker

# Optional: API key + base URL (or put providers.bioturing.apiKey in ../config.yml).
export BIOTURING_API_KEY=sk-...

docker compose up -d --build
open http://localhost:8080            # IMPORTDOC_HTTP_PORT overrides 8080
```

| Service   | Port | Notes |
|-----------|------|-------|
| frontend  | 8080 | nginx: SPA + `/api/` → `backend:8000` |
| backend   | —    | internal only; `/api/v1/health` used for healthcheck |
| worker    | —    | `python -m app.worker`, starts once the API is healthy |

State lives in the `app-data` volume (`/app/data`): SQLite DB, uploaded files, generated exports.

```sh
docker compose ps
docker compose logs -f backend worker
docker compose down          # keep the volume; add -v to wipe data
```

## Configuration

Settings resolve as **environment > `config.yml`** (see `src/backend/app/config.py`).

- `../.env` — optional, read by compose. Leave empty and the API fails fast with a clear error.
- `../config.yml` — optional, mounted read-only at `/app/config.yml`. Same shape as
  `config.example.yml`:
  ```sh
  cp config.example.yml config.yml   # then set providers.bioturing.apiKey
  ```
  If you do not want a `config.yml`, delete the volume line in `docker-compose.yml`.

| Variable | Default in compose | Purpose |
|----------|--------------------|---------|
| `BIOTURING_API_KEY` | _(required)_ | LLM gateway key |
| `BIOTURING_BASE_URL` | `https://hy-llm.bioturing.com/v1` | OpenAI-compatible endpoint |
| `BIOTURING_TEXT_MODEL` / `BIOTURING_VISION_MODEL` | `sonya` / `vision_sonya` | model names |
| `MAX_TOKENS` | `32000` | per-request cap |
| `IMPORTDOC_HTTP_PORT` | `8080` | host port for the frontend |

Paths inside the container are fixed: `DATA_DIR=/app/data/storage`,
`DATABASE_URL=sqlite+aiosqlite:////app/data/app.db`. Back them by the volume, not by a
bind mount of a host file, or SQLite's WAL files (`-wal`, `-shm`) will misbehave.

## Notes

- **Single worker.** The job worker is a separate process but shares the SQLite file; run exactly
  one `worker` replica. SQLite with WAL does not support multiple writers across containers.
- **Schema.** `create_all` runs on every start and only adds nullable columns. There is no
  migration step; keep backups of `app-data` before upgrading images.
- **Non-root.** The backend runs as UID 10001; the image pre-creates `/app/data` with matching
  ownership so a fresh named volume works. A pre-populated bind mount must be writable by that UID
  (e.g. `chown -R 10001:10001 ./data`).
- **Proxy timeouts.** nginx allows up to 300 s and streams responses unbuffered, since extraction
  and xlsx export can be slow; uploads are capped at 64 MB.
- **Build context** is the repository root (`context: ..`) so both images see the backend package
  and the frontend sources; `.dockerignore` trims tests, node_modules, `.venv` and local data.
