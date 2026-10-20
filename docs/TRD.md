# Technical Requirements Document — SpyMonk Encyclopedia

| Field | Value |
| --- | --- |
| Product | SpyMonk Encyclopedia |
| Version | 2.3 — deployment target |
| Last reviewed | 2026-10-20, against the code at API version 2.3.0 |
| Status | Technical design current; cloud services and public-release controls remain to be configured |
| Audience | Product owner, engineering, reviewers, and operators |
| Product approval | The PRD remains a review draft; this TRD does not imply product approval |
| Architecture | React/Vite on Vercel; FastAPI on a persistent Python host (one process); Chroma Cloud for production vectors |

## Revision 2.3

The 2.2 document had drifted from the code and lacked several sections a reviewer needs to judge the design. This revision:

- **Corrects stale behavior:** the research save rule (runs with failures no longer save nothing), the retired `memory/attempts/` record, the module list, and the model-call budget.
- **Adds missing architecture:** design invariants (§2), persisted data contracts and recovery (§6), the research outcome model (§7.3), a failure and degradation matrix (§8), the LLM contract and model qualification (§9), the observability model (§11), and quality gates (§15).
- **Documents the unanswered-question feature:** stored records, a PDF report for the person who asked, and a developer log (ADR-026, ADR-027).
- **2026-10-20:** research answers the question from the claims it saves (ADR-029); the API log and a developer log for every run are kept under `logs/` (ADR-028).
- **2026-10-20:** free hosting on Hugging Face Spaces in one container, with the archive backed up to a private dataset (ADR-030, §3.1).

## 1. Purpose and boundary

SpyMonk is an evidence-grounded research archive. It retrieves candidate passages from vector memory first, validates their provenance, and asks the configured language model to answer only from eligible evidence. Web research is an explicit, bounded fallback, not the source of answers. When research cannot verify an answer, the system says so plainly and explains why, instead of guessing.

This document describes the current React/FastAPI implementation and a deployable target. The product PRD at [PRD.md](PRD.md) is still a review draft. Moving document processing and memory to hosted services changes the original local-first privacy boundary and needs explicit product-owner approval before public deployment.

Non-goals: multi-user tenancy, horizontal scaling, OCR, and translation review (see §17).

## 2. Architecture overview

```text
Browser (React)
  └─ HTTPS /api  (Bearer token, or loopback only)
       └─ FastAPI, one process per data directory
            ├─ api/        HTTP boundary: auth, origin/host checks, limits, schemas, routes
            ├─ services/   orchestration: memory-first lookup, research jobs, rate limits, reports
            └─ core/       research pipeline, evidence rules, retrieval, stores, ingestion
                 ├─ Topic audits (JSON)        source of truth for web claims
                 ├─ Unanswered questions (JSON) diagnostics; never used for answers
                 ├─ Snapshots and PDFs          content-addressed files
                 ├─ Vector index (Chroma)       derived; rebuildable from audits and PDFs
                 └─ Search, LLM, and embedding providers
```

### Design invariants

These hold across the codebase; a change that breaks one needs an ADR.

1. **Evidence before answers.** An answer uses only eligible, provenance-carrying passages. A web claim is eligible only when it is quote-verified and supported by at least `MIN_INDEPENDENT_ORIGINS` (2) registrable domains, with no unresolved contradiction.
2. **Audits are the source of truth.** The index is derived from audits and stored PDFs and can be rebuilt at any time (`python -m backend.core.reindex`).
3. **Fail closed per claim, degrade per run.** A failure withholds only the claims it affects. No failure deletes previously verified memory.
4. **Every research run ends in exactly one outcome:** saved, saved with warnings, unanswered, failed, or cancelled (§7.3). The UI states which one, in plain language.
5. **Untrusted input is data.** Web text is delimited and labeled as untrusted, and model output is parsed and validated, never executed or trusted as instructions.
6. **One process per data directory.** Jobs, rate limits, and the LLM budget are process-local (ADR-022).
7. **Logs carry no content.** Console logs omit prompts, document text, credentials, and provider error text. Per-run diagnostics with claim content are stored as records, behind the same access control as the archive.

## 3. Deployment target

```text
Browser
  └─ React + Vite static site (Vercel)
       └─ HTTPS API_BASE_URL  (Authorization: Bearer <APP_API_TOKEN>)
            └─ FastAPI service — exactly one process (Python host with persistent volume)
                 ├─ Chroma Cloud: embeddings, chunks, semantic retrieval
                 ├─ Persistent volume: PDFs, web snapshots, JSON audits, unanswered-question records
                 └─ Configured search, embedding, and LLM providers
```

The frontend and API are separate deployment units. FastAPI stays a normal Python service so the 20 MB PDF limit, snapshot writes, research jobs, and PDF report rendering do not depend on Vercel Function request limits or an ephemeral filesystem. Vercel documents a 4.5 MB request/response body cap for Functions and notes that function filesystems are not durable. See [Vercel Function limits](https://vercel.com/docs/functions/limitations) and [Vercel FastAPI guidance](https://vercel.com/docs/frameworks/backend/fastapi).

### 3.1 Free hosting: Hugging Face Spaces

```text
Browser ── HTTPS ── Hugging Face proxy ── Space container (Docker, UID 1000, port 7860)
                                            ├─ FastAPI: /api/* and the built React UI (same origin)
                                            ├─ /home/user/spymonk-data  (temporary disk)
                                            └─ private dataset YOUR-NAME/spymonk-data  (durable backup)
```

- **Image:** [`Dockerfile`](../Dockerfile): a Node stage builds the UI; the runtime is `python:3.11-slim` with `fonts-noto-core`, CPU-only torch, and the embedding model baked in. One uvicorn worker.
- **Startup order:** take the data-directory lock, open the log file, restore the archive from the backup (failure stops startup), rebuild the index if it is missing, then start the backup timer.
- **Backup:** every `BACKUP_INTERVAL_SECONDS` (15), files whose content changed are committed to the dataset; a final sync runs at shutdown. Only the files in §6.1 marked as backed up are copied. Nothing is deleted from the backup.
- **Hosted mode** (`SPACE_ID` present, or `SPYMONK_HOSTED=true`): `APP_API_TOKEN` is required, the API schema is hidden, the allowed host and origin come from `SPACE_HOST`, and the UI may be framed only by `https://huggingface.co`.
- **Deploy:** `scripts/deploy_hf_space.py` uploads an allow-list of code files (never `.env`, data, or logs) and sets the Space's secrets and variables. See [DEPLOY_HF_SPACES.md](DEPLOY_HF_SPACES.md).
- **Limits:** the Space sleeps after 48 hours without visits; the first visit after a restart takes about 1–2 minutes (restore plus index rebuild). Changes written in the last seconds before a crash (not a normal shutdown) can be lost. Requests reach the app through Hugging Face's proxy, so rate limits are shared by all clients of one Space.

### Deployment prerequisites

- Create a Vercel project with its root directory set to `frontend/`, and set `VITE_API_BASE_URL` to the HTTPS origin of the FastAPI service.
- Run FastAPI as a single process on a host with a persistent mounted volume, and set `SPYMONK_DATA_DIR` to that mount. A second process on the same data directory refuses to start.
- Set `APP_API_TOKEN` to a long random value. Without it the API serves loopback clients only.
- Set `APP_ALLOWED_ORIGINS` to the exact production frontend origin(s), and `APP_ALLOWED_HOSTS` to the API hostname. CORS is not authentication.
- Set `VECTOR_STORE_PROVIDER=chroma-cloud` with `CHROMA_API_KEY`, `CHROMA_TENANT`, and `CHROMA_DATABASE`.
- Configure LLM and embedding credentials as server-side secrets. A hosted backend should use a hosted embedding provider (install `requirements.txt` only) unless it is sized for the local Sentence Transformers model.
- Install fonts for PDF reports in the API image (for example `fonts-noto-core`), or set `REPORT_FONT_DIR`. Without a Unicode font, reports fall back to Helvetica, which cannot show non-Latin text (§9.4).
- The bearer token is one shared secret. Multi-user public hosting needs an identity-aware proxy or real login in front of the API.

The repository contains deploy configuration and provider selection, not provisioned resources. No credentials are stored in source control.

## 4. Stack

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Web UI | React 19, Vite 8, JavaScript/CSS | Research desk, PDF library, archive questions, evidence display, unanswered-question reports. Static Vercel site. |
| API | Python 3.11+, FastAPI, Pydantic, Uvicorn | Typed HTTP boundary, bearer/loopback access, CORS/origin/host policy, rate limits, validation, error mapping. |
| Services | In-process job pool, token buckets, fpdf2 | Memory-first research flow; background jobs with cancellation and replayable progress; report rendering. |
| Research core | LangChain, OpenAI-compatible chat API | Query planning, claim extraction, batched cross-origin comparison, quote verification, grounded answers. |
| Search/acquisition | DDGS, HTTPX/httpcore, Beautiful Soup, PyPDF (subprocess) | Public search, IP-pinned guarded fetches, HTML extraction, byte and time limits, isolated PDF parsing. |
| Vector store | ChromaDB + `langchain-chroma` | Local persistent Chroma for development; Chroma Cloud for hosting. One shared client per process. |
| Embeddings | Sentence Transformers locally; OpenAI-compatible API optionally | Document and query vectors. The index records which model built it and refuses another. |
| Persistence | JSON records and content-addressed files | Topic audits, audit history, unanswered questions, snapshots with cached text, user PDFs. |
| Reports | fpdf2 with uharfbuzz text shaping | PDF reports with Unicode text, including shaped Devanagari. |
| Configuration | `.env` locally; environment variables in production | Validated at startup; an invalid value fails with the variable name. Secrets are server-only. |

## 5. Project structure

```text
spymonk-encyclopedia/
├── app.py                         # backwards-compatible ASGI import: app:app
├── backend/
│   ├── main.py                    # FastAPI factory, lifespan, middleware, router composition
│   ├── api/
│   │   ├── schemas.py             # Pydantic request/response contracts
│   │   ├── security.py            # bearer/loopback access and exact-origin checks
│   │   ├── errors.py              # exception handlers
│   │   └── routes/                # system, research, questions, documents, memory, inquiries
│   ├── services/
│   │   ├── archive.py             # memory-first lookup or research, failure mapping
│   │   ├── jobs.py                # background research jobs
│   │   ├── rate_limit.py          # per-client token buckets
│   │   └── reports.py             # PDF report and developer log rendering
│   ├── observability/logging.py   # request-ID logging, safe exception context
│   └── core/
│       ├── config.py              # validated paths and environment settings
│       ├── embeddings.py          # embedding provider factory and identity
│       ├── evidence.py            # status vocabulary, claim IDs, claim documents
│       ├── ingest.py              # isolated PDF extraction and model-sized chunking
│       ├── inquiries.py           # unanswered-question records and diagnostics
│       ├── instance_lock.py       # one API process per data directory
│       ├── llm_budget.py          # daily LLM call ceiling
│       ├── llm_json.py            # model-output JSON parsing
│       ├── memory.py              # topic audit records, atomic writes
│       ├── query.py               # filtered retrieval and grounded answers
│       ├── reindex.py             # rebuild the index from audits and PDFs
│       ├── research.py            # research pipeline and save rules
│       ├── research_trace.py      # per-run facts for diagnostics
│       ├── scraper.py             # safe, bounded search and fetching
│       ├── search_planner.py      # LLM query planning with a baseline fallback
│       ├── snapshot_paths.py      # portable snapshot paths, cached snapshot text
│       ├── store.py               # local/cloud Chroma adapter with fingerprints
│       └── verify.py              # quote and source revalidation
├── frontend/                      # src/ (App, components/, hooks/, API client, tests), vercel.json
├── data/                          # pdfs/, web_html/, web_pdfs/
├── memory/                        # topics/, history/, unanswered/, attempts/ (legacy)
├── chroma_db/                     # local-only Chroma data
├── docs/                          # PRD, TRD, decisions, diagram
└── tests/                         # backend tests (unittest)
```

HTTP concerns live in `backend/api`, orchestration shared by routes and jobs in `backend/services`, and the research and retrieval core in `backend/core`. Core modules do not import from `api` or `services`.

## 6. Data model and persistence contracts

### 6.1 Stores

| Store | Location | Format | Role | Written by | Retention |
| --- | --- | --- | --- | --- | --- |
| Topic audits | `memory/topics/<slug>.json` | `TopicArchive` (Pydantic, extra fields kept) | Source of truth for web claims | Research save | Until replaced; prior version copied to history |
| Audit history | `memory/history/` | Earlier audit versions | Audit trail | Each audit replacement | Unbounded (see §17) |
| Unanswered questions | `memory/unanswered/<id>.json` | Versioned record (§6.3) | Explains runs that verified nothing; never feeds answers | Research, when no claim is verified | Unbounded (see §17) |
| Legacy attempts | `memory/attempts/` | Counts only | Superseded by unanswered questions | No longer written | Read-only legacy |
| Backup (hosted) | Private Hugging Face dataset, or a directory | Copies of topic audits, history, unanswered records, attempts, PDFs, snapshots, and research run logs | Durable copy when the host's disk is temporary | Backup timer and shutdown | Never deleted from |
| Web snapshots | `data/web_html/`, `data/web_pdfs/` | Content-addressed files + `.txt` sidecars | Evidence the quotes were verified against | Fetching | Unbounded |
| User PDFs | `data/pdfs/` | Original files | User-provided sources | Upload | Until the user removes them |
| Vector index | `chroma_db/` or Chroma Cloud | Chroma collection with fingerprint metadata | Derived search index | Research save, upload, reindex | Rebuildable |
| API log | `logs/spymonk-api.log` | Text, same format as the console | Diagnosis after the fact | API process holding the lock | Rotated: 5 × 5 MB |
| Research run logs | `logs/research/<UTC time>-<outcome>-<topic>-<id>.json` | Developer section (§6.3) plus outcome | Pipeline quality across runs | API process holding the lock | Newest `RESEARCH_LOG_KEEP` (500) |
| Diagnostics | `logs/diagnostics/` | Investigation records | Reference | Developers | Manual |
| Jobs, rate limits, LLM budget | Process memory | — | Runtime state | API process | Lost on restart |

All paths resolve under `SPYMONK_DATA_DIR`, and the index stores only paths relative to it.

### 6.2 Claims and evidence

A claim record carries `id`, `topic`, `claim`, a lead `quote` with its source, `validation_status`, `supporting_origins`, `supporting_evidence`, `contradicting_evidence`, `cross_check_incomplete`, and `verified_at`. Each evidence item records its source URL, registrable origin, verdict, verbatim quote, and the snapshot's SHA-256. The status vocabulary is defined in [DECISIONS.md](DECISIONS.md#evidence-status-vocabulary). Only `corroborated`, `pdf_resolved`, and `local_source` are answerable.

### 6.3 Unanswered-question record

One JSON file per research run that verified no claim. The `schema_version` is currently 1.

| Section | Audience | Contents |
| --- | --- | --- |
| `id`, `created_at`, `question`, `topic_key`, `topic_slug`, `request_id` | Both | Identity and correlation with backend logs |
| `outcome` | Asker | The fixed message, a reason code and label, a plain-language explanation, claim and failure counts, earlier verified claims |
| `summary` | Asker | Queries, results, sources read, independent sites, claims found and verified, duration |
| `process` | Asker | The seven pipeline steps, each `done`, `stopped`, or `not_reached`, with a one-line detail |
| `claims` | Asker | Each claim the model found, its status label, why it was not verified, supporting sites, and a short quote |
| `sources`, `suggestions` | Asker | Sources read; what to try next |
| `developer` | Developer | Configuration snapshot, stage timings, plan, search results, collection bytes, per-source read and extraction results, origin diversity, candidate selection, every cross-check call (status, duration, split, error types), verdict counts, per-claim checked and failed sources, diagnostics, and the event timeline |

Reason codes: `no_search_results`, `no_readable_sources`, `no_candidate_claims`, `no_independent_support`, `conflicting_evidence`, `processing_failure`.

Records hold claims, URLs, and quotes of at most 300 characters, as audits do. They never hold prompts, full source text, credentials, or provider error messages; errors appear only as type names.

### 6.4 Identifiers

| Identifier | Form | Property |
| --- | --- | --- |
| Topic key and slug | NFKC case-folded topic; readable prefix plus a 12-hex SHA-256 digest | Spelling variants map to one audit; filenames cannot collide |
| Claim ID | `sha256("claim\|topic_key\|normalized claim")[:32]` | Re-research updates claims in place |
| PDF chunk ID | Document SHA-256, chunker version, chunk index | Re-uploads update rather than duplicate |
| Unanswered-question ID | 14-digit UTC timestamp followed by 16 hex characters | Sortable by time; validated by pattern before any file access |

### 6.5 Write ordering and recovery

- **Atomic writes.** Every JSON write goes to a temporary file in the same directory, is `fsync`ed, and is then renamed into place. History and unanswered records are linked, never overwriting an existing file.
- **Audit before index.** A save writes the audit with `index_state: pending`, upserts the index, then marks the audit `indexed`, or `stale` if indexing failed. A crash between the two leaves `pending` or `stale`, which the reindex tool reconciles.
- **History before replace.** The existing audit is copied to history before it is replaced. If that copy fails, the replace is not attempted.
- **Unanswered records are best effort.** If writing one fails, the error is logged and the run still returns its outcome, without a report link.

### 6.6 Schema versioning

Unanswered-question records carry `schema_version`, and readers skip records they cannot validate. Topic audits are validated structurally (`TopicArchive`) but carry no version field yet (§17). The index records a fingerprint (`spymonk_schema`, embedding provider and model, chunker); a different embedding model is refused, and an older fingerprint is reported as "rebuild recommended".

## 7. Runtime flows

### 7.1 Ask from memory (RAG)

1. React sends the question to `POST /api/ask`.
2. Chroma retrieves passages with eligibility filtered inside the vector query (local PDFs, or web claims with an answerable status). Duplicates and matches below `RETRIEVE_MIN_SIMILARITY` are dropped, so an unrelated question returns the not-found answer without an LLM call.
3. When enabled, source quotes are re-verified against content-addressed snapshots whose recorded hash must still match.
4. The LLM receives only the eligible evidence; the API returns the answer with provenance.
5. Web research runs only when the user enabled research-on-miss. It goes through the job queue, followed by one retrieval retry. If research verifies nothing, the response carries the unanswered message and report ID.

### 7.2 Research a topic

1. `POST /api/research/jobs` validates the request and queues a job, one per topic at a time; it returns 429 when capacity is full.
2. Unless a refresh was requested, the job checks the archive: an exact topic match by index metadata, then semantic retrieval.
3. On a miss, the LLM planner prepares two to six queries (or the request supplies them). Invalid planner output falls back to labeled baseline queries.
4. Queries run in order. Result pages are fetched in parallel (`FETCH_CONCURRENCY`, two per host) up to `MAX_WEB_HTML` pages, `MAX_WEB_PDFS` linked PDFs, and the run's byte budget.
5. Sources are parsed, and claims extracted in parallel (`LLM_CONCURRENCY`). A candidate claim needs a verbatim quote from its source, and at most `MAX_CANDIDATE_CLAIMS_PER_TOPIC` candidates are kept.
6. Each candidate is planned against up to `MAX_CROSS_CHECKS_PER_CLAIM` sources: its own source first, then one source per other registrable domain. One call judges up to `CROSS_CHECK_BATCH_SIZE` claims against one source. If a batched reply is malformed, its claims are re-checked one per call instead of resending the same prompt.
7. Each claim gets a status (§6.2), and a claim that missed a planned check is held back as `incomplete`. The run then takes one of the outcomes in §7.3.
8. If claims were saved, the job answers the question from them (step `answer`, without re-verification, since they were just verified). The claims are already stored, so a model failure or cancellation here skips only the answer.
9. Clients follow `GET /api/research/jobs/{id}/events` (replayable from any event index) and may cancel with `DELETE`. A deadline (`RESEARCH_MAX_SECONDS`) and the daily LLM ceiling apply.

### 7.3 Research outcomes

| Outcome | Condition | Written | Job | Shown to the user |
| --- | --- | --- | --- | --- |
| Saved | At least one claim verified, no step failed | Audit replaced; claims upserted; stale claims removed | `succeeded` | The answer from the new claims, then stored and unresolved counts |
| Saved with warnings | At least one verified, some step failed | Verified claims added; earlier claims not re-checked are kept; nothing removed; affected claims held back as `incomplete` | `succeeded` | The answer, then stored, held-back, and kept counts |
| Unanswered | No claim verified, including no search results, no readable source, or no candidate claim | No audit or index change; one unanswered-question record | `succeeded`; `memory_updated: false` with `inquiry_id` | "We cannot verify the claims for providing you the answers of your question.", the reason, the PDF report, and the developer log. If earlier verified claims exist, it says they still answer |
| Failed | An infrastructure or provider error stops the run (§8) | Nothing | `failed` with status and message | The error with a request ID |
| Cancelled or timed out | The user cancelled, or the deadline passed | Nothing | `cancelled`, with HTTP status 409 (cancelled) or 504 (deadline) | "Nothing was saved" |

In the pipeline view, the save step of an unanswered run is `skipped` and the run is labeled "No verified answer", not as a failure.

### 7.4 Unanswered-question reports

- `GET /api/inquiries` lists recent records, newest first.
- `GET /api/inquiries/{id}/report.pdf` renders the asker's report on demand from the stored record. It covers the question, the outcome and reason, four headline numbers, the seven steps, the claims the model found with their status, sources read, and next steps; it is usually two pages.
- `GET /api/inquiries/{id}/developer-log` returns the record's technical section as JSON, with automatic diagnostics (§11.2).

Reports are generated from the record, so a report can be re-downloaded later and always matches the run.

### 7.5 Upload a PDF

1. The browser sends PDFs directly to FastAPI; uploads never pass through a Vercel Function.
2. The API checks file count, extension, PDF signature, and per-file size, and skips files whose bytes are already in the batch or already indexed (reported as `duplicates`).
3. Files are staged under the PDF directory and parsed in a separate process with time, page, and text limits. Every file in a batch must yield searchable chunks before any is promoted.
4. Validated files are promoted to unique names, then indexed. A failure removes the temporary and newly promoted files and leaves older PDFs untouched.

### 7.6 Rebuild the index

`python -m backend.core.reindex --apply`, or `POST /api/memory/reindex`, builds a fingerprinted collection from `data/pdfs/` (identical files once; image-only files skipped) and the answerable claims in the audits, then swaps it in. A failed build leaves the live index untouched. `GET /api/memory/reindex` previews the plan.

## 8. Failure handling and degradation

| Failure | Handling | Effect | Outcome |
| --- | --- | --- | --- |
| Every search query fails (provider unavailable) | `SearchProviderUnavailableError` | Nothing collected | Failed, 503 |
| Search returns no results | Recorded in the trace | — | Unanswered (`no_search_results`) |
| A fetch is blocked, too large, or fails | Source skipped and logged by host | Fewer sources | Continues |
| No fetched source has readable text | — | — | Unanswered (`no_readable_sources`) |
| A PDF parse times out or crashes | Isolated subprocess; source skipped | Counted as `source_read` | Continues, with warnings |
| Extraction call times out or errors | The client retries once (`LLM_MAX_RETRIES`); the source is then skipped | Source adds no candidates | Continues, with warnings |
| Extraction reply is malformed JSON | Retried once with the same prompt | As above if it fails again | Continues, with warnings |
| Cross-check batch reply is malformed | Split into one call per claim | Only claims whose own check fails are affected | Continues |
| Cross-check call times out or errors | Client retry, then every claim in the batch is marked unchecked for that source | Those claims, if otherwise answerable, become `incomplete` | Saved with warnings, or unanswered |
| Model quote is not verbatim | Verdict discarded | No support from that source | Continues |
| Daily LLM ceiling reached mid-run | Remaining calls fail | As timeouts | Saved with warnings, or unanswered (`processing_failure`) |
| Daily LLM ceiling reached before planning | `LLMBudgetExceeded` | — | Failed, 429 |
| Provider rejects the key or model | Mapped by `describe_failure` | — | Failed, 502, with a configuration hint |
| Index write fails after the audit write | Audit marked `stale` | Audit current, index behind | Failed, 502; the reindex tool repairs it |
| Unanswered record cannot be written | Logged | No report link | Unanswered |
| Answer from new claims fails or is cancelled | Step `answer` skipped with advice to ask in the chat | Claims stay saved | Saved; no answer shown |
| Log file or run log cannot be written | Logged to the console | — | No effect on the run |
| Backup cannot be restored at startup | Retried three times | — | Startup refused (the archive would otherwise look empty) |
| Backup sync fails | Logged; changed files stay pending | — | Retried on the next interval |
| Index rebuild at startup fails | Logged | Files intact; archive not searchable until rebuilt | App serves; rebuild from the UI |
| Deadline passed or cancelled | Checked between stages and model calls | Nothing written | Cancelled (409, or 504 for the deadline) |
| Process crash | Jobs are lost | Nothing written unless the audit write completed | Client reconnect fails; reindex reconciles |

## 9. LLM integration contract

### 9.1 Calls per research run

| Call | Count (defaults) |
| --- | --- |
| Query planning | 1 |
| Claim extraction | One per readable source, at most `MAX_WEB_HTML + MAX_WEB_PDFS` = 10; plus one retry per malformed reply |
| Cross-checks | One per (source, batch of up to 6 claims): 10–17 for 12 candidates checked against up to 5 sources each; plus up to 6 single-claim calls per malformed batch |
| Answer composition | 1 per answered question, including the answer after a saving research run |

A run normally makes about 20–30 calls. All calls count against `LLM_DAILY_CALL_LIMIT`, and transport retries inside the client do not.

### 9.2 Output contract

- Every prompt requests a single JSON object. Replies are parsed by `llm_json`, and the expected keys and types are checked.
- A malformed reply raises `LLMOutputError`. It never becomes an empty result: "no claims" and "unreadable reply" are different outcomes.
- At `LLM_TEMPERATURE` 0.2, an identical prompt often reproduces an identical defect. Retries that need a different result change the prompt shape (one claim per call) rather than resending it.
- Source text is wrapped in `<source_text>` tags it cannot close, and prompts state that its contents are evidence, not instructions.

### 9.3 Model qualification

Before changing `LLM_MODEL` or `LLM_BASE_URL`, qualify the model by replaying saved source snapshots through extraction and cross-checks with the real provider. Then review the developer-log diagnostics. Accept the model only if:

- at least 99% of replies are valid JSON (`malformed_model_output` rarely appears);
- the slowest 5% of calls on full-length sources (45,000 characters) finish within `LLM_TIMEOUT_SECONDS` (`llm_timeouts` absent);
- verbatim quoting is reliable (`non_verbatim_quotes` and `extraction_quote_mismatch` low).

Observed on 2026-10-19:

- `deepseek-ai/deepseek-v4.1-flash` timed out repeatedly.
- `nvidia/nemotron-3.5-lightning-30b-a3b` returned about one malformed reply per 11 cross-check calls (an unquoted string value). Three of its six extraction calls in one replay exceeded 60 seconds.

### 9.4 Report rendering

PDFs are rendered in-process with fpdf2. Fonts resolve from `REPORT_FONT_DIR`, then bundled `backend/assets/fonts/` if present, then system fonts (Noto Sans, Segoe UI, DejaVu Sans, Liberation Sans, Arial). Devanagari falls back to Noto Sans Devanagari or Nirmala UI, and uharfbuzz shapes it. With no Unicode font installed, reports fall back to Helvetica with Latin-1 substitution and log a warning.

## 10. HTTP API

| Method | Route | Purpose | Notes |
| --- | --- | --- | --- |
| GET | `/api/health` | Liveness | Public; no token required. |
| GET | `/api/status` | Runtime status | LLM/embedding settings, vector store, index state and rebuild advice, PDF counts. |
| GET | `/api/topics` | Saved topics | Names, audit claim counts, searchable claims. |
| POST | `/api/research/plan` | Query planning only | Two to six queries; no web fetch. |
| POST | `/api/research/jobs` | Start research | 202 with a job ID; a topic already running returns its job. |
| GET | `/api/research/jobs/{id}` | Job status | Status, timestamps, result or error. |
| GET | `/api/research/jobs/{id}/events?after=N` | Job progress | Server-sent events from index N; disconnecting does not cancel. |
| DELETE | `/api/research/jobs/{id}` | Cancel research | Stops between steps; saves nothing. |
| POST | `/api/research` | Research and wait | Compatibility route. |
| POST | `/api/research/stream` | Research with live progress | Compatibility route; closing the stream cancels the job. |
| POST | `/api/ask` | Ask the archive | Up to 2,000 characters; optional research-on-miss. |
| POST | `/api/documents` | Upload PDFs | At most 10 files, 20 MiB each; duplicates skipped. |
| DELETE | `/api/memory` | Clear the index | PDFs and audits are kept. |
| GET / POST | `/api/memory/reindex` | Preview / rebuild the index | Synchronous; can take minutes. |
| GET | `/api/inquiries?limit=N` | Unanswered questions | Newest first; at most 200. |
| GET | `/api/inquiries/{id}/report.pdf` | Asker's report | `application/pdf` attachment. |
| GET | `/api/inquiries/{id}/developer-log` | Developer log | `application/json` attachment. |

A research result carries `answer` and `sources` when the archive or the newly saved claims answered the question. Its `archive` carries `memory_updated`, `failure_reason`, `held_back_claims`, `carried_over_claims`, `previous_verified_claims`, `inquiry_id`, `outcome_message`, and `outcome_explanation`, plus claim and source summaries. Quotes and local paths stay in the audit.

Every route except `/api/health` requires `Authorization: Bearer <APP_API_TOKEN>`, or a loopback client when no token is set.

- **Request checks:** browser writes must come from `APP_ALLOWED_ORIGINS`, and every request must use a `Host` in `APP_ALLOWED_HOSTS`.
- **Rate limits:** research, questions, uploads, and maintenance are rate-limited per client, returning 429 with `Retry-After`.
- **Body limits:** enforced before multipart parsing: 10 × 20 MiB plus 1 MiB for `/api/documents`, and 1 MiB elsewhere.
- **Response headers:** responses carry `X-Request-ID`, `X-Content-Type-Options`, `Referrer-Policy`, and a Content-Security-Policy. CORS exposes `X-Request-ID` and `Content-Disposition`.

## 11. Observability

### 11.1 Console logs

Each request is logged with a request ID, method, route, status, and duration, and background jobs keep the ID of the request that started them. The process holding the data-directory lock also writes the log to `logs/spymonk-api.log` (rotated); tests and imports never open it. Logs contain stage durations, fallbacks, skipped sources, error types, and outcome counts. They never contain prompts, document text, credentials, full source URLs, or provider error text, and search queries are logged only as a hash. Set `LOG_LEVEL` to tune verbosity.

### 11.2 Research trace and developer log

Each research run collects a `ResearchTrace`: pipeline events with timings, the plan, search results, per-source read and extraction results, candidate selection, every cross-check call, and verdict counts. Every run that completes (saved, saved with warnings, or unanswered) writes it to `logs/research/` as a developer log. For unanswered runs it is also stored in the record's `developer` section and downloadable from the UI. Runs that end in an error leave only log lines. Diagnostics are derived automatically:

| Code | Finding | Typical fix |
| --- | --- | --- |
| `no_search_results`, `no_readable_sources` | The run had nothing to analyze | Check the search provider and fetch logs for the request ID |
| `low_origin_diversity` | Few registrable domains, or one supplied half or more of the sources | Cap sources per domain; widen queries |
| `candidate_cap_order_bias` | The candidate cap filled from the first sources only | Select candidates round-robin across sources |
| `single_origin_support` | Claims lacked a second independent domain | Increase source diversity (do not relax the rule) |
| `unsupported_after_extraction` | Claims with a verbatim quote that no cross-check supported | Align extraction and judging prompts |
| `malformed_model_output`, `llm_timeouts`, `llm_call_errors` | Model contract violations | Qualify or change the model (§9.3) |
| `non_verbatim_quotes`, `extraction_quote_mismatch` | Quotes not copied exactly | Tighten prompts; check source clipping |
| `incomplete_checks`, `conflicting_sources`, `sources_without_claims` | Held-back, contradicted, or off-topic material | Rerun; review evidence; refine planning |

## 12. Security, privacy, and reliability

- Keep the LLM, Chroma Cloud, and provider credentials only in backend secrets. Reports and records never include them.
- Set `APP_API_TOKEN`; without it only loopback clients are served. The token is one shared secret, not per-user authentication.
- Restrict `APP_ALLOWED_ORIGINS` to exact trusted origins (never `*`) and `APP_ALLOWED_HOSTS` to the backend hostnames.
- Keep the upstream proxy body ceiling at or below the API's (201 MiB for PDF uploads, 1 MiB elsewhere).
- Set `SPYMONK_DATA_DIR` to a persistent, backed-up volume.
- Web collection resolves each host once, rejects non-public addresses (including IPv4 embedded in IPv6), pins the connection to the checked address, allows only ports 80/443, re-validates redirects, and bounds every fetch by time and bytes.
- Untrusted PDFs are parsed in a separate process with time, page, and text limits.
- Report IDs are validated against a strict pattern before any file access. Reports are rendered from stored records only, never from request input.
- Fail closed on unsupported quotes, malformed evidence, mismatched snapshot hashes, unresolved conflicts, and unfinished checks.
- A similarity match is a retrieval signal, not proof. Answers keep quotes and provenance.
- Search, LLM, and hosted embedding providers may receive research terms or excerpts; the privacy notice and PRD must match the production configuration.

## 13. Performance, capacity, and limits

| Limit | Default | Setting |
| --- | --- | --- |
| Concurrent research jobs / queued jobs | 1 / 4 | `RESEARCH_MAX_CONCURRENT_JOBS`, `RESEARCH_MAX_QUEUED_JOBS` |
| Research deadline | 900 s | `RESEARCH_MAX_SECONDS` |
| Pages / PDFs per run | 6 / 4 | `MAX_WEB_HTML`, `MAX_WEB_PDFS` |
| Download budget per run | 80 MiB | `RESEARCH_MAX_FETCH_BYTES` |
| Parallel fetches | 4 total, 2 per host | `FETCH_CONCURRENCY`, `FETCH_PER_HOST_CONCURRENCY` |
| Candidate claims / checks per claim / batch size | 12 / 5 / 6 | `MAX_CANDIDATE_CLAIMS_PER_TOPIC`, `MAX_CROSS_CHECKS_PER_CLAIM`, `CROSS_CHECK_BATCH_SIZE` |
| Parallel model calls per run | 3 | `LLM_CONCURRENCY` |
| Model call timeout / retries | 60 s / 1 | `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` |
| Daily model calls | 2,000 | `LLM_DAILY_CALL_LIMIT` |
| Requests per minute per client | Research 6, questions 30, uploads 10, maintenance 3 | `RATE_LIMIT_*_PER_MINUTE` |

Use remote embeddings on a hosted backend unless the instance is sized and its cold start tested for Sentence Transformers; `EMBEDDINGS_WARMUP=true` loads the local model at startup. A two-page report renders in about 0.4 s.

## 14. Configuration

Local mode keeps vectors under `chroma_db/` (`VECTOR_STORE_PROVIDER=chroma-local`), files under `data/`, and records under `memory/`. Hosted mode must set `SPYMONK_DATA_DIR` to a persistent volume. The app never silently falls back from a selected cloud provider to local storage.

| Variable | Required | Purpose |
| --- | --- | --- |
| `LLM_API_KEY` (or a supported provider key) | Yes, for ask and research | Chat model credentials. `OPENAI_API_KEY` is used only without a custom `LLM_BASE_URL`. |
| `LLM_BASE_URL`, `LLM_MODEL` | Optional | OpenAI-compatible endpoint and model; qualify a new model first (§9.3). |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_CONCURRENCY`, `LLM_DAILY_CALL_LIMIT` | No | Call timeout, retries, parallelism, daily ceiling (0 disables). |
| `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL` | No; local default | `huggingface` or `openai`. Changing either requires a reindex. |
| `EMBEDDING_API_KEY`, `EMBEDDING_BASE_URL` | Remote embeddings | Separate credentials, so the LLM key is not sent to the embedding provider. |
| `EMBEDDING_MAX_TOKENS`, `RETRIEVE_MIN_SIMILARITY`, `EMBEDDINGS_WARMUP` | No | Chunk sizing, weak-match cutoff, startup model load. |
| `VECTOR_STORE_PROVIDER`, `CHROMA_API_KEY`, `CHROMA_TENANT`, `CHROMA_DATABASE` | Cloud only | Vector store selection and Chroma Cloud credentials. |
| `SPYMONK_DATA_DIR`, `SPYMONK_INSTANCE_LOCK` | Hosted API | Persistent data root; refuse a second process (default on). |
| `APP_API_TOKEN`, `APP_ALLOWED_ORIGINS`, `APP_ALLOWED_HOSTS` | Production | Access token, exact browser origins, API host names. |
| `RESEARCH_*`, `FETCH_CONCURRENCY`, `RATE_LIMIT_*_PER_MINUTE` | No | Limits in §13. |
| `LOG_TO_FILE`, `SPYMONK_LOG_DIR`, `LOG_FILE_MAX_BYTES`, `LOG_FILE_BACKUPS`, `RESEARCH_LOG_KEEP` | No | File logging on/off (default on), log directory (default `SPYMONK_DATA_DIR/logs`), rotation size and count, research run logs kept (0 keeps all). |
| `SPYMONK_HOSTED`, `APP_API_DOCS`, `APP_FRAME_ANCESTORS` | No | Hosted mode (default on when `SPACE_ID` is set), API schema visibility (default off when hosted), who may frame the UI. |
| `BACKUP_TARGET`, `BACKUP_HF_REPO`, `BACKUP_HF_TOKEN`, `BACKUP_DIRECTORY`, `BACKUP_INTERVAL_SECONDS`, `BACKUP_ALLOW_PUBLIC` | Hosted with a temporary disk | Where the archive is backed up (`hf-dataset` or `directory`), the dataset and its fine-grained token, and the sync interval. A public dataset is refused unless allowed. |
| `REBUILD_INDEX_ON_START` | No | Rebuild a missing index from audits and PDFs at startup (default on when hosted). |
| `REPORT_FONT_DIR` | No | Directory with `NotoSans-Regular.ttf`, `NotoSans-Bold.ttf`, and `NotoSansDevanagari-Regular.ttf` for consistent PDF reports. |

## 15. Testing and quality gates

- **Backend unit and integration tests** (`python -m unittest discover -s tests`) cover the evidence rules, save outcomes, partial saves, unanswered records and both reports (including a Devanagari line-wrap regression and the no-font fallback), job lifecycle, rate and spend limits, fetch safety, index lifecycle, and the API contracts. They use temporary data directories and mocked providers. Running the suite with `SPYMONK_DATA_DIR` set to an empty directory leaves it empty, which shows no test touches a real index or log.
- **Frontend tests** (`npm --prefix frontend test`) cover the API client, pipeline state, and outcome messages; `npm --prefix frontend run build` must succeed.
- **Live replay** of saved snapshots against the real model is the tool for diagnosing provider behavior and qualifying models (§9.3). It runs with a scratch data directory and never writes to the live archive.
- **CI** (`.github/workflows/ci.yml`) runs the same checks plus `pip-audit` and `npm audit`. Dependencies are pinned in `constraints.txt`.

## 16. Acceptance criteria

1. `npm --prefix frontend run build` produces a bundle deployable from the Vercel `frontend/` root, and `npm --prefix frontend test` passes.
2. The frontend API client works with an empty `VITE_API_BASE_URL` locally and an HTTPS API origin in production.
3. FastAPI starts with `uvicorn backend.main:app` and `uvicorn app:app`; a second process on the same data directory refuses to start.
4. Every private route rejects missing or wrong tokens; CORS and write-origin validation accept configured origins and reject others.
5. Local Chroma ingestion and retrieval persist across restarts. Cloud mode stores vectors remotely and fails clearly when credentials are absent.
6. Audits, PDFs, snapshots, and unanswered records resolve under the configured data directory, and the index stores only relative paths.
7. Responses expose a request ID, and failures and fallbacks emit request-correlated logs without request or document content.
8. With no archive evidence, ask returns the standard not-found response, and web research starts only when explicitly enabled.
9. Invalid, oversized, duplicate, and image-only PDFs return explanatory results, never a false successful-index state.
10. A cancelled, failed, degraded, or unanswered run never removes previously verified claims.
11. A run that saves verified claims shows an answer composed from them, or, if that step fails, keeps the claims and says how to get the answer.
12. A run that verifies no claim writes no audit or index entry, records one unanswered question, and shows "We cannot verify the claims for providing you the answers of your question." with a downloadable PDF report and developer log.
13. A malformed batched cross-check reply affects at most the claims whose own single-claim check also fails.
14. The API log and a developer log for every completed research run are written under the data directory's `logs/`.
15. On a host whose disk is wiped, a restart restores the archive from the backup and rebuilds the index before serving; a hosted deployment without `APP_API_TOKEN` refuses to start.
16. Public deployment is blocked until the revised cloud privacy boundary is approved.

## 17. Known limitations, risks, and follow-up decisions

- **Product approval.** The PRD still describes a local-first single-user product. The hosted deployment and the unanswered-question reports (which store claim text) need PRD and privacy approval before public release.
- **Corroboration starvation.** Source collection has no per-domain cap, and candidate selection is first come, first served. A result set dominated by one site, as in the 2026-10-19 Docker run (3 of 6 sources from docker.com), can leave every claim with one origin. The developer log flags both (`low_origin_diversity`, `candidate_cap_order_bias`). Proposed fix: cap sources per registrable domain and select candidates round-robin.
- **Model JSON compliance.** Provider JSON mode, or a narrow repair of known defects, would remove most malformed replies; neither is enabled, pending qualification per provider.
- **Retention.** Audit history, snapshots, and unanswered records grow without bound. Decide on retention periods and a pruning tool before public hosting.
- **Audit schema version.** Topic audits have no `schema_version`. Add one before the next audit format change.
- **Errored runs.** A run that ends in an error (provider unavailable, deadline) writes no run log, only log lines. Writing a partial trace for those would complete the picture.
- **Single process.** JSON records, snapshots, jobs, and rate limits are process-local and filesystem-backed. Moving audits and jobs to SQLite, or to object storage plus a database for multiple instances, is a deferred decision.
- **Authentication.** The bearer token is not per-user authentication or tenancy; do not expose the API as a shared multi-user archive.
- **Search source.** `ddgs` scrapes search engines without an API, and the archive keeps full copies of third-party pages. A licensed search API and a retention policy are product decisions for public hosting.
- **Vector provider.** Chroma Cloud is the configured hosted option; another provider needs a store adapter and a migration plan.
- **Bloom filters.** A Bloom filter only answers probabilistic exact-key membership. It cannot retrieve related passages or validate evidence, so it stays out of the retrieval path; it may be reconsidered only as a negative cache for a measured, costly exact lookup.
- **Out of scope.** Original-language translation review (ADR-013) and OCR remain separate product decisions.
- **Search from cloud hosts.** DuckDuckGo often blocks requests from cloud providers' addresses, so research on a hosted Space may fail with "web search is temporarily unavailable". A licensed search API (GAP-10) is the lasting fix.
- **Diagram.** The draw.io architecture diagram predates the unanswered-question store, report routes, and the Spaces deployment.
