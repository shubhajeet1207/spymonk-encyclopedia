# Installation & usage

[← Project overview](../../README.md) · [Documentation index](../README.md)

This guide covers local setup and operation of the React frontend and FastAPI backend. Commands use Windows PowerShell and assume the repository is at `E:\Work\spymonk-encyclopedia`; substitute your own checkout path.

## 1. Prerequisites

- Python **3.11.5 or newer 3.11** is the CI-tested version; the pinned dependency set needs at least 3.11.5.
- Node.js **22.12 or newer**, with npm.
- A chat-model API key and an accessible OpenAI-compatible endpoint.
- Internet access for dependencies, the initial local embedding-model download, and web/model requests.

The default embedding setup uses Sentence Transformers locally. A remote embedding API is an alternative, not an additional requirement. Provider usage may incur charges.

## 2. Install dependencies

Run these commands from the repository root:

```powershell
cd E:\Work\spymonk-encyclopedia
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-local-embeddings.txt -c constraints.txt
npm --prefix frontend ci
```

Using the virtual environment's Python directly avoids PowerShell activation-policy issues. `requirements-local-embeddings.txt` includes the base API dependencies plus Sentence Transformers and torch. `constraints.txt` pins the dependency set.

For a backend that uses only remote embeddings, install `requirements.txt` instead and configure the remote provider in the next step:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
```

## 3. Configure the backend

Create a local configuration file **only if you do not already have one**:

```powershell
if (-not (Test-Path -LiteralPath .env)) {
    Copy-Item -LiteralPath .env.example -Destination .env
}
```

Edit `.env` yourself. Never commit it or paste its secrets into logs, issues, or frontend configuration. The checked-in [`.env.example`](../../.env.example) is the reference for available settings and defaults.

### Required chat-model settings

| Setting | Purpose |
| --- | --- |
| `LLM_API_KEY` | Secret for the selected chat-model provider. |
| `LLM_BASE_URL` | The provider's OpenAI-compatible API base URL. |
| `LLM_MODEL` | A model identifier supported by that endpoint. |
| `LLM_DISABLE_THINKING` | Vendor-specific option. Leave `false` unless the provider supports `chat_template_kwargs`. |

The example file is configured for NVIDIA's OpenAI-compatible endpoint. Replace the placeholder key, and confirm the model is available to your account. Use matching endpoint, model, and credentials when changing providers.

### Embeddings and vector storage

For local embeddings, keep:

```dotenv
EMBEDDING_PROVIDER=huggingface
EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2
VECTOR_STORE_PROVIDER=chroma-local
```

The first semantic search or ingestion may take longer while the model downloads and loads. `EMBEDDINGS_WARMUP=true` moves model loading to API startup.

For an OpenAI-compatible embedding service, use:

```dotenv
EMBEDDING_PROVIDER=openai
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_API_KEY=your-embedding-provider-key
# Set only when using a compatible endpoint other than the default:
# EMBEDDING_BASE_URL=https://your-provider.example/v1
```

Changing the embedding provider or model requires an [index rebuild](#rebuild-the-searchable-index). Chat and embedding providers have separate configuration and can be different services.

### Access and storage

- Keep `APP_ALLOWED_ORIGINS` at the two local frontend origins for local development.
- Keep `APP_ALLOWED_HOSTS=127.0.0.1,localhost` for a loopback-only API.
- Without `APP_API_TOKEN`, API routes other than health allow loopback clients only. With a token, protected API requests need `Authorization: Bearer <token>`; enter the token in the UI when prompted.
- `SPYMONK_DATA_DIR` selects the persistent storage root. When unset, local development uses the project root.
- Start **one API process per data directory**. The instance lock rejects another process on the same directory; do not use `--workers` or disable the lock to bypass this protection.

Restart the API after configuration changes.

Browser writes are checked against the allowed origins, and request bodies are limited before multipart parsing. Public-source fetches pin the validated public IP address, restrict ports to 80/443, recheck redirects, and enforce byte/time limits. These safeguards complement the token and rate limits; they do not replace the hosting work listed in the [gap analysis](../review-gap-analysis.md).

## 4. Start the application

In the first terminal, from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
npm --prefix frontend run dev
```

Open **http://127.0.0.1:5173**. Vite proxies `/api` requests to **http://127.0.0.1:8000** in development. A read-only health check is available at **http://127.0.0.1:8000/api/health**.

`app.py` is a compatibility entry point; `backend.main:app` is the preferred target. Use `--reload` for development only.

## 5. Try a first session

1. **Connect.** If an API token is configured, enter it in the access panel. The frontend keeps it in session storage.
2. **Add a reference.** Upload a PDF containing selectable text. Wait for ingestion to report indexed chunks. Image-only scans need OCR outside SpyMonk first.
3. **Ask a specific question.** Ask something supported by that PDF and inspect the source references. Uploaded PDFs are local evidence, not independently verified web claims.
4. **Research something missing.** Enter a topic in the research workspace. Optionally prepare and review the search plan, then start research.
5. **Watch and review.** Follow the pipeline, inspect claims and sources, and read the answer generated from saved evidence. If nothing qualifies, open **Unverified questions** and download the report.

Previously researched topics are checked before a new web search. Use refresh when you intentionally want a new review. In the question workflow, enable research-on-miss only when you want an unanswered question to trigger web research.

### Research lifecycle

| Operation | Endpoint |
| --- | --- |
| Prepare search queries without fetching sources | `POST /api/research/plan` |
| Start a background job; receive its ID | `POST /api/research/jobs` |
| Follow replayable stage updates | `GET /api/research/jobs/{id}/events` |
| Request cancellation between stages | `DELETE /api/research/jobs/{id}` |
| List unanswered-question records | `GET /api/inquiries` |
| Download a user-facing report | `GET /api/inquiries/{id}/report.pdf` |
| Download developer diagnostics | `GET /api/inquiries/{id}/developer-log` |

Submitting a topic already running returns its existing job. A dropped browser event connection can reconnect without restarting research; backend restarts do not preserve in-memory jobs. Legacy `POST /api/research` waits for the result, while closing the legacy `/api/research/stream` connection cancels that work.

Research has bounded fetch/model concurrency and a wall-clock deadline (`RESEARCH_MAX_SECONDS`). Malformed extraction output is retried once. If a batched cross-check is malformed, individual checks are attempted; claims whose checks still fail remain `incomplete`. A zero-verification run records an unanswered question instead of adding answerable claims.

## Testing

For the development dependency set and checks:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -c constraints.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -t .
npm --prefix frontend test
npm --prefix frontend run build
```

The [CI workflow](../../.github/workflows/ci.yml) defines backend tests, frontend tests/build, and dependency audits. It runs automatically only after this repository is hosted with that workflow enabled.

## Maintenance and troubleshooting

### Rebuild the searchable index

Rebuild after changing the embedding model/provider, clearing the index, or seeing a fingerprint/rebuild warning. Back up your data and let active research/uploads finish before maintenance.

First inspect the proposed work:

```powershell
.\.venv\Scripts\python.exe -m backend.core.reindex
```

Then, only when ready to replace the derived index:

```powershell
.\.venv\Scripts\python.exe -m backend.core.reindex --apply
```

The UI's **Rebuild searchable index** action also rebuilds it. A replacement index is built beside the live index and swapped in after completion. Identical PDFs are indexed once; PDFs without extractable text are skipped with a reason.

Topic JSON audits are the source of truth for web evidence. Keep them, source snapshots, and uploaded PDFs when backing up—not just Chroma. Older installations can inspect collision-safe topic-filename migration with `python -m backend.core.memory`; `--apply` performs the migration. Run it using the virtual environment and back up first.

### Read the logs

Paths below are relative to `SPYMONK_DATA_DIR`:

| Location | Contents |
| --- | --- |
| `logs/spymonk-api.log` | Request IDs, route/status/duration, exceptions, and fallback events; rotated at 5 MiB with five backups by default. |
| `logs/research/` | Per-run plans, sources, model-call outcomes, verdicts, timings, and automatic diagnostics; newest 500 retained by default. |
| `logs/diagnostics/` | Investigation records. |
| `memory/unanswered/` | Questions for which research could not verify an answer. |
| `memory/history/` | Earlier topic audit versions. |

Search backend logs using the request ID displayed by the UI. General request logs hash query text and avoid request bodies, prompts, and provider error text. Research reports contain source and claim details: treat them as private, and review them before sharing.

Set `LOG_LEVEL=DEBUG` for more detail or `HTTPX_LOG_LEVEL=INFO` for HTTP traces. `LOG_TO_FILE=false` switches off file logging. `SPYMONK_LOG_DIR` can override the log directory.

### Common issues

| Symptom | Check |
| --- | --- |
| API access denied | Token matches the backend; frontend origin and API hostname are explicitly allowed. |
| First indexing/search request is slow | Local embedding model download/loading. Consider warmup or remote embeddings; warmup adds startup time. |
| No searchable text after upload | The PDF may be a scan. Upload a text-based/OCR-processed document. A batch must pass extraction validation before files are saved. |
| Existing PDF is skipped | Duplicate detection compares file bytes; renaming a copy does not make it new evidence. |
| Nothing verified | Inspect the unanswered-question report for source diversity, empty text, failed model checks, or timeouts. Do not relax verification just to produce an answer. |
| Index incompatible with configuration | Restore the previous embedding settings or perform a backed-up rebuild. |
| A second API instance will not start | Stop the other process using that data directory. Keep one backend instance. |
| Report fonts are missing | Set `REPORT_FONT_DIR` to a directory containing the Noto Sans fonts described in `.env.example`. |

## Hosting on Hugging Face Spaces (free)

The recommended free option runs the UI and API in one Docker container on a Hugging Face Space. The Space's disk is wiped on restart, so the archive is restored from a private Hugging Face dataset at every start and copied back every 15 seconds. Follow [Deploy on Hugging Face Spaces](../DEPLOY_HF_SPACES.md).

## Alternative: Vercel frontend with a separate backend

This is the deployment topology, not a claim of public-production readiness. Review the [open hosting/security gaps](../review-gap-analysis.md) before exposing the service.

### Frontend

1. Set the Vercel project root to `frontend/`.
2. Use the Vite build (`npm run build`) with output directory `dist`.
3. Set `VITE_API_BASE_URL` to the **HTTPS origin** of your FastAPI service, then build/deploy.

Never put provider keys or the personal API token in `VITE_*` variables: those values become browser-visible build output.

### Backend

Run FastAPI on a separate Python host with persistent storage, one API process, and no development reloader. Configure backend environment/secrets:

| Setting | Hosted value |
| --- | --- |
| `SPYMONK_DATA_DIR` | The persistent mounted volume, not an ephemeral function directory. |
| `APP_ALLOWED_ORIGINS` | Exact frontend origins, including only intended preview deployments. |
| `APP_ALLOWED_HOSTS` | The API's public hostname. |
| `APP_API_TOKEN` | A long, randomly generated personal token. |
| `LLM_*` / embedding settings | Matching provider credentials, model, and endpoint. |

For Chroma Cloud, set `VECTOR_STORE_PROVIDER=chroma-cloud`, `CHROMA_API_KEY`, `CHROMA_TENANT`, and `CHROMA_DATABASE`. Files and JSON audits still need the backend volume; Cloud vectors do not make the file-backed application multi-instance.

Set reverse-proxy body limits to match the API: up to ten 20 MiB PDFs plus 1 MiB overhead for uploads, and 1 MiB for other requests. Configure rate limits (`RATE_LIMIT_*`), the daily chat-model call ceiling (`LLM_DAILY_CALL_LIMIT`), research deadlines, and backups. A call ceiling is not a monetary spend cap.

The personal token is a shared secret, not a user-account system. Do not rely on loopback-only mode behind a public reverse proxy, and do not expose this as a public multi-user service without resolving the identity, proxy, isolation, and compliance gaps. See the [TRD](../TRD.md) for architecture constraints.
