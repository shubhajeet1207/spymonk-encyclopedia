# SpyMonk Encyclopedia: Remediation Brief for a Coding Agent

> **Audience:** an LLM coding agent working in this repository with no prior context.
> **Prepared:** 2026-10-19. Based on a full read of the backend, frontend, tests and docs, plus read-only checks against the local index and data.
> **Your task:** fix the issues in §5, in the phase order given in §6. Make each change small and give it a regression test. Read §1–§4 before touching any code.

---

## 1. What this system is

SpyMonk is a single-user research archive with these layers:

- **Backend:** FastAPI (`backend/`).
- **Frontend:** React 19 + Vite 8 (`frontend/`), deployed separately to Vercel.
- **Storage:** a Chroma vector index, JSON "audit" files per topic, and on-disk snapshots of every fetched web page or PDF.
- **Answers:** produced by an OpenAI-compatible LLM (NVIDIA NIM by default) using only retrieved evidence.

### Request flows

- **`POST /api/research`** and **`POST /api/research/stream`** (`backend/api/routes/research.py`). The route first tries memory:
  1. Check the index.
  2. Look up the exact topic through Chroma metadata.
  3. Fall back to semantic search.
  4. Optionally re-verify the stored quotes.
  5. Compose an LLM answer.

  If memory has no answer, it researches the web:
  1. Make an LLM search plan.
  2. Run the planned queries on DDGS metasearch.
  3. Fetch HTML pages and PDFs.
  4. Extract candidate claims with the LLM, once per source.
  5. Cross-check each claim against each source with the LLM.

  Claims supported by at least 2 different hostnames are indexed in Chroma. Every claim goes into `memory/topics/<slug>.json`.
- **`POST /api/ask`** (`routes/questions.py`): semantic search, optional re-verify, then an LLM answer. If the client sets `research_on_miss`, it also researches the web.
- **`POST /api/documents`** (`routes/documents.py`): uploads PDFs to `data/pdfs/`, then chunks, embeds and indexes them in Chroma.
- **`DELETE /api/memory`**: clears the vector index and keeps all files.
- **`GET /api/status`**, **`GET /api/topics`**, **`GET /api/health`**.

### Storage

| Store | Location | Written by | Key |
|---|---|---|---|
| Chroma collection `personal_encyclopedia` | `chroma_db/` locally, or Chroma Cloud | `backend/core/store.py` | sha256 of content + metadata (`store.py:81-91`) |
| Topic audit JSON | `memory/topics/<slug>.json` | `backend/core/memory.py` | lossy slug of the topic string |
| Source snapshots | `data/web_html/`, `data/web_pdfs/` | `backend/core/scraper.py` | filename derived from the URL |
| User PDFs | `data/pdfs/` | `routes/documents.py` | random 8-hex prefix + cleaned filename |

**Evidence statuses** (see `docs/DECISIONS.md`):
- Answerable: `local_source`, `corroborated`, `pdf_resolved`.
- Audit-only: `single_source`, `conflicted`.

**Environment:** Windows 10 with PowerShell; Python 3.11.5 venv at `.venv`; chromadb 1.5.9; pydantic 2.14.1. **The project is not a git repository.**

### State of local data on 2026-10-19

Migrations are cheap right now:
- `memory/topics/` contains no JSON files.
- The Chroma index holds 111 chunks, all from one uploaded PDF (`abceedf8-docker-tutorial.pdf`), and no web claims.
- `data/pdfs/` holds 4 files, of which 3 are unindexed image-only copies (see DATA-04).

---

## 2. Ground rules

1. **Secrets.** `.env` contains a live LLM API key. Never open, print, copy, or commit it. Use `.env.example` to learn the variable names.
2. **User data.** Do not modify or delete anything under `data/`, `chroma_db/` or `memory/topics/`.
   - Tests must use temp dirs. Patch `config.CHROMA_DIR`, `config.MEMORY_DIR`, `config.PDF_DIR`, `config.WEB_HTML_DIR` and `config.WEB_PDF_DIR`.
   - These paths are computed at import time, so setting `SPYMONK_DATA_DIR` after import has no effect.
3. **Fail-closed evidence (ADR-015) is the core invariant.** Anything that can't be verified must never become answerable. If a fix changes documented behaviour, update `docs/DECISIONS.md` and `docs/TRD.md` in the same change.
4. **Logging policy.**
   - Log exceptions with `safe_exception_context(exc)` from `backend/observability/logging.py:29`.
   - Never log exception messages, prompts, document text, file contents, or full source URLs.
   - Hostnames are fine to log.
5. **Baseline tests.**
   - Run `.\.venv\Scripts\python.exe -m unittest discover -s tests`. All 20 tests passed on 2026-10-19.
   - pytest is not installed.
   - Discovery with `-t .` fails because `tests/` has no `__init__.py`.
   - For frontend changes, also run `npm --prefix frontend run build`.
6. **Scope.** Make one issue, or one tightly coupled group, per change, and add a test for each fix. Finish Phases 1–2 before starting the Phase 3 refactors.
7. **Reindexing.** DATA-08, DATA-09 and DATA-10 require rebuilding the vector index. Build the reindex tool (DATA-11) first, and ask the user before wiping or rebuilding their index.
8. **Escalate the decisions listed in §7.** Ask the user; don't choose for them.

---

## 3. Confidence labels

- **Verified:** reproduced on this machine. Each such issue says how.
- **Code-read:** follows deterministically from the code, but was not executed.
- **Needs check:** plausible. Confirm it with a test before fixing.

---

## 4. Issue register

| ID | Sev | Title | Confidence |
|---|---|---|---|
| SEC-01 | Critical* | No authentication on any endpoint | Code-read |
| SEC-02 | High | No rate limit, cost ceiling, or concurrency cap on LLM and web research | Code-read |
| SEC-03 | High | SSRF via DNS rebinding (the URL is checked, then resolved again to connect) | Code-read |
| SEC-05 | High | "Independent origins" counted by raw hostname, so self-corroboration is possible | Code-read |
| DATA-01 | High | A research run with zero verified claims deletes existing verified memory | Code-read |
| DATA-02 | High | Topic slug collisions overwrite audit files (all non-Latin topics → `topic.json`) | **Verified** |
| ARCH-01 | High | Long research runs inside the HTTP request: no jobs, no cancel, races | Code-read |
| ARCH-02 | High | Chroma and JSON have no consistency model and no shared key | Code-read |
| PERF-01 | High | Every cross-check resends up to 45k chars of source (~0.8M tokens per run, worst case) | Code-read |
| PERF-02 | High | The whole pipeline is sequential (fetches and LLM calls) | Code-read |
| SEC-04 | Medium | The IP filter misses NAT64 and 6to4 encodings of private IPs | **Verified** |
| SEC-06 | Medium | Prompt injection through fetched source text | Code-read |
| SEC-07 | Medium | No request-body size limit, so uploads can exhaust disk | Code-read |
| SEC-08 | Medium | Untrusted PDFs parsed in-process with no timeout | Code-read |
| SEC-09 | Medium | Local DNS rebinding can read the topic list (no Host validation) | Code-read |
| DATA-03 | Medium | `DELETE /api/memory` fails on Windows (`rmtree` on an open SQLite file) | **Verified** |
| DATA-04 | Medium | Failed uploads leave orphaned files, and `/api/status` miscounts | **Verified** |
| DATA-05 | Medium | A missing `origin` field is treated as a trusted local PDF (fails open) | Code-read |
| DATA-06 | Medium | Audit JSON reads can crash the status endpoints; writes aren't atomic | Code-read |
| DATA-07 | Medium | One failed extraction call aborts the whole research run | Code-read |
| DATA-08 | Medium | Record IDs aren't deterministic: re-uploads and refreshes create duplicates | **Verified** |
| DATA-09 | Medium | Chunks are 500 tokens but the embedding model reads only 256 | **Verified** |
| DATA-10 | Medium | The index doesn't record which embedding model built it | Code-read |
| DATA-11 | Medium | No reindex path from the audit files and PDFs | Code-read |
| DATA-16 | Medium | A vendor-specific `chat_template_kwargs` parameter is sent by default | Needs check |
| ARCH-03 | Medium | Orchestration logic lives in route handlers and is duplicated | Code-read |
| ARCH-04 | Medium | Global config and import-time side effects | Code-read |
| ARCH-05 | Medium | Chroma and LLM clients are rebuilt on every call | Code-read |
| ARCH-06 | Medium | LLM calls have no explicit timeout or retry policy | Code-read |
| ARCH-07 | Medium | The code assumes a single instance, but that isn't documented or enforced | Code-read |
| ARCH-08 | Medium | torch and sentence-transformers are always installed and loaded in the API process | Code-read |
| ARCH-14 | Medium | Test gaps: security, uploads, memory, real store | Code-read |
| PERF-03 | Medium | Pages are still fetched after the HTML cap is reached; HTML allowed up to 20 MiB | Code-read |
| PERF-04 | Medium | Re-verification re-parses every source on every question | Code-read |
| PERF-06 | Medium | `list_topics` parses every archive file on each status call | Code-read |
| PERF-08 | Medium | Retrieval has no relevance threshold; eligibility is filtered after retrieval | Code-read |
| SEC-10 | Low | The LLM key can be sent to the wrong provider | Code-read |
| SEC-11 | Low | Absolute server paths stored in vector metadata and opened unchecked | Code-read |
| SEC-12 | Low | Missing security headers (CSP and others) | Code-read |
| SEC-13 | Low | Raw user search queries are logged (contradicts the logging policy) | Code-read |
| SEC-14 | Low | Python dependencies are unpinned (no lockfile or hashes) | Code-read |
| SEC-15 | Low | Search-engine scraping and full-copy retention of third-party content | Decision |
| SEC-16 | Low | UI privacy text claims local-only storage even in hosted mode | Code-read |
| DATA-12 | Low | Snapshots get overwritten, which breaks the audit trail | Code-read |
| DATA-13 | Low | A non-list `evidence_json` crashes the response model | Code-read |
| DATA-14 | Low | Exact-topic lookup is case- and whitespace-sensitive | Code-read |
| DATA-15 | Low | Thresholds and status lists are duplicated across files | Code-read |
| ARCH-09 | Low | LLM JSON is parsed by brace-scanning in two duplicate parsers | Code-read |
| ARCH-10 | Low | Untyped dicts across layers; no audit-file schema | Code-read |
| ARCH-11 | Low | Dead code and leftover files | Code-read |
| ARCH-12 | Low | No version control and no CI | Verified |
| ARCH-13 | Low | Frontend: a monolithic component, no cancel, no retry | Code-read |
| PERF-05 | Low | One DNS lookup per PDF link during text extraction (also during re-verify) | Code-read |
| PERF-07 | Low | In cloud mode, `index_exists()` makes network calls on every request | Code-read |
| PERF-09 | Low | The embedding model loads during the first user request | Code-read |

\*Critical for any deployment reachable from outside the local machine. On a machine where the API is bound only to 127.0.0.1, see SEC-09 instead.

---

## 5. Issue details

### 5.1 Security

#### SEC-01: No authentication on any endpoint (Critical)

**Where:** `backend/api/security.py:15-43`; every router in `backend/api/routes/`; `backend/main.py:127-148`.

**Problem:**
- The only guard is `require_allowed_origin`. It returns immediately when the request has no `Origin` header (`security.py:18-19`).
- So any script or `curl` can call `DELETE /api/memory`, upload files, and trigger paid LLM research.
- `GET /api/status` and `GET /api/topics` have no guard at all.
- The README "Deploy" section describes a public deployment, while TRD §11 item 10 says deployment is blocked until access control exists.

**Fix direction:**
- Add a FastAPI dependency `require_api_access` and apply it to every `/api/*` router except `/api/health`.
- If `APP_API_TOKEN` is set, require `Authorization: Bearer <token>` and compare with `hmac.compare_digest`.
- If it isn't set, accept only loopback clients (`request.client.host` in `127.0.0.1` / `::1`) and log a startup warning.
- Keep the Origin check as CSRF defence.
- Frontend: send the header using a token the user enters once and keep it in `sessionStorage`. Never put it in a `VITE_*` variable, because the static bundle is public.
- Public multi-user hosting needs an identity-aware proxy or real login. Escalate this (§7).

**Acceptance:**
- With a token configured, each mutating route returns 401 when the token is missing.
- Without a token configured, non-loopback clients are rejected.
- Requests without an `Origin` header no longer bypass the checks.
- `/api/health` stays open.

#### SEC-02: No rate limiting, cost ceiling, or concurrency cap (High)

**Where:** `routes/research.py:276-339`, `routes/questions.py:30-46`, `backend/core/research.py`.

**Problem:**
- One research run can make up to 6 search queries, fetch up to ~48 pages, and download 4 PDFs.
- It then makes up to 10 extraction calls and 12 claims × 5 sources = 60 cross-check calls.
- Each call sends up to 45,000 characters of source text.
- Nothing limits how often this runs or how many runs happen at once. `/api/ask` with `research_on_miss=true` triggers the same pipeline.

**Fix direction:**
- A global semaphore capping concurrent research runs (default 1). When it's full, return 429, or queue the run once ARCH-01 exists.
- A per-client token bucket (in-process is enough for one instance).
- A daily LLM-call budget read from an env var.
- A wall-clock deadline per run.

**Acceptance:**
- A second concurrent run gets 429 or a queued job id.
- Exceeding the budget returns a clear error.
- Both behaviours are covered by tests.

#### SEC-03: SSRF via DNS rebinding (High)

**Where:**
- `scraper.py:59-77` (`_is_public_http_url`).
- `scraper.py:254-290` (`_client`, `fetch_url`).

URLs reach these functions from three places:
- search hits (`scraper.py:84`);
- `.pdf` links found inside fetched pages (`scraper.py:332-335`);
- stored evidence URLs refetched during re-verification (`verify.py:64-78`).

**Problem:**
- `socket.getaddrinfo` validates the hostname, and then httpx resolves it again when it connects. A hostname with a short DNS TTL can pass the check and then point to `127.0.0.1` or the cloud metadata address `169.254.169.254`. Any page that appears in search results can supply such a hostname.
- `httpx.Client` defaults to `trust_env=True`. If `HTTP(S)_PROXY` is set, the proxy does the DNS resolution and this check never sees the real address.
- httpx timeouts apply per phase, not per request. A server can trickle bytes and hold a worker until the 20 MiB cap.
- Any port is allowed.

**Fix direction:**
- Resolve the hostname once, validate every returned address, and connect to the validated IP. Two ways to do that:
  - a custom transport that pins the IP while keeping the Host header and SNI; or
  - checking the connected peer via `response.extensions["network_stream"].get_extra_info("server_addr")` before reading the body.
- Set `trust_env=False`.
- Allow only ports 80 and 443 unless configured otherwise.
- Enforce a total deadline per fetch.

**Acceptance:**
- A test where the resolver returns a public IP for the check and `127.0.0.1` at connect time: the fetch is blocked.
- A non-standard port is rejected.
- The deadline is enforced.

#### SEC-04: The IP filter misses IPv6 transition encodings (Medium, Verified)

**Verified in this venv:**
- `ipaddress.ip_address("64:ff9b::a9fe:a9fe").is_global` returns `True` (NAT64).
- `ipaddress.ip_address("2002:a9fe:a9fe::1").is_global` returns `True` (6to4).
- Both addresses embed `169.254.169.254`. IPv4-mapped (`::ffff:`) addresses are correctly rejected.

**Fix direction:**
- Write a pure function `is_public_ip(ip)` that first unwraps the embedded IPv4 address from:
  - `ipv4_mapped`;
  - `sixtofour`;
  - `teredo`;
  - NAT64 `64:ff9b::/96` and `64:ff9b:1::/48`.
- Then reject anything that isn't global, plus multicast, reserved, unspecified and site-local addresses.
- Cover it with table-driven tests.

#### SEC-05: "Independent origins" are raw hostnames (High)

**Where:** `scraper.py:249-251` (`source_origin`); `research.py:354-367` and `410-423`.

**Problem:**
- Corroboration counts distinct hostnames.
- So `www.example.com` and `example.com` count as two independent origins, as do `a.attacker.com` and `b.attacker.com`.
- One attacker-controlled domain can therefore corroborate its own planted claim, which then becomes answerable memory.
- This breaks ADR-010 and the PRD's independence requirement.

**Fix direction:**
- Count origins by registrable domain (eTLD+1). Use `tldextract` with its bundled Public Suffix List and network fetching disabled (`suffix_list_urls=()`).
- Optionally collapse known syndication or mirror hosts into one origin.
- Keep the full hostname for display only.

**Acceptance:**
- `www.example.com` + `example.com` count as 1 origin, so the claim is `single_source`.
- `a.evil.com` + `b.evil.com` count as 1 origin.
- `a.github.io` + `b.github.io` count as 2, because `github.io` is on the Public Suffix List.

#### SEC-06: Prompt injection through fetched content (Medium)

**Where:** prompts at `research.py:38-87`; calls at `research.py:112-129` and `146-159`.

**Problem:**
- Untrusted page text is pasted straight into the prompts, with no delimiters.
- A page can carry instructions such as "verdict: supports", and its own claims count toward their support.
- Quote checking only proves that the quote exists on that page. Combined with SEC-05, a single attacker can get claims accepted.

**Fix direction:**
- Wrap the source text in explicit tags such as `<source_text>…</source_text>`, and tell the model to treat it as data, not instructions.
- Use structured or JSON-mode output (ARCH-09).
- Don't count the source a claim was extracted from toward its own corroboration: require ≥2 other origins, or document why the seed source counts.
- Add an injection fixture to the tests.

#### SEC-07: No request-body size limit (Medium)

**Where:** `routes/documents.py:28-67`; `backend/main.py` has no body-limit middleware.

**Problem:**
- FastAPI parses the whole multipart body before the handler runs. Starlette spools the files to temp disk while doing so.
- So the 10-file and 20 MiB limits are checked only after the entire body is already on disk.
- An unauthenticated client can fill the disk.

**Fix direction:**
- Add ASGI middleware that rejects a `Content-Length` above 10 × 20 MiB + 1 MiB for `/api/documents`, and ~1 MiB elsewhere.
- Also count streamed bytes, because chunked bodies don't send `Content-Length`.
- Document the matching reverse-proxy limit.

#### SEC-08: Untrusted PDF parsing in-process with no timeout (Medium)

**Where:** `ingest.py:31`; `research.py:135-138`; `verify.py:31-35`. `pypdf>=5.1.0` is unpinned.

**Problem:**
- User uploads and web PDFs (which an attacker can supply) are parsed inside API worker threads.
- Malformed PDFs, such as decompression bombs or pathological cross-reference tables, can hang a worker or exhaust memory.

**Fix direction:**
- Parse in a subprocess (`ProcessPoolExecutor` with `max_tasks_per_child`) with a timeout and a page cap.
- Pin pypdf to a current release.

#### SEC-09: Local DNS rebinding can read private topics (Medium)

**Where:** `backend/main.py:127-142` (no `TrustedHostMiddleware`); `routes/system.py:17-53` (no checks on the GET endpoints).

**Problem:**
- In the default local mode, a malicious website can rebind its own domain to `127.0.0.1`.
- Its pages can then read `/api/topics` and `/api/status` as if they were the same site, exposing the user's research topic list.
- Writes are protected by the Origin check; reads aren't.

**Fix direction:**
- Add `TrustedHostMiddleware` with hosts from a new `APP_ALLOWED_HOSTS` variable (default `127.0.0.1,localhost`).
- The authentication from SEC-01 also covers this.

#### SEC-10: The LLM key can be sent to the wrong provider (Low)

**Where:** `config.py:51-56`, `embeddings.py:22-28`.

**Problem:**
- `LLM_API_KEY` falls back to `OPENAI_API_KEY`, while `LLM_BASE_URL` may point at NVIDIA or any other provider.
- So a user's global OpenAI key can be sent to a third party.
- Embeddings reuse the same key and base URL.

**Fix direction:**
- Use the `OPENAI_API_KEY` fallback only when `LLM_BASE_URL` is unset.
- Add separate `EMBEDDING_API_KEY` and `EMBEDDING_BASE_URL` variables.

#### SEC-11: Absolute server paths in vector metadata, opened unchecked (Low)

**Where:** `ingest.py:34-35`; `research.py:286` and `434`; `verify.py:56-63` and `83-88`.

**Problem:**
- Absolute host paths are stored in Chroma, which exposes the server's directory layout to Chroma Cloud.
- Paths read back from the database are opened without checking that they're inside `DATA_DIR`.
- Moving `SPYMONK_DATA_DIR` breaks every stored path and triggers mass refetches.

**Fix direction:**
- Store paths relative to `DATA_DIR`.
- On read, `resolve()` the path and require `is_relative_to(DATA_DIR)`.
- Include a migration for existing records in the reindex tool (DATA-11).

#### SEC-12: Missing security headers (Low)

**Where:** `backend/main.py:150-165` and `frontend/vercel.json`.

**Fix direction:** in both places, add:
- a Content-Security-Policy (`default-src 'self'`, with `connect-src` allowing the API origin);
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy: no-referrer`;
- `frame-ancestors 'none'`.

#### SEC-13: Raw user queries in logs (Low, decision needed)

**Where:** `scraper.py:191-202` logs `query=%r`. The tests assert this behaviour (`tests/test_core.py:121`, `tests/test_api.py:219`).

**Problem:** this contradicts the README and TRD §10 promise that logs never contain request content.

Ask the user which option they want, then update code and tests together:
- keep it as is;
- log the query only at DEBUG level; or
- log a hash of the query instead.

#### SEC-14: Unpinned Python dependencies (Low)

**Where:** `requirements.txt` uses only `>=` pins, with no lockfile or hashes. The frontend has `package-lock.json`, which is fine.

**Fix direction:**
- Lock dependencies with hashes using `pip-tools` or `uv`.
- Split dev and optional ML requirements into separate files (see ARCH-08).
- Add `pip-audit` and `npm audit` to CI.

#### SEC-15: Search scraping and content retention (Low, decision needed)

**Problem:**
- `ddgs` scrapes search engines without an API. The tests already simulate the 429 rate-limit responses this produces.
- The app keeps full copies of third-party pages and PDFs indefinitely.

**For public hosting:**
- Put search behind a provider interface backed by a licensed search API.
- Define a retention policy for stored copies.

#### SEC-16: Privacy text is wrong in hosted mode (Low)

**Where:** `frontend/src/App.jsx:360` and `390`.

**Problem:** the UI says files and the index "stay on this machine" even when Chroma Cloud or remote embeddings are configured.

**Fix direction:** drive this text from `/api/status` (`vector_store_provider`, `embedding_provider`).

### 5.2 Data integrity and correctness

#### DATA-01: A research run with zero verified claims deletes existing verified memory (High)

**Where:**
- `store.py:203-220`;
- `research.py:465-479`;
- comparison errors are swallowed at `research.py:381-393`;
- bad JSON falls back to "no claims" at `research.py:90-102`.

**Problem:**
- `replace_topic_web_documents(topic, [])` skips the write, then deletes every existing web-claim ID for the topic.
- `save_topic` then overwrites the audit file.
- Example: the LLM starts failing after extraction. Every cross-check raises an error and is skipped, every claim becomes `single_source`, and zero claims are verified. The topic's previously corroborated memory is erased, and the API still returns 200.

**Fix direction:**
- Count LLM and parse failures during the run.
- Don't delete stored claims or overwrite the audit if either:
  - the run had any failures; or
  - it verified zero claims while the topic already has verified ones.
- Instead, record the run as a failed attempt (an `attempts` list, or a timestamped sidecar file) and return `memory_updated: false`.
- Keep earlier archives as history instead of overwriting them.

**Acceptance:** a test seeds existing claim IDs, then runs research with every comparison raising. Nothing is deleted, and the old audit survives.

#### DATA-02: Topic slug collisions overwrite audit files (High, Verified)

**Where:** `memory.py:13-23`.

**Verified:**
- `topic_slug("भगवद् गीता") == topic_slug("महाभारत") == "topic"`.
- `"C++"` and `"C"` both become `"c"`.
- Slugs are cut to 80 characters. `/api/ask` with research-on-miss uses the full question (up to 2,000 characters) as the topic, so any two questions sharing an 80-character prefix collide.

This also undercuts ADR-013 and PRD PR-15, which require Sanskrit and Hindi support.

**Fix direction:**
- Build the filename as a readable, Unicode-aware prefix plus a hash: `re.sub(r"[^\w]+", "-", topic.casefold())` truncated, then `-`, then `sha256(canonical_topic)[:12]`.
- Write a migration that renames existing files using the `topic` field inside each file.
- Share the canonical key with ARCH-02.

#### DATA-03: Clearing the local index fails on Windows (Medium, Verified)

**Where:** `store.py:136-148`.

**Verified on a throwaway index:**
- After any chromadb access in the same process, `clear_vectorstore()` raises `PermissionError`.
- The index stays intact, and `DELETE /api/memory` returns 500.
- Cause: chromadb caches its client per path and keeps the SQLite file open.

**Unverified:** on Linux the `rmtree` succeeds, but the cached client may keep pointing at the deleted files.

**Fix direction:**
- Use `client.delete_collection()` then `get_or_create_collection()`, as the cloud branch already does, through the shared client from ARCH-05.
- Never `rmtree` a database that is in use.

#### DATA-04: Failed uploads leave files on disk (Medium, Verified)

**Where:** `routes/documents.py:47-81`; `routes/system.py:24`.

**Verified:**
- `data/pdfs/` holds three copies of `IBM questions.pdf`. Each is one page with 0 extractable characters, and none is indexed.
- `/api/status` reports `local_pdf_count = 4` while only 1 PDF is indexed.

**Fix direction:**
- Write each upload to a temporary file and move it into place only after ingestion succeeds. Delete it on any failure.
- Report the indexed-document count separately from the on-disk count.

#### DATA-05: A missing `origin` is treated as trusted (Medium)

**Where:** `query.py:115` and `verify.py:124` both default `origin` to `"local_pdf"`.

**Problem:**
- A record without an `origin` field skips both the eligibility filter and quote re-verification, which contradicts ADR-015.
- Every writer sets the field today, so this is latent, but nothing enforces it.

**Fix direction:**
- Treat a record as a local PDF only when `origin == "local_pdf"` is explicitly set.
- Every other record must carry an answerable status to be used.

#### DATA-06: Audit JSON reads can crash; writes aren't atomic (Medium)

**Where:** `memory.py:33-55`.

**Problem:**
- `list_topics()` catches only `JSONDecodeError`. Any of these crashes it, making `/api/status` and `/api/topics` return 500:
  - a file whose root is a list;
  - a file containing `"claims": null`;
  - a file that isn't valid UTF-8.
- `save_topic` writes in place. A crash mid-write leaves a truncated file, which is then skipped silently with no log.

**Fix direction:**
- Validate files against a Pydantic `TopicArchive` model.
- Write atomically: write a temp file in the same directory, `flush` and `fsync`, then `os.replace`.
- On read, catch `(OSError, ValueError, TypeError)` and log a warning that names the file's slug.

#### DATA-07: One failed extraction call aborts the whole run (Medium)

**Where:** `research.py:306-312`. Compare with the per-source error handling at `research.py:381-393`.

**Fix direction:**
- Wrap each per-source extraction in the same `try/except`, and log failures with `safe_exception_context`.
- Count each failure toward the health check from DATA-01.

#### DATA-08: Record IDs aren't deterministic, so duplicates accumulate (Medium, Verified)

**Where:**
- Web-claim IDs include a random `uuid4()` (`research.py:427`, used at `store.py:88`), so every refresh rewrites every claim ID.
- Each upload gets a random filename prefix (`routes/documents.py:69`).
- `chunk_index` is numbered across the whole upload batch rather than per file (`ingest.py:45-46`).

As a result, re-uploading a PDF duplicates all of its chunks, which contradicts ADR-006.

**Verified:** 25 of the 111 indexed chunks (22%) exactly repeat another chunk's text (repeated slide pages in the Docker PDF). A top-5 search can return the same text several times.

**Fix direction:**
- Identify each PDF by `sha256(file bytes)`, and number chunks per document.
- Make claim IDs `sha256(canonical_topic + normalize_text(claim))`.
- Skip identical chunk text within a document.
- Skip uploads whose file hash is already indexed.

This requires a reindex (DATA-11).

#### DATA-09: The embedding model truncates every chunk (Medium, Verified)

**Verified:**
- The cached `all-MiniLM-L6-v2` config has `max_seq_length: 256`.
- Chunks are 500 tiktoken (`cl100k`) tokens (`config.py:34`).
- So the second half of every chunk is never embedded, and semantic search can't find text there.

**Fix direction:**
- Chunk with the embedding model's own tokenizer at about 240 tokens or fewer, with overlap; or
- switch to a model with a longer input limit.

This requires a reindex.

#### DATA-10: The index doesn't record which embedding model built it (Medium)

**Problem:**
- Changing `EMBEDDING_PROVIDER` or `EMBEDDING_MODEL` silently mixes incompatible vectors in one index.
- The result is either a dimension-mismatch error or meaningless similarity scores.

**Fix direction:**
- When creating the collection, write `{embedding_provider, embedding_model, dimension, chunker_version}` into its metadata.
- On first use, refuse to run with a clear error if the current config doesn't match, and point to the reindex tool.

#### DATA-11: No way to rebuild the index (Medium)

**Problem:**
- ADR-005 says either store can be rebuilt from the other, but no rebuild exists.
- After "Clear searchable index", `/api/topics` (`system.py:44-53`) and the UI (`App.jsx:484`) still show "N answerable claims" that can no longer be answered.

**Fix direction:**
- Add `python -m backend.core.reindex`, plus an optional `POST /api/memory/reindex`.
- Rebuild from `memory/topics/*.json` (answerable claims only) and `data/pdfs/*.pdf`.
- Make `list_topics` report the indexed claim count, or flag topics that aren't indexed.

#### DATA-12: Snapshots get overwritten (Low)

**Where:** `scraper.py:301-323`.

**Problem:**
- Snapshot filenames are derived from the URL.
- A later run, or a refetch during re-verify, overwrites the exact file an earlier claim was verified against.
- Those claims then silently fail re-verification.

**Fix direction:**
- Name snapshots by the sha256 of their content.
- Store that hash in each evidence item.

#### DATA-13: A non-list `evidence_json` crashes the response model (Low)

**Where:** `query.py:60-76`.

**Problem:** if `evidence_json` parses to something other than a list, the API returns a 500 when `reverify=false`.

**Fix direction:** add `if not isinstance(evidence, list): evidence = []`.

#### DATA-14: Exact-topic lookup is case- and whitespace-sensitive (Low)

**Where:** `store.py:121`.

**Problem:** "Docker compose" misses "Docker Compose" and falls back to the slower semantic search.

**Fix direction:** store the canonical topic key from ARCH-02 in the metadata and query by it.

#### DATA-15: Duplicated thresholds and status lists (Low)

**Where:**
- `verify.py:136` and `research.py:416` hard-code `2` instead of using `config.MIN_INDEPENDENT_ORIGINS`.
- The set `{"corroborated","pdf_resolved"}` is repeated at `store.py:132`, `query.py:116`, `verify.py:128` and `research.py:445`.

**Fix direction:**
- Define one `EvidenceStatus` enum and one `ANSWERABLE_STATUSES` constant.
- Use them everywhere, including in the Pydantic `Literal` types.

#### DATA-16: A vendor-specific LLM parameter is sent by default (Medium, Needs check)

**Where:** `config.py:61-65` defaults `LLM_DISABLE_THINKING` to true, so `query.py:89-90` sends `extra_body={"chat_template_kwargs": …}`. This parameter is specific to vLLM and NVIDIA NIM.

**Problem:** providers that reject unknown parameters, likely including OpenAI's API, would fail every call under the default config.

**Fix direction:**
- Default `LLM_DISABLE_THINKING` to false, or send the parameter only when the base URL belongs to a known-compatible provider.
- Before changing anything, confirm the behaviour with a test that uses a mocked transport.

### 5.3 Architecture

#### ARCH-01: Long research runs inside the HTTP request (High)

**Where:** `routes/research.py:276-339`; `routes/questions.py:30-46`.

**Problem:**
- A run takes minutes, and nothing records it as a job or makes it safe to retry.
- If the client disconnects from the stream, the `events()` generator is cancelled, but the background thread keeps running and writing (`routes/research.py:315-330`; `await worker` is never reached).
- The UI has no way to cancel a run.
- Two runs on the same topic can race each other, which is one way DATA-01 and DATA-06 get triggered.
- Proxy and host timeouts can cut the request off; TRD §9 acknowledges this.

**Fix direction (single instance first):**
- Keep a job registry, in-process or as a SQLite `jobs` table, with: id, topic key, status, progress events, and result.
- `POST /api/research/jobs` creates a job and returns its id.
- Run jobs on a bounded worker pool, with a per-topic lock so two runs on one topic can't overlap.
- `GET /api/research/jobs/{id}` returns status, plus an SSE stream addressed by job id.
- `DELETE /api/research/jobs/{id}` sets a cancel flag. The pipeline checks it between stages and between LLM calls.
- Keep `/api/research` as a compatibility wrapper.

#### ARCH-02: Chroma and JSON have no consistency model and no shared key (High)

**Where:** `research.py:465-479`; `store.py`; `memory.py`.

**Problem:**
- Chroma is keyed by the raw topic string, while the JSON files are keyed by a lossy slug.
- Writes go to Chroma first, then JSON, with no transaction around them.
- Clearing the index affects only Chroma, and nothing reconciles the two stores.

**Fix direction:**
- Define a canonical `TopicKey`: Unicode NFKC normalization, casefold, collapsed whitespace, plus a hash.
- Make the audit record the source of truth and the index derived from it:
  - write the audit atomically first, then index;
  - track `index_state` as `indexed` or `stale`;
  - reconcile through the reindex tool (DATA-11).
- Recommended, but decide with the user: move archives, claims, evidence and jobs into SQLite (one file in `DATA_DIR`, WAL mode) and keep only snapshots on the filesystem. That gives transactions, locking and cheap listing (PERF-06).

#### ARCH-03: Orchestration logic lives in route handlers and is duplicated (Medium)

**Where:**
- `routes/research.py:56-273`: about 200 lines covering the memory check, exact lookup, semantic search, re-verify, compose, plan and research steps.
- `routes/questions.py:24-72` implements a different variant: no exact-topic lookup, no progress events, and `ask()` called twice.

**Fix direction:**
- Create `backend/services/archive_service.py` with a single `lookup_or_research(topic, *, refresh, reverify, plan, progress, cancel)`.
- Make both routes thin HTTP adapters around it.

#### ARCH-04: Global config and import-time side effects (Medium)

**Where:**
- `config.py` reads the environment and `.env` when it's imported.
- `ingest.py:16-21` builds the tiktoken splitter at import. On a cold cache that downloads `cl100k_base`, so startup fails offline.
- `main.py:27` configures logging at import.
- Routes import core modules inside the handlers, partly so tests can patch module globals.

**Fix direction:**
- Add a `pydantic-settings` `Settings` class.
- Inject dependencies through FastAPI `Depends`, and build the store and LLM clients in the app's `lifespan` handler.
- Build the text splitter lazily.
- Change tests to use `app.dependency_overrides` instead of `patch.object` on modules.

#### ARCH-05: Clients are rebuilt on every call (Medium)

**Where:**
- `store.py:111-115` creates a new `PersistentClient` for every exact-topic lookup.
- `load_vectorstore()` creates a new store wrapper for every retrieval.
- `_cloud_client()` creates a new `CloudClient` on every call.
- `replace_topic_web_documents` builds 2–3 store wrappers.
- `get_llm()` (`query.py:79-91`) creates a new `ChatOpenAI`, with a new HTTP client, for each of the ~70 LLM calls in a run.

**Fix direction:**
- Create one `VectorStoreRepository` in `lifespan`, holding one client and one collection.
- Create one LLM client.
- Close both on shutdown.

#### ARCH-06: LLM calls have no explicit timeout or retry policy (Medium)

**Where:** `query.py:79-91`.

**Problem:** the OpenAI SDK defaults apply (up to a 10-minute timeout and 2 retries), so one stalled provider can tie up a worker for minutes on every call.

**Fix direction:**
- Set `timeout` (for example 60 s) and `max_retries` from settings.
- Add a deadline per research run.

#### ARCH-07: Single-instance assumption isn't documented or enforced (Medium)

**Problem:**
- Snapshots, uploads and audit files live on local disk, and nothing takes a lock.
- Yet the README describes a hosted deployment with Chroma Cloud.
- With two or more instances, the vectors are shared but the files aren't, so re-verification on another instance refetches sources or fails.

**Fix direction:** either
- state "exactly one API instance" as a hard deployment constraint in the README and TRD, and enforce it with a lock file in `DATA_DIR`; or
- move the files to object storage. This is a decision for the user (§7).

#### ARCH-08: Heavy ML dependencies are always installed and loaded (Medium)

**Where:** `requirements.txt`; `embeddings.py:35-41`.

**Problem:**
- torch and sentence-transformers are installed and loaded in the API process even when remote embeddings are configured.
- That means multi-GB images, slow cold starts and high memory use.

**Fix direction:**
- Move them to a `requirements-local-embeddings.txt`, or to optional extras.
- Make hosted configurations default to remote embeddings.
- Optionally warm the model up in `lifespan`.

#### ARCH-09: LLM JSON is parsed by brace-scanning in two places (Low)

**Where:** `research.py:90-102` and `search_planner.py:34-46` contain duplicate parsers.

**Fix direction:**
- Replace both with one `parse_llm_json(raw, Model)` function that validates with Pydantic.
- Where the provider supports it, use JSON mode or `with_structured_output`.

#### ARCH-10: Untyped dicts across layers (Low)

**Where:**
- Archives, claims and evidence are passed around as plain dicts.
- `AskResponse.archive` is `dict[str, Any]` (`schemas.py:110`).
- `validation_status` is a plain `str` (`schemas.py:66` and `80`).

**Fix direction:** shared Pydantic models (`Claim`, `Evidence`, `TopicArchive`, `EvidenceStatus`) used by both the core and the API.

#### ARCH-11: Dead code and leftovers (Low)

Confirm with grep that nothing calls these, then delete:
- `memory.load_topic` and `memory.load_claim_by_id`;
- `verify.refresh_source_text` (a near-copy of `source_text_from_record`);
- `scraper.download_reference_html` and `scraper.collect_pdf_urls`, plus `config.MAX_HTML_PAGES`, which only they use;
- `research._pdf_to_text`;
- `ingest.persist_chunks`.

Remove these leftover files and folders:
- the empty `.streamlit/` folder, plus the Streamlit comment at `scraper.py:383-385`;
- `frontend/frontend/`, a stray npm package whose only dependency is `"file:.."`;
- the root `__pycache__/`.

Ask the user before moving the two mood-board JPGs (about 1.8 MB, at the repo root) into `docs/design/`.

#### ARCH-12: No version control and no CI (Low, Verified)

**Problem:** there is no `.git` directory, even though the repo has a `.gitignore`.

**Fix direction:**
- Initialize git as described in Phase 0.
- Add CI that runs the unit tests, the frontend build, `pip-audit` and `npm audit`.

#### ARCH-13: Frontend structure (Low)

**Problems:**
- `App.jsx` is one roughly 370-line component (lines 140-508), with very long JSX lines such as line 466.
- `api.js` duplicates its request and error handling (lines 3-28 and 30-53).
- There's no `AbortController`, so a research run can't be cancelled.
- Status is fetched once at load, with no retry if the API is offline at that moment.

**Fix direction:**
- Split it into components and hooks such as `useWorkspaceStatus` and `useResearchJob`.
- Add cancellation, tied to the job model from ARCH-01.
- Retry the status fetch with backoff.

#### ARCH-14: Test gaps (Medium)

**Problem:** there are no tests for:
- `security.py`;
- the upload route;
- `memory.py`;
- the store against a real Chroma instance in a temp dir;
- the SSRF filter;
- clearing and reindexing.

The frontend has no tests at all.

**Fix direction:**
- Add `tests/__init__.py`.
- Add a `requirements-dev.txt` that includes pytest.
- Give every issue in this brief a regression test as it's fixed.

### 5.4 Performance

#### PERF-01: Cross-checking resends whole source texts (High)

**Where:** `research.py:146-159` and `344-408`.

**Problem:**
- Each cross-check call sends up to 45,000 characters of the same source.
- With up to 60 cross-checks plus 10 extractions, that's about 3.1M characters (~0.8M tokens) per run in the worst case.

**Fix direction:** one or both of:
- **Batch:** one LLM call per source that judges every candidate claim against it. Calls then scale with the number of sources (up to 10) instead of claims × sources (up to 60).
- **Retrieve:** chunk each source once, pick the top 3–5 passages per claim using BM25 or embeddings, and send only those (about 2–4k characters).

**Acceptance:** a test with a mocked LLM counts the calls and asserts the new upper bound.

#### PERF-02: The whole pipeline is sequential (High)

**Where:**
- search queries: `scraper.py:163-226`;
- page fetches: `scraper.py:482-524` (up to ~48 fetches, each with 25 s per-phase timeouts);
- PDF downloads: `scraper.py:386-421`;
- LLM calls: `research.py:256-336` and `344-408`.

**Fix direction:**
- Add bounded concurrency: a `ThreadPoolExecutor` with about 4–8 workers and at most 1–2 requests per host, or async httpx with `ainvoke`.
- Keep the result order deterministic.
- Check the cancel flag from ARCH-01 between tasks.

#### PERF-03: Pages are still downloaded after the HTML cap is reached (Medium)

**Where:** `scraper.py:496-509`.

**Problem:**
- `fetch_url` downloads the full body (up to 20 MiB) before the `MAX_WEB_HTML` cap is checked.
- So after 6 pages, it keeps downloading whole pages just to find out whether any is a PDF.
- HTML responses are allowed up to 20 MiB even though only the first 2 MiB is ever used (`scraper.py:280-287` versus `327-328`).

**Fix direction:**
- Once the HTML cap is reached, follow up only URLs that look like PDFs, or probe with HEAD or Range requests.
- Apply a per-content-type byte cap while streaming.
- Add a total byte budget per run.

#### PERF-04: Re-verification re-parses every source on every question (Medium)

**Where:**
- Re-verification is on by default: `schemas.py:13` and `33`, `App.jsx:148`.
- Every evidence source is re-read and re-parsed with pypdf or BeautifulSoup on every request (`verify.py:45-78` and `119-161`).
- The cache lasts for a single request only (`query.py:128`).

**Fix direction:**
- At research time, store each snapshot's sha256 and its normalized text (a `.txt` file next to the snapshot).
- Re-verify by checking the hash is unchanged, then matching the quote against the cached text.
- Add a process-wide LRU cache keyed by `(path, size, mtime)`.

#### PERF-05: One DNS lookup per PDF link during text extraction (Low)

**Where:**
- `scraper.py:332-335` resolves every PDF link while extracting text.
- Re-verification (`verify.py:38-42`) calls this and then throws the links away.
- `search_web` resolves every search hit the same way (`scraper.py:85`).

**Problem:**
- The current snapshots contain no PDF links (verified), so this is latent for now.
- On link-heavy index pages, it means hundreds of sequential DNS lookups per page, on every question.

**Fix direction:**
- Split `extract_text` from `extract_pdf_links`.
- Validate a URL only right before fetching it.

#### PERF-06: `list_topics` parses every archive file on each status call (Medium)

**Where:** `memory.py:40-55`.

**Problem:** after every action, the UI calls both `/api/status` and `/api/topics` (`App.jsx:162-166`), and each call parses every archive file in full.

**Fix direction:**
- Keep a small topic index: a SQLite table, or an `index.json` that `save_topic` updates atomically. Alternatively, cache the list keyed by the directory's mtime.
- Merge the two calls into one `/api/workspace` endpoint.

#### PERF-07: In cloud mode, `index_exists()` makes network calls on every request (Low)

**Where:** `store.py:26-31`.

**Problem:**
- Each call makes two network round trips (`get_or_create_collection`, then `count`).
- It runs on every status, research and upload request.
- It also creates the collection as a side effect.

**Fix direction:**
- Cache the result in the repository object (ARCH-05) and invalidate it on writes.
- Use the read-only `get_collection`.

#### PERF-08: Retrieval has no relevance threshold and filters eligibility afterwards (Medium)

**Where:** `query.py:94-123`.

**Problem:**
- Retrieval always returns the top 5 chunks, even when they're unrelated, and all 5 are sent to the LLM. That costs tokens and adds noise.
- Eligibility is filtered in Python after retrieval, so fewer than 5 results may remain.

**Fix direction:**
- Filter eligibility inside the Chroma query with a `where` clause (an `$or` on origin and status).
- Use `similarity_search_with_relevance_scores` with a configurable threshold.
- Use MMR (maximal marginal relevance) to avoid near-duplicate chunks; see DATA-08.

#### PERF-09: The embedding model loads during the first user request (Low)

**Fix direction:** an optional warm-up in `lifespan`, enabled by `EMBEDDINGS_WARMUP=true`.

---

## 6. Execution order

**Phase 0: Setup (ask the user first)**
- Run `git init` and make a baseline commit. `.gitignore` already excludes `.env`, `data/`, `chroma_db/` and `memory/topics/`; confirm this before committing.
- Confirm the 20 baseline tests pass.

**Phase 1: Stop data loss and close cheap holes**
- DATA-01 and DATA-07 (together)
- DATA-02, with migration
- DATA-03
- DATA-04
- DATA-05
- DATA-06
- DATA-13
- SEC-04
- SEC-05
- SEC-07
- SEC-09

**Phase 2: Required before any deployment beyond the local machine**
- SEC-01
- SEC-02
- SEC-03
- SEC-08
- ARCH-01
- ARCH-06
- SEC-10
- SEC-11
- SEC-12
- SEC-16
- DATA-16

**Phase 3: Consistency and structure**
- ARCH-02 together with DATA-14 (the canonical topic key)
- DATA-11 (the reindex tool)
- DATA-08, DATA-09 and DATA-10, done together so they need only one reindex, and only after the user approves it
- ARCH-03
- ARCH-04
- ARCH-05
- ARCH-09
- ARCH-10
- DATA-12
- DATA-15

**Phase 4: Performance**
- PERF-01
- PERF-02
- PERF-04
- PERF-08
- PERF-03
- PERF-06
- PERF-07
- PERF-05
- PERF-09

**Phase 5: Hygiene**
- ARCH-11
- SEC-14
- ARCH-08
- ARCH-13
- ARCH-12 (CI)

ARCH-14 (tests) applies throughout every phase.

## 7. Decisions to escalate to the user

1. **Auth model (SEC-01):** a personal API token, or an identity-aware proxy or real login for public hosting.
2. **Logging search queries (SEC-13):** keep them, move them to DEBUG, or hash them.
3. **Search provider (SEC-15):** keep `ddgs`, or move to a licensed search API.
4. **Storage (ARCH-02, ARCH-07):** move to SQLite for archives and jobs, now or later. Object storage is needed if more than one instance is ever planned.
5. **Refresh behaviour (DATA-01):** should a refresh that verifies nothing ever delete previously verified claims? Proposed default: never.
6. **Embeddings (DATA-08, DATA-09, DATA-10):** which embedding model and chunk size to use, and when to run the full reindex.
7. **Repo setup (ARCH-11, ARCH-12):** whether to run `git init`, and whether to move or delete the mood-board images and stray folders.

## 8. Definition of done (for each issue)

- The code change comes with a regression test that fails before the fix and passes after it.
- The full unit test suite passes. If the frontend was touched, `npm --prefix frontend run build` passes too.
- No new logging of secrets or content, and exceptions are logged with `safe_exception_context`.
- README, `docs/TRD.md` or `docs/DECISIONS.md` are updated whenever behaviour or an ADR's status changes.
- A short note in the change description covers: the issue ID, what changed, what was tested, and any follow-ups.

## 9. Status as of 2026-10-19 (second pass)

Verified at the end of this pass:

- **Backend:** 115 tests pass (`python -m unittest discover -s tests`).
- **Frontend:** 15 tests pass (`npm --prefix frontend test`) and the production build succeeds.
- **Real-server smoke test** on a throwaway data directory, covering:
  - PDF upload, duplicate upload, and an image-only upload;
  - status;
  - an unrelated question (answered without an LLM call);
  - reindex plan;
  - security headers and Host rejection;
  - the second-process lock.

| Status | Issues |
| --- | --- |
| Done in the first pass | SEC-01, 03, 04, 05, 07, 08, 09, 10, 12, 13 (queries logged as a hash), 16; DATA-01–07, 13, 16; ARCH-06 |
| Done in this pass | SEC-02, 06, 11 (PDF paths), 12 (frontend CSP), 14 (constraints file); DATA-08, 09, 10, 11, 12, 14, 15; ARCH-01, 02 (canonical key, audit-first writes, `index_state`, reindex), 03, 05, 07 (instance lock), 08, 09, 10, 11 (code and stray folders), 12 (CI workflow), 13, 14; PERF-01–09 |
| Partial, by choice | **ARCH-04:** settings stay module attributes, now validated at import; the splitter is lazy; the job manager is injectable. A full pydantic-settings and dependency-injection rewrite was judged not worth churning 100+ call sites and tests. **PERF-06:** cached per file, not merged into one endpoint. **PERF-08:** duplicates and weak matches dropped, no MMR. **SEC-14:** exact pins, no hashes. |
| Waiting on the user (§7) | SEC-15 (search provider, retention); SQLite or object storage (ARCH-02/07); `git init` (ARCH-12); the two mood-board JPGs at the repo root (ARCH-11); rebuilding the user's existing index, which is a legacy index holding the Docker PDF twice |
