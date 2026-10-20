# SpyMonk Encyclopedia — Review and Gap Analysis

| Field | Value |
| --- | --- |
| Reviewed | 2026-10-20, API version 2.3.0 (backend, frontend, tests, CI, docs, and the local running instance) |
| Scope | Security, answer quality, data integrity, reliability, operations, maintainability, compliance, frontend |
| Findings | 34 gaps: 2 × P0, 8 × P1, 15 × P2, 9 × P3 |
| Supersedes | Nothing. [REMEDIATION_PROMPT.md](REMEDIATION_PROMPT.md) records the earlier 55-issue pass (closed); [TRD.md §17](TRD.md) lists known limitations this document expands into actionable issues |

## Summary

The evidence pipeline is in good shape: quote verification, cross-domain corroboration, fail-closed saves, SSRF-safe fetching, and audit-first persistence all work and are tested. The largest gaps sit around that pipeline rather than in it:

1. **The final answer is not checked.** Research verifies every claim, but the answer composed from those claims is the model's free text, never checked against them. The product's promise ("every answer points back to its evidence") stops one step short.
2. **Answer quality is not measured.** There is no evaluation set or metric, so a model swap or threshold change cannot be judged except by hand.
3. **The project is not under version control,** so none of the CI gates (142 backend tests, 23 frontend tests, dependency audits) ever run automatically.
4. **Hosting prerequisites are missing:** proxy-safe client identity, a deployable container, backups, and compliance decisions.

Fix the two P0 items first, then the P1 items in the order in [Suggested sequence](#suggested-sequence).

## Priority definitions

| Priority | Meaning | Target |
| --- | --- | --- |
| **P0** | Breaks a core promise, or puts the code itself at risk. | Now |
| **P1** | Blocks hosting for anyone but the owner, risks silent data loss, or limits answer quality. | Before hosting, or the next iteration |
| **P2** | Real weakness with a workaround, or a scaling and maintainability cost. | Planned work |
| **P3** | Hygiene and polish. | Opportunistic |

Effort: **S** up to a day, **M** a few days, **L** a week or more.

## Method

Each area was reviewed against an established reference:

| Area | Reference |
| --- | --- |
| Web and API security | OWASP ASVS 4.0 (authentication, access control, input handling, configuration) |
| LLM-specific risks | OWASP Top 10 for LLM Applications 2025 (prompt injection, output handling, unbounded consumption) |
| Architecture quality | ISO/IEC 25010 (reliability, maintainability, security, portability) |
| Operations | 12-factor app (config, processes, disposability, logs, dev/prod parity) |
| Reliability | SRE practice (timeouts, idempotency, durable work, observability, SLOs) |
| Answer quality | RAG evaluation practice (retrieval relevance, faithfulness, citation precision) |
| Frontend | React resilience patterns, WCAG 2.2 |

Evidence came from reading the code and from checks against the local instance: index health, the latest topic audit, topic-key behavior, and quote overlap between sources. **Not done in this pass:** penetration testing, load testing, browser-based visual and accessibility testing, and a live dependency CVE scan.

### Strengths to preserve

- Fail-closed evidence rules: verbatim quotes, two independent registrable domains, conflicts and unfinished checks held back.
- SSRF defenses: one DNS resolution, IP pinning, public-address checks including IPv4-in-IPv6, ports 80/443 only, revalidated redirects, byte and time limits.
- Content-addressed snapshots with recorded hashes; audit-first writes with `index_state`; a fingerprinted index and a rebuild tool.
- Background jobs with cancellation, deadlines, rate limits, and a daily model-call ceiling.
- Partial saves and unanswered-question reports with automatic diagnostics; logs kept under `logs/`.
- Test isolation: the full suite leaves an empty data directory empty.

## Issue index

| ID | Title | Area | Priority | Effort |
| --- | --- | --- | --- | --- |
| [GAP-01](#gap-01) | No version control, so CI never runs | Engineering process | P0 | S |
| [GAP-02](#gap-02) | Answers are not checked against their evidence | Answer quality | P0 | M |
| [GAP-03](#gap-03) | Local-only mode and rate limits trust the socket peer | Security | P1 | S |
| [GAP-04](#gap-04) | Indexed text enters the answer prompt as trusted instructions | Security (LLM) | P1 | S |
| [GAP-05](#gap-05) | No evaluation harness for retrieval and answers | Answer quality | P1 | M |
| [GAP-06](#gap-06) | Source collection and candidate selection starve corroboration | Answer quality | P1 | M |
| [GAP-07](#gap-07) | Reindex can silently drop newly saved claims | Data integrity | P1 | M |
| [GAP-08](#gap-08) | No backup or restore; data lives in the source tree | Data, operations | P1 | S |
| [GAP-09](#gap-09) | No deployable artifact | Operations | P1 | M |
| [GAP-10](#gap-10) | Public-hosting compliance is unresolved | Compliance | P1 | M |
| [GAP-11](#gap-11) | Model JSON output is unconstrained | LLM robustness | P2 | M |
| [GAP-12](#gap-12) | Corroboration counts domains, not independent content | Answer quality | P2 | M |
| [GAP-13](#gap-13) | Claim support is judged by one model only | Answer quality | P2 | M |
| [GAP-14](#gap-14) | Punctuation splits one question into several topics | Data model | P2 | S |
| [GAP-15](#gap-15) | No token or cost accounting | LLM operations | P2 | S |
| [GAP-16](#gap-16) | Research-on-ask and reindex hold HTTP requests for minutes | Reliability | P2 | M |
| [GAP-17](#gap-17) | Research jobs are lost on restart | Reliability | P2 | M |
| [GAP-18](#gap-18) | No deletion or retention controls | Data lifecycle, privacy | P2 | M |
| [GAP-19](#gap-19) | The research pipeline is one 646-line function over untyped dicts | Maintainability | P2 | L |
| [GAP-20](#gap-20) | No static analysis or coverage gates | Engineering process | P2 | S |
| [GAP-21](#gap-21) | Frontend resilience and test gaps | Frontend | P2 | M |
| [GAP-22](#gap-22) | Observability is logs only | Operations | P2 | M |
| [GAP-23](#gap-23) | Frontend CSP allows connections to any HTTPS origin | Security | P2 | S |
| [GAP-24](#gap-24) | The answer path has side effects and trusts local PDFs blindly | Data integrity | P2 | S |
| [GAP-25](#gap-25) | The PDF parser has no memory cap | Security | P2 | S |
| [GAP-26](#gap-26) | API docs are public (fixed when hosted) | Security | P3 | S |
| [GAP-27](#gap-27) | Retrieval is dense-only and tuned for one model | Answer quality | P3 | M |
| [GAP-28](#gap-28) | Repeated questions always call the model | Cost | P3 | S |
| [GAP-29](#gap-29) | Prompts are not versioned | LLM operations | P3 | S |
| [GAP-30](#gap-30) | Topic audits have no schema version | Data model | P3 | S |
| [GAP-31](#gap-31) | Supply-chain hygiene | Security | P3 | S |
| [GAP-32](#gap-32) | Version numbers disagree | Engineering process | P3 | S |
| [GAP-33](#gap-33) | Repository clutter and unfinished visual and accessibility QA | Frontend, hygiene | P3 | S |
| [GAP-34](#gap-34) | Report rendering relies on private fpdf2 internals | Maintainability | P3 | S |

## P0 — fix now

### GAP-01

**No version control, so CI never runs** · Engineering process · P0 · S

- **Evidence:** the project root has no `.git` directory, yet [`.github/workflows/ci.yml`](../.github/workflows/ci.yml) defines tests, a build, `pip-audit`, and `npm audit`.
- **Impact:**
  - Nothing records history or allows rollback.
  - Changes cannot be reviewed.
  - The quality gates exist on paper only.
  - A bad edit or disk failure loses work, since the code exists in one copy.
- **Recommendation:** run `git init` and commit the code. `.gitignore` already excludes `.env`, the snapshots and PDFs in `data/`, topic and unanswered records in `memory/`, `chroma_db/`, and `logs/`. Decide on the root mood-board images (GAP-33) and check `git status` before the first commit. Push to a private remote, enable the workflow, and protect the main branch (CI must pass).
- **Done when:** CI runs on every push and the main branch is protected.
- **Status (2026-10-20): mostly done.** The code is on GitHub (`shubhajeet1207/spymonk-encyclopedia`), and the CI pipeline runs on every pull request to `main` ([CI_CD.md](CI_CD.md)). Open: the owner turning on branch protection and push protection.

### GAP-02

**Answers are not checked against their evidence** · Answer quality (OWASP LLM05 improper output handling, LLM09 misinformation) · P0 · M

- **Evidence:** in [`query.py`](../backend/core/query.py), `answer_from_documents` returns the model's text as-is. Specifically:
  - nothing checks that its statements appear in the context;
  - `sources` lists every retrieved passage, not the ones used;
  - "not found" is detected by exact string equality with `NOT_FOUND`, so a variant ("…archive" without the period, or a translation) is shown as an answer.
- **Impact:**
  - Research holds claims to a strict standard, but the answer step can add, merge, or overstate facts, and the UI presents the result with authoritative-looking citations.
  - Smaller models (the configured Nemotron "lightning" model) are more prone to this.
- **Recommendation:**
  - Ask for a structured answer, `{"answer_sentences": [{"text", "excerpt_ids"}], "not_found": bool}`, and validate that every sentence cites existing excerpts.
  - Check each sentence against its cited excerpts: lexical overlap with the claim's quote as a cheap first check, then a judge call or an NLI model for the rest. Drop unsupported sentences; if none remain, return not-found.
  - Return only the cited sources, and show the citation markers in the UI.
- **Done when:** an evaluation case with a deliberately unsupported sentence is filtered out, sources match the citations, and not-found is a field rather than a string match.

## P1 — before hosting or next iteration

### GAP-03

**Local-only mode and rate limits trust the socket peer** · Security (ASVS V4 access control) · P1 · S

- **Evidence:**
  - [`security.py`](../backend/api/security.py) `require_api_access` lets tokenless requests through when `request.client.host` is `127.0.0.1` or `::1`.
  - [`rate_limit.py`](../backend/services/rate_limit.py) `client_key` uses the same value.
- **Impact:**
  - Behind a reverse proxy on the same host (nginx, Caddy, a tunnel), every request arrives from 127.0.0.1. A missing `APP_API_TOKEN` then exposes the whole API publicly, including research spending, uploads, and index clearing.
  - All users share one rate-limit bucket, so one user can lock out everyone.
- **Recommendation:**
  - Refuse to start without `APP_API_TOKEN` unless bound to a loopback address and no proxy is configured.
  - Add a `TRUSTED_PROXIES` setting: derive the client address from `X-Forwarded-For` only when the peer is a trusted proxy, and key rate limits on that, or on a token hash when a token is set.
- **Done when:** tests cover a proxied request without a token (rejected) and per-client buckets behind a proxy.
- **Status (2026-10-20): partly done.** A hosted deployment now refuses to start without `APP_API_TOKEN` (ADR-030). Proxy-aware client identity and per-client rate limits remain open.

### GAP-04

**Indexed text enters the answer prompt as trusted instructions** · Security (OWASP LLM01 prompt injection) · P1 · S

- **Evidence:**
  - [`query.py`](../backend/core/query.py) `answer_from_documents` formats retrieved passages directly into the system prompt.
  - Research prompts wrap source text in a `<source_text>` block it cannot close, with an "untrusted data" notice (`_source_block` in [`research.py`](../backend/core/research.py)); the answer path has no equivalent.
- **Impact:** an uploaded PDF, or a quote taken from a hostile page, can carry instructions ("ignore the rules above and…") that the answer model treats as system-level. Uploaded PDFs are trusted as `local_source` and indexed in full.
- **Recommendation:**
  - Keep the instructions in the system message, and pass passages in the user message inside delimited, tag-neutralized blocks with the same untrusted-data notice.
  - Add injection test fixtures to the evaluation set (GAP-05).
- **Done when:** a fixture passage containing instructions does not change the answer's format or rules.

### GAP-05

**No evaluation harness for retrieval and answers** · Answer quality · P1 · M

- **Evidence:**
  - There is no golden question set and no metric.
  - Model qualification ([TRD §9.3](TRD.md)) is a manual replay; the replay scripts exist only as investigation records in `logs/diagnostics/`.
  - The retrieval threshold (`RETRIEVE_MIN_SIMILARITY`) was calibrated by hand on one model.
- **Impact:**
  - Model swaps, prompt edits, and threshold changes cannot be judged, and regressions go unnoticed.
  - This session's model problems (DeepSeek timeouts; Nemotron malformed JSON at about one in 11 replies) were found by hand after users hit them.
- **Recommendation:**
  - Add `backend/tools/evaluate.py` with a versioned set of saved snapshots, questions, expected claims, and expected not-found cases. Include multilingual (Hindi) cases and injection cases.
  - Report retrieval hit rate, answer faithfulness and citation precision (after GAP-02), unanswered rate by reason, malformed-reply rate, p50 and p95 latency, and calls and tokens per run.
  - Run it on model and prompt changes, and nightly against the configured provider.
- **Done when:** one command produces a scored report, and the TRD's model-qualification criteria are checked automatically.

### GAP-06

**Source collection and candidate selection starve corroboration** · Answer quality · P1 · M

- **Evidence:**
  - [`scraper.py`](../backend/core/scraper.py) `collect_research_sources` stops at `MAX_WEB_HTML` pages with no per-domain limit.
  - The candidate loop in [`research.py`](../backend/core/research.py) (around line 573) keeps the first `MAX_CANDIDATE_CLAIMS_PER_TOPIC` claims in source order.
  - In the 2026-10-19 Docker run, 3 of 6 sources came from docker.com and the first two sources filled the 12-claim cap, leaving 0 verified answers. The developer log now flags both conditions (`low_origin_diversity`, `candidate_cap_order_bias`).
- **Impact:** questions answerable from the web end as "We cannot verify the claims…" because the pipeline didn't give claims a chance at a second domain.
- **Recommendation:**
  - Cap pages per registrable domain (for example 2) and keep collecting until `MAX_WEB_HTML` is reached across domains.
  - Pick candidates round-robin across sources, preferring claims that already appear in more than one source.
  - Ask the planner for queries that reach different source types.
  - Measure the effect with GAP-05.
- **Done when:** the evaluation shows a lower `no_independent_support` rate with no drop in faithfulness.

### GAP-07

**Reindex can silently drop newly saved claims** · Data integrity · P1 · M

- **Evidence:**
  - [`reindex.py`](../backend/core/reindex.py) `run_reindex` reads all audits (line 134) without holding `store.write_lock`, then rebuilds and swaps under the lock.
  - [`store.py`](../backend/core/store.py) `rebuild_collection` deletes the live collection (line 552) before renaming the new one.
- **Impact:**
  - If a research job saves while a reindex is embedding, its claims go into the old collection, which the swap discards. Their audit still says `indexed`, so nothing reports the loss until the next rebuild.
  - Between the delete and the rename, readers see no index, and a question in that window falls through to "no saved index".
- **Recommendation:**
  - Run reindex as a background job that blocks research saves for its duration (a maintenance flag checked before `save_topic`), or re-read audits modified after the snapshot and apply them before the swap.
  - Mark `index_state` from what was actually embedded.
  - Rename atomically (rename the old collection aside, rename the new one in, then drop the old one).
- **Done when:** a test that saves a topic during a reindex finds its claims in the rebuilt index.

### GAP-08

**No backup or restore; data lives in the source tree** · Data, operations · P1 · S

- **Evidence:**
  - `SPYMONK_DATA_DIR` defaults to the repository root ([`config.py`](../backend/core/config.py) line 63), so `data/`, `memory/`, `chroma_db/`, and `logs/` sit beside the code.
  - There is no backup or restore tool or documented procedure.
- **Impact:**
  - Audits, snapshots, and uploaded PDFs are the source of truth, and the index can be rebuilt only from them; losing the folder loses the archive.
  - Code operations (clean checkouts, image builds, `git clean`) can delete or ship user data.
- **Recommendation:**
  - Default the data directory to a per-user location outside the repository (for example `~/.spymonk`), with the current layout as an explicit option.
  - Add `python -m backend.tools.backup` and `restore`: archive `memory/`, `data/`, and optionally `logs/`. The index is rebuildable, so it can be excluded.
  - Document a schedule and test the restore.
- **Done when:** a restore into an empty directory, followed by a reindex, reproduces the archive.
- **Status (2026-10-20): done for hosted use.** `BACKUP_TARGET` mirrors the archive to a private Hugging Face dataset or a directory, restores it at startup, and rebuilds the index; `python -m backend.services.backup push|pull` copies it by hand. Verified by restoring a copy of the live archive into an empty directory. Locally, the data directory still defaults to the repository.

### GAP-09

**No deployable artifact** · Operations (12-factor dev/prod parity) · P1 · M

- **Evidence:** the TRD describes the hosted topology, but the repository has no Dockerfile or compose file, no process-manager configuration, and no readiness check. PDF reports need system fonts (Noto) that a slim image lacks.
- **Impact:** every deployment is hand-built, so fonts, the single-process rule, proxy headers, the volume mount, and non-root execution can each be got wrong.
- **Recommendation:** add a Dockerfile:
  - `python:3.11-slim`, `fonts-noto-core`, and optional CPU-only torch;
  - a non-root user, `SPYMONK_DATA_DIR=/data` on a volume, one uvicorn worker, and proxy headers set from `TRUSTED_PROXIES` (GAP-03);
  - a `HEALTHCHECK` against a readiness endpoint (GAP-22).

  Add a compose file for local parity.
- **Done when:** `docker compose up` gives a working instance, the test suite runs inside the image in CI, and the TRD's deployment section references it.
- **Status (2026-10-20): mostly done.** A [`Dockerfile`](../Dockerfile) (UI and API in one image, Noto fonts, CPU torch, baked model, non-root) and a deploy script for Hugging Face Spaces exist, and CI builds the image. Open: a compose file, and a readiness check (GAP-22).

### GAP-10

**Public-hosting compliance is unresolved** · Compliance, privacy · P1 · M plus owner decisions

- **Evidence:**
  - `fetch_url` ignores `robots.txt`.
  - Search uses `ddgs`, which scrapes search engines without an API.
  - Full copies of third-party pages are kept indefinitely.
  - Questions and claims are stored in audits, unanswered records, and run logs.
  - The PRD is an unapproved draft ([TRD §17](TRD.md)).
- **Impact:** fine for one person's local use; for public hosting it raises search-provider terms-of-service, copyright, crawler-etiquette, and personal-data issues.
- **Recommendation:**
  - Honor `robots.txt` (cached per host).
  - Decide on a licensed search API.
  - Set a snapshot retention period (GAP-18).
  - Publish a privacy notice covering what is sent to the LLM, embedding, and search providers, and what is stored and for how long.
  - Get PRD approval for the hosted boundary.
- **Done when:** the owner's decisions are recorded as ADRs and the code follows them.

## P2 — planned work

### GAP-11

**Model JSON output is unconstrained** · LLM robustness · P2 · M

- **Evidence:**
  - No call requests JSON mode or a schema.
  - About one in 11 cross-check replies from Nemotron were malformed (unquoted `reason` values); retrying the same prompt reproduced the defect.
  - The per-claim fallback (ADR-025) limits the damage but costs extra calls.
- **Recommendation:**
  - Use provider JSON mode or schema-constrained decoding where supported, behind a per-provider capability setting.
  - Repair narrowly defined, known defects before parsing.
  - Track the malformed rate in the evaluation (GAP-05).

### GAP-12

**Corroboration counts domains, not independent content** · Answer quality · P2 · M

- **Evidence:** [`evidence.py`](../backend/core/evidence.py) `has_independent_support` counts distinct registrable domains. Mirrors and syndicated or copied pages on different domains count as independent. This is a risk rather than an observed defect: in the 2026-10-20 Docker run, none of the 13 cross-domain quote pairs were near-identical.
- **Recommendation:**
  - Compare supporting passages with shingled hashes or MinHash; treat near-duplicate sources (for example over 0.8 similarity) as one origin.
  - Consider source-quality tiers (official documentation, academic, news, forum).

### GAP-13

**Claim support is judged by one model only** · Answer quality · P2 · M

- **Evidence:** the same model extracts claims and judges support ([ADR-021](DECISIONS.md)). The verbatim-quote check proves the quote exists, not that it entails the claim.
- **Recommendation:**
  - Add an entailment check between claim and quote: an NLI model, or a second judge model on disagreement. Store the score in the evidence.
  - Make a low score hold the claim back, as `incomplete` does now.

### GAP-14

**Punctuation splits one question into several topics** · Data model · P2 · S

- **Evidence:** [`memory.py`](../backend/core/memory.py) `canonical_topic` keeps punctuation. "how to run a docker container?" maps to `…-99a02909dc5c`, while "How to run a docker container" maps to `…-61bae8df31e7`.
- **Impact:** the same question creates duplicate topics and misses the exact-topic lookup, falling back to semantic search.
- **Recommendation:** strip punctuation, and possibly a trailing question word, from the topic key; migrate existing audits with the existing migration tool, merging duplicates.

### GAP-15

**No token or cost accounting** · LLM operations (OWASP LLM10 unbounded consumption) · P2 · S

- **Evidence:** `LLM_DAILY_CALL_LIMIT` counts calls; no code records `usage_metadata` (prompt and completion tokens).
- **Impact:** a run's real cost is unknown, and a few 45,000-character extraction prompts cost far more than many short calls.
- **Recommendation:** record tokens per call in the trace, run log, and `/api/status`; add an optional daily token ceiling; include cost per run in the evaluation.

### GAP-16

**Research-on-ask and reindex hold HTTP requests for minutes** · Reliability · P2 · M

- **Evidence:**
  - [`questions.py`](../backend/api/routes/questions.py) line 37 waits up to `RESEARCH_MAX_SECONDS + 60` (960 seconds by default) inside the ask request; legacy `POST /api/research` does the same.
  - `POST /api/memory/reindex` runs synchronously.
- **Impact:** reverse proxies and browsers commonly time out after 60–100 seconds, so the client loses a result the server still produces. A thread is held for the duration.
- **Recommendation:** return a job ID from ask-with-research and reindex, and have the frontend follow the job with the existing event stream. Retire the waiting endpoints once clients have moved.

### GAP-17

**Research jobs are lost on restart** · Reliability · P2 · M

- **Evidence:** [`jobs.py`](../backend/services/jobs.py) keeps jobs in memory. After a restart, including every `--reload`, a client's reconnect gets "Research job not found. It may have expired."
- **Recommendation:** persist job records (status, events, result) in SQLite, the deferred storage decision. On startup, mark unfinished jobs `interrupted` so the UI can offer a rerun.

### GAP-18

**No deletion or retention controls** · Data lifecycle, privacy · P2 · M

- **Evidence:**
  - The only delete operation is clearing the whole index.
  - There is no way to delete a topic, an uploaded PDF, an unanswered record, a snapshot, or logs.
  - The local instance has 5 PDFs on disk and 1 indexed (duplicate and unreadable copies).
  - Audit history, snapshots, and unanswered records grow without bound.
- **Recommendation:**
  - Add delete endpoints and UI for topics (audit, index entries, history), PDFs (file and chunks), and unanswered records.
  - Add retention settings and a purge tool for history, snapshots, unanswered records, and run logs.
  - Support erasure of a question across all stores.

### GAP-19

**The research pipeline is one 646-line function over untyped dicts** · Maintainability (ISO 25010 modularity, testability) · P2 · L

- **Evidence:**
  - `research_topic` spans [`research.py`](../backend/core/research.py) lines 320–966, with nested closures for progress, saving, extraction, and cross-checks.
  - Archives, claims, and outcomes are plain dicts whose fields are re-declared in API schemas, reports, and run logs.
- **Impact:** each change, such as today's partial saves, unanswered records, and run logs, touches the same function. Field names drift between producers and consumers, and individual stages are hard to test.
- **Recommendation:** split the function into stage functions (collect, read, extract, select, cross-check, classify, persist) over a typed `RunContext` and `RunOutcome` (dataclasses or Pydantic). Do it behind the existing tests, without behavior changes.

### GAP-20

**No static analysis or coverage gates** · Engineering process · P2 · S

- **Evidence:** `requirements-dev.txt` installs `pytest` and `pip-audit` only. CI runs no linter, formatter, or type checker for either Python or JavaScript, and measures no coverage.
- **Recommendation:**
  - Add `ruff` (lint and format) and `mypy` or `pyright`, gradually, starting with `backend/core/evidence.py`, `verify.py`, and `store.py`.
  - Add ESLint (React hooks rules) and Prettier.
  - Add a coverage report with a floor that only rises.
- **Status (2026-10-20): mostly done.** CI runs `ruff` (pyflakes, bugbear, bandit), ESLint with the React hooks rules, and coverage with an 83% floor. Open: type checking and a formatter.

### GAP-21

**Frontend resilience and test gaps** · Frontend · P2 · M

- **Evidence:**
  - There is no React error boundary, so one render error blanks the workspace.
  - Tests cover pure modules only (`api`, `pipeline`, `outcome`, `privacy`), not components or user flows.
  - Destructive actions use `window.confirm` ([`App.jsx`](../frontend/src/App.jsx) lines 222 and 233).
  - `App.jsx` holds 343 lines of interdependent state.
- **Recommendation:**
  - Add an error boundary per panel.
  - Add React Testing Library tests for research, unanswered, and answer outcomes.
  - Add a Playwright end-to-end test (research, then answer, then report download) against a mocked API.
  - Replace `confirm` with an accessible dialog.
  - Move workspace state into a reducer.

### GAP-22

**Observability is logs only** · Operations (SRE) · P2 · M

- **Evidence:**
  - `/api/health` returns `ok` unconditionally ([`system.py`](../backend/api/routes/system.py) line 14).
  - Readiness (index, LLM configuration, disk) is only in the authenticated `/api/status`.
  - There are no metrics or service-level objectives.
- **Recommendation:**
  - Add `/api/ready` (index reachable, data directory writable, LLM configured).
  - Export metrics (Prometheus or OpenTelemetry): research outcomes by reason, stage durations, model latency and errors, malformed-reply rate, unanswered rate, queue depth.
  - Define SLOs, for example research success rate and p95 answer latency, with alerts.

### GAP-23

**Frontend CSP allows connections to any HTTPS origin** · Security (ASVS V14 configuration) · P2 · S

- **Evidence:** [`vercel.json`](../frontend/vercel.json) sets `connect-src 'self' https: http://localhost:* http://127.0.0.1:*`.
- **Impact:** if a script injection ever succeeds, the policy does not stop it sending the session's API token to any HTTPS server. The policy otherwise follows the strict API CSP.
- **Recommendation:** generate the header at build time with the exact `VITE_API_BASE_URL` origin; keep the localhost entries for development builds only.

### GAP-24

**The answer path has side effects and trusts local PDFs blindly** · Data integrity · P2 · S

- **Evidence:**
  - [`verify.py`](../backend/core/verify.py) `source_text_from_record` refetches a missing snapshot over the network and saves it during a question.
  - `document_still_verified` returns `True` for any `local_pdf` chunk without checking that the file still exists.
- **Impact:**
  - Asking a question can make outbound requests and write files.
  - Evidence checked against a refetched, changed page passes without a recorded hash.
  - A deleted PDF keeps answering.
- **Recommendation:**
  - On the read path, exclude evidence whose snapshot is missing and queue a refresh instead of fetching.
  - Check local PDF chunks against the file's hash (`document_sha256` is already in the metadata).

### GAP-25

**The PDF parser has no memory cap** · Security (resource exhaustion) · P2 · S

- **Evidence:** [`ingest.py`](../backend/core/ingest.py) `_parse_pdf_isolated` limits time, pages, and output size, but not memory.
- **Impact:** a crafted PDF with compressed streams can exhaust the host's memory before the timeout fires.
- **Recommendation:** set `RLIMIT_AS` in the worker on POSIX, or use a Windows Job Object; in containers, rely on the container memory limit (GAP-09).

## P3 — hygiene

### GAP-26

**API docs are public** · Security · P3 · S

- **Evidence:** `FastAPI()` in [`main.py`](../backend/main.py) (line 231) keeps the default `/docs`, `/redoc`, and `/openapi.json`, which require no token.
- **Recommendation:** disable them when `APP_API_TOKEN` is set, or serve them behind the token.
- **Status (2026-10-20): done for hosted mode.** `APP_API_DOCS` defaults to off when hosted.

### GAP-27

**Retrieval is dense-only and tuned for one model** · Answer quality · P3 · M

- **Evidence:** `query.retrieve` uses vector similarity only. The cut-off was calibrated on all-MiniLM-L6-v2, and there is no result diversification (MMR).
- **Impact:** exact terms (CLI flags such as `--rm`, names, codes) match poorly.
- **Recommendation:** add hybrid retrieval (BM25 plus vectors, fused by rank), MMR, and per-model thresholds stored with the index fingerprint; validate with GAP-05.

### GAP-28

**Repeated questions always call the model** · Cost · P3 · S

- **Recommendation:** cache answers keyed by the normalized question, the IDs of the retrieved passages, and the model and prompt versions. Invalidate on index changes.

### GAP-29

**Prompts are not versioned** · LLM operations · P3 · S

- **Evidence:** prompts are string constants in `research.py`, `query.py`, and `search_planner.py`; run logs record the model but not the prompt.
- **Recommendation:** give each prompt a version or content hash and record it in run logs and evaluation reports, so quality changes can be attributed.

### GAP-30

**Topic audits have no schema version** · Data model · P3 · S

- **Evidence:** `TopicArchive` in [`memory.py`](../backend/core/memory.py) has no version field ([TRD §6.6](TRD.md)).
- **Recommendation:** add `schema_version`, with a migration hook, before the next audit format change.

### GAP-31

**Supply-chain hygiene** · Security · P3 · S

- **Evidence:** `constraints.txt` pins exact versions without hashes, there's no automated dependency-update process, and the virtual environment's pip is version 23.2.1.
- **Recommendation:** generate hashed locks (`pip-compile --generate-hashes` or `uv`), enable Dependabot or Renovate, and upgrade pip in CI.
- **Status (2026-10-20): partly done.** Dependabot opens weekly grouped updates, and CI audits the exact pinned versions. Open: hashed locks.

### GAP-32

**Version numbers disagree** · Engineering process · P3 · S

- **Evidence:** the API reports 2.3.0, the UI footer shows V2.3, and [`frontend/package.json`](../frontend/package.json) says 2.0.0.
- **Recommendation:** keep one version source (for example a `VERSION` file read by both builds) and tag releases.
- **Status (2026-10-20): versions aligned** (UI package 2.3.0). Open: a single version source and release tags.

### GAP-33

**Repository clutter and unfinished visual and accessibility QA** · Frontend, hygiene · P3 · S

- **Evidence:**
  - Two mood-board images (`9043432.jpg`, `9046442.jpg`, about 1.9 MB) and `design-qa.md` sit at the repository root.
  - `design-qa.md` says the visual comparison is still pending.
  - No WCAG audit has been done.
- **Recommendation:** move the design references to `docs/design/`, or delete them; finish the visual QA in a browser; run an automated accessibility check (axe) in the Playwright tests (GAP-21).

### GAP-34

**Report rendering relies on private fpdf2 internals** · Maintainability · P3 · S

- **Evidence:** the report renderer's `font()` method in [`reports.py`](../backend/services/reports.py) calls `self._out` and `self._set_font_for_page` to work around an fpdf2 font-state bug with fallback fonts. A regression test covers it.
- **Recommendation:** keep fpdf2 pinned, report the bug upstream, and remove the workaround when a fixed release is adopted.

## Suggested sequence

1. **Now:** GAP-01 (version control and CI), then GAP-02 (grounded answers).
2. **Measure, then improve answers:** GAP-05 (evaluation), then GAP-06, GAP-04, and GAP-11, each judged by the evaluation.
3. **Before anyone else uses it:** GAP-03, GAP-08, GAP-09, GAP-07, then GAP-10 once the owner has decided.
4. **Reliability and lifecycle:** GAP-16, GAP-17, GAP-22, GAP-18, GAP-24.
5. **Engineering health:** GAP-20 first, since it makes GAP-19 safer, then GAP-19 and GAP-21.
6. **Quality depth:** GAP-12, GAP-13, GAP-14, GAP-15, then the P3 items.

## Decisions for the owner

| Decision | Affects | Options |
| --- | --- | --- |
| Where the code lives | GAP-01 | Private Git remote (GitHub, GitLab), and who reviews |
| Hosted or local only | GAP-03, 09, 10, 23 | Stay single-user local; or host behind a proxy with a token or real login |
| Search provider | GAP-10 | Keep `ddgs`; or a licensed API (Brave, Bing, Google Programmable Search) |
| Retention periods | GAP-10, 18 | Days to keep snapshots, history, unanswered records, run logs |
| Durable storage | GAP-17, 19 | SQLite for jobs (and later audits) now or later |
| Clean-up | GAP-18, 33 | Delete the duplicate and unreadable PDFs on disk and the root mood-board images |

## Definition of done (every issue)

- A regression test that fails before the change and passes after; the full backend and frontend suites pass.
- No secrets, prompts, or document text added to logs.
- The TRD, decision log, or README is updated when behavior changes.
- For answer-quality changes, an evaluation report (GAP-05) before and after.
