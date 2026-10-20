# SpyMonk Encyclopedia — Product Requirements Document

| Field | Value |
| --- | --- |
| Status | Review draft — approval required |
| Version | 2.0 draft |
| Product | SpyMonk Encyclopedia |
| Owner | Product owner / project maintainer |
| Audience | Product owner, design, engineering, and reviewers |
| Last updated | 2026-10-18 |

> This document defines the product we intend to build. It is not a description of what the current code already guarantees. The existing [TRD](TRD.md) is intentionally unchanged pending approval of this PRD; after approval, it should be reconciled to these requirements.

## 1. Executive summary

SpyMonk Encyclopedia is a personal research workspace that helps a user answer questions from a small, traceable body of local documents and carefully checked public sources. It should remember what has already been researched, search that memory first, and make it easy to distinguish supported conclusions from weak, disputed, or missing evidence.

The product’s central promise is not “the AI is always right.” It is: **each answer makes clear what evidence supports it, where that evidence came from, how well sources agree, and what remains uncertain.**

For historical and multilingual material—including Sanskrit and Hindi—the product must preserve the source text and separate transcription, translation, interpretation, and modern commentary. A PDF is not automatically authoritative, and multiple web pages are not automatically independent.

## 2. Problem

Research across PDFs and web articles is fragmented. Search results often repeat one another, secondary authors can paraphrase or misread a primary text, and language-model answers can blend sourced material with unsupported prior knowledge. A conventional “chat with documents” flow may retrieve a passage but still fail to show whether it is direct evidence, a translation, an interpretation, or a copied claim.

The current project has a useful first implementation, but its product contract is not yet clear enough. In particular, it needs explicit behavior for source quality, disagreement, memory freshness, scanned documents, multilingual interpretation, and what the UI should say when evidence is inadequate. This PRD establishes those expectations before the technical design is revised.

## 3. Product goal and principles

### Product goal

Help one researcher build and query a reusable evidence archive, producing concise answers whose factual statements can be traced to inspected evidence and whose uncertainty is visible.

### Product principles

1. **Evidence before fluency.** A cautious “not enough evidence” is better than a confident unsupported answer.
2. **Provenance is part of the answer.** Users can inspect the supporting excerpt, source, date or edition when available, and validation state.
3. **Agreement is not proof.** Corroboration must account for source independence and shared origins, not just the number of URLs.
4. **Disagreement is data.** Preserve competing interpretations and explain what evidence favors each; do not silently average them into consensus.
5. **Memory is inspectable and refreshable.** Reuse prior research, show when it was collected, and make refresh behavior explicit.
6. **Language layers stay distinct.** Keep original text separate from transliteration, translation, and interpretation.
7. **Privacy is explicit.** Local storage does not mean no external processing: disclose when excerpts or questions are sent to a configured model or search provider.

### Visual direction — proposed from the supplied references

The UI should take visual inspiration from the two supplied retro desktop-interface illustrations: [example 1](../9043432.jpg) and [example 2](../9046442.jpg). Treat them as a mood board for the app’s visual language, not as literal screen layouts or content to copy.

- **Palette:** warm cream/peach for primary surfaces, orange/coral for prominent actions and active title bars, pale lavender for secondary surfaces and selected states, and deep navy/ink for text, rules, and outlines.
- **Shape and detail:** crisp outlined panels, restrained desktop-window chrome, simple illustrated/icon-library symbols, and a light paper/grain accent only in low-information areas. Avoid decorative texture behind long text or evidence.
- **Typography:** highly legible body text for research and citations; a more characterful display face may be used sparingly for short labels. Keep long-form excerpts and controls easy to scan.
- **Product adaptation:** apply the retro cues to a clear research workspace with predictable navigation, source/evidence panels, and calm reading surfaces. Do not recreate the reference collages, stack modal dialogs, or let decoration compete with citations.
- **Accessibility:** maintain readable contrast, visible keyboard focus, familiar control behavior, responsive layouts, and status cues that do not rely on color alone. Error, progress, and confirmation states should be visually distinct and understandable without the decorative theme.

The working assumption is a **retro-inspired research tool with modern readability**, rather than a pixel-for-pixel vintage desktop clone. This is the proposed UI direction for approval; no UI implementation is included in this PRD update.

## 4. Users and primary use cases

### Primary user

An individual researcher, student, or curious reader who wants a persistent, source-grounded archive without managing a multi-user knowledge platform.

### Core use cases

- Add a local PDF or a set of PDFs and ask questions about their contents.
- Research a topic from public web pages and documents, then keep the evidence for later questions.
- Ask a question already covered by memory and get an answer without repeating the entire research workflow.
- Investigate a new question, compare independent sources, and identify direct contradictions.
- Examine a classical or multilingual passage without confusing the original verse with an author’s or model’s interpretation.
- Understand why a document was not indexed and what action can make it searchable.

## 5. Goals and non-goals

### Goals for the first approved release

- Support a single-user, local-first research archive for local documents and bounded public-web research.
- Prefer stored, verified evidence before initiating new web research.
- Make a deliberate distinction between answerable, single-source, conflicted, and unsupported claims.
- Show source-level evidence for answers and retain an audit trail for researched topics.
- Detect PDFs that yield no searchable text and guide the user through a local OCR or alternate-file path.
- Keep model-produced translation or interpretation visibly labeled and non-authoritative.

### Non-goals for the first release

- Guaranteeing factual truth or replacing expert, legal, medical, or scholarly review.
- Crawling the open web at scale, accessing private/authenticated sites, or bypassing paywalls.
- Building a collaborative multi-user library, public publishing platform, or hosted SaaS service.
- Treating a Bloom filter, vector similarity score, or LLM confidence statement as proof that a remembered claim is correct.
- Automatically deciding the definitive meaning of a classical text when reliable editions or human review are absent.

## 6. Product experience and primary flows

### A. Ask from the archive

1. The user asks a question.
2. SpyMonk searches existing memory for relevant passages and claims, including exact-topic and semantic matches.
3. It checks whether the retrieved evidence is sufficient, current enough for the question, and still traceable to its stored source snapshot.
4. If sufficient, it answers from that evidence and presents supporting citations and excerpts. If evidence conflicts, it presents the conflict rather than hiding it.
5. If evidence is insufficient, it states what is missing. When the user has enabled research-on-miss, it searches the web, validates the new evidence, stores the resulting archive, and retries the question once.

The memory lookup must retrieve candidate evidence and its conclusion; a probabilistic membership check alone is not a valid answer path. A Bloom filter may only be considered as a non-authoritative optimization in a later technical design.

### B. Research a topic

1. The user enters a topic or question and chooses research or refresh.
2. SpyMonk gathers a bounded set of public HTML and document sources and displays what it found, including sources it could not read.
3. Candidate claims are tied to exact source passages before they can be considered for the archive.
4. The system checks each candidate against other sources, records support and contradiction separately, and assesses whether sources are genuinely independent or likely to share a source.
5. Claims that meet the evidence bar become answerable. Single-source claims and unresolved disagreements remain visible in the topic record but are excluded from ordinary answers.

### C. Add a local document

1. The user uploads or selects a PDF.
2. SpyMonk extracts text and reports whether it found searchable content, pages, and any extraction problems.
3. If no usable text is found, the UI says the document was not indexed and offers the OCR/alternate-document path. OCR, if enabled, runs locally and must not silently send the document to a third party.
4. Successfully extracted text is indexed with document and page provenance. User-provided documents are labeled as such, not described as independently verified.

### D. Interpret original-language material

1. The user asks about a passage or a claim attributed to a source text.
2. SpyMonk retains and displays the original passage and identifies the edition/source when available.
3. It distinguishes a cited published translation from a model-generated gloss and from an interpretive conclusion.
4. Where translations or commentaries differ, it presents the alternatives and their evidence. Without adequate source support, it marks the interpretation unresolved and recommends qualified human review.

## 7. Functional requirements

Priority labels: **P0** is required for the first approved release; **P1** is valuable follow-up scope.

| ID | Priority | Requirement |
| --- | --- | --- |
| PR-01 | P0 | Users can add local PDF documents and see ingestion status, source name, page information, and searchable-text outcome. |
| PR-02 | P0 | Empty, image-only, corrupt, or unsupported documents must not be reported as successfully indexed. The UI must explain the failure and next action. |
| PR-03 | P0 | The product supports public HTML articles as research sources in addition to PDFs, retaining source URL and the captured evidence used. |
| PR-04 | P0 | Every web-derived candidate claim must be tied to an exact excerpt from the source before promotion to answerable memory. |
| PR-05 | P0 | Cross-checking records source URL, origin, source type, quote, and whether the evidence supports, contradicts, or is unrelated to the candidate. |
| PR-06 | P0 | The system must avoid counting obvious mirrors, syndications, or sources that merely cite the same underlying report as independent confirmation. If independence is unclear, it must disclose that limitation. |
| PR-07 | P0 | The system preserves conflicting claims and evidence. It does not make a disputed claim answerable unless a documented resolution rule is satisfied; otherwise the response exposes the disagreement. |
| PR-08 | P0 | A PDF can help resolve a conflict only when it provides direct, verifiable evidence relevant to the claim. PDF format alone does not grant authority. |
| PR-09 | P0 | Ordinary answers use only eligible archive evidence and show citations/excerpts close to the claims they support. Unsupported factual assertions are omitted or explicitly labeled as inference. |
| PR-10 | P0 | Memory is checked before fresh web research. Automatic research on a miss is an explicit user setting; manual refresh remains available when memory already has an answer. |
| PR-11 | P0 | Stored web evidence can be rechecked against a retained source snapshot; an unavailable or changed source must be disclosed and must not be silently treated as freshly verified. |
| PR-12 | P0 | Local documents, web-derived claims, corroborated claims, single-source claims, and conflicts are visibly distinguishable in the UI and archive. |
| PR-13 | P0 | The system reports source-collection and extraction limits, including when search found no usable sources or a PDF could not be parsed. |
| PR-14 | P0 | The interface discloses which data stays local and which prompts, excerpts, or metadata may be sent to configured external search, embedding, or language-model services. |
| PR-15 | P0 | For Sanskrit, Hindi, or other non-English sources, preserve original text and label transliteration, published translation, model gloss, and interpretation separately. Unverified model translation cannot be presented as the source’s definitive meaning. |
| PR-16 | P1 | Provide user-controlled local OCR for scanned PDFs, including language selection where supported, OCR confidence/quality warnings, and preservation of the original PDF. |
| PR-17 | P1 | Provide archive review tools to inspect, remove, refresh, or reprocess sources and to see why a claim was accepted or withheld. |
| PR-18 | P1 | Allow export of an answer and its evidence in a portable format such as Markdown, preserving links and uncertainty labels. |
| PR-19 | P0 | Use the approved retro desktop-inspired visual direction across the research, archive, ingestion, and evidence-review surfaces while preserving readability, accessibility, and a clear information hierarchy. |

## 8. Evidence and answer policy

### Claim states

| State | Meaning | Normal answer behavior |
| --- | --- | --- |
| `local_source` | Excerpt comes from a document the user selected. | May be used, labeled as user-provided and not independently validated. |
| `quote_verified` | A source contains the cited excerpt. | Attribution is verified; truth is not established by this state alone. |
| `corroborated` | Multiple sufficiently independent sources support the same bounded claim and no unresolved material contradiction is found. | May be stated with citations and a visible corroboration label. |
| `document_supported` | A source document directly supports the claim under the documented evidence policy. | May be used with the document, edition/page, and any remaining caveats shown. |
| `single_source` | Only one independent origin supports the claim. | Do not present as established fact; may show as limited evidence. |
| `conflicted` | Relevant sources make incompatible claims or interpretations. | Present the competing evidence and uncertainty; do not force a single conclusion. |
| `unverified_translation` | Translation or interpretation has not met the evidence/review bar. | Do not state as definitive meaning. |

“Corroborated” means that the cited sources agree on the stated claim; it does not guarantee that the claim is true. A source’s publication date, authority, proximity to the underlying event/text, edition, and dependence on other sources should inform how the answer is framed.

## 9. Quality attributes and guardrails

- **Traceability:** every factual claim in a generated answer has at least one inspectable supporting source excerpt; web claims show their validation state.
- **Fail-closed behavior:** malformed model output, missing quotes, unavailable evidence, or unresolved contradictions cannot promote a web claim into trusted memory.
- **Privacy:** user documents are processed locally where possible; any external processing is disclosed before or at the point of use. No source content is uploaded to an unconfigured service.
- **Safety:** web acquisition is bounded to public sources and must not access local/private network resources or bypass access controls.
- **Data integrity:** ingestion and refresh are repeatable without silently multiplying duplicate source records; deleting an archive item removes its associated searchable entries and snapshots according to the user’s chosen retention action.
- **Usability:** failures provide a plain-language explanation and a next step; “no document to index” is not an acceptable user-facing diagnosis by itself.
- **Performance:** research limits and progress are visible enough that the user can tell whether the app is searching, validating, or complete. Exact latency targets will be set after the first usability pass.

## 10. Acceptance criteria for PRD-level release

1. Given a text-based PDF, the user can ingest it and receive an answer that identifies the document and relevant page/excerpt.
2. Given an image-only PDF, the app does not claim it was indexed; it explains the missing text and offers the documented OCR or replacement route.
3. Given a public article and a relevant PDF, topic research retains provenance for both and distinguishes their source types.
4. Given two independent sources with verified supporting quotes, a claim may be marked corroborated and answerable with both citations.
5. Given a source that repeats another source’s claim, the system does not automatically count it as independent corroboration.
6. Given credible contradictory quotes, both sides remain inspectable and the ordinary answer clearly reports the disagreement.
7. Given a PDF that directly addresses the contradiction, the system explains the documentary basis for any resolution and still shows the relevant provenance.
8. Given an answer already covered by eligible memory, the system answers from memory without web access unless the user explicitly refreshes.
9. Given a memory miss with research-on-miss enabled, the system performs one bounded research pass and retries; if evidence remains insufficient, it says so.
10. Given a Sanskrit/Hindi passage with competing interpretations, the original text and each attributed translation/interpretation remain distinct; no unsupported model gloss is shown as definitive.
11. Given an answer, a reviewer can navigate from each cited statement to its evidence excerpt and source record.
12. Given external model/search configuration, the UI makes clear which content may leave the local machine.
13. Given the supplied visual references, the main app surfaces share the approved warm cream/orange/lavender palette and outlined retro components, while long excerpts, citations, forms, and error states remain legible and accessible.

## 11. Success measures

Release quality gates:

- **100% evidence coverage** in the acceptance set: each factual answer statement maps to at least one shown excerpt.
- **0 conflicted or single-source web claims** in the standard trusted retrieval set.
- **100% truthful ingestion reporting** in the acceptance set: documents with no extracted text are never counted as successfully indexed.
- **100% provenance retention** for accepted web claims in the acceptance set, including source URL and evidence quote.
- For a curated multilingual evaluation set, **0 cases** where a model-generated gloss is labeled as an authoritative published translation.

After release, track user-visible research completion, answer abstention rate, citation usefulness feedback, duplicate-source rate, OCR success/failure, and refresh frequency. These metrics should diagnose usability and data quality; optimizing answer volume must not weaken evidence requirements.

## 12. Risks and mitigations

| Risk | Product mitigation |
| --- | --- |
| Search results repeat copied material and create false confidence. | Track origin and source relationships; distinguish host diversity from independent evidence. |
| A quote is accurate but the source is wrong or misleading. | State that quote verification proves attribution, not truth; expose authority, date, and source type. |
| PDFs are scanned, corrupt, or have poor OCR. | Detect extraction failure, offer local OCR, preserve originals, and show quality caveats. |
| Historical texts have variant editions or interpretive translations. | Preserve edition/context, show alternatives, separate textual evidence from interpretation, and allow unresolved outcomes. |
| A web page changes or disappears after research. | Keep an auditable local snapshot with capture time and disclose snapshot freshness. |
| External model providers receive sensitive excerpts. | Clear disclosure, user-controlled provider configuration, and no silent transmission. |
| A polished answer overstates weak evidence. | Answer-state labels, claim-level citations, conservative abstention, and regression acceptance criteria. |

## 13. Assumptions and decisions needed for approval

This draft assumes:

- The first target is one person using a local desktop/web app, not a hosted multi-user product.
- Local PDFs and bounded public-web research are both first-release capabilities.
- Memory-first retrieval is the default; research-on-miss remains user-controlled.
- OCR should be local and user-visible; whether it is P0 or P1 is an approval decision.
- External LLM/search services may be used, but privacy disclosure and provenance are required.
- This product should support historical-text research carefully, while refusing to claim expert-grade translation without suitable sources or review.

Please approve or revise these scope decisions before technical design begins:

1. Is the single-user, local-first product the right target, or should the PRD include a team/cloud product?
2. Should local OCR for scanned PDFs be required in the first release, or can the first release only detect and explain the limitation?
3. Should research-on-miss remain opt-in, or should it be the default?
4. Is classical-language interpretation a first-release requirement, or should the first release only preserve original text and cite published translations?
5. Are portable answer/evidence exports required in the first release?

## 14. Approval gate and next step

This PRD is ready for product review, not implementation sign-off. Once the product owner approves or amends the scope, the next step is to revise the TRD, architecture diagram, and implementation plan to map each approved requirement to a design, data flow, and verification approach. No TRD rewrite is included in this draft.
