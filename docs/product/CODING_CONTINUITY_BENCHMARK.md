# Coding Continuity Benchmark — Design (v1)

**Status:** Design draft. Implementation deferred to a fresh session.
**Authored:** 2026-04-26 / 27. Grounded in 14 sampled real devsession files from the dogfood corpus (`/Users/will/coding-projects/RecCli/devsession/`).
**Motivation:** The 2026-04-26 LongMemEval A/B revealed that RecCli's coding-purposed summary schema correctly discards personal-fact incidentals as out-of-scope — making the −6pp loss the schema *doing its job*, not a bug. RecCli's actual product domain (long coding sessions where users ask "what did we decide about X 3 weeks ago?") has zero benchmark coverage. This document specifies one.

---

## 1. Design principles

Four principles, each load-bearing for the spec below:

1. **Data-first taxonomy.** The 8 question clusters in §3 were derived by reading 14 sampled real devsession files (sizes 9 KB → 2.6 MB, dates spread across 2026-03-23 → 2026-04-26), not invented from intuition. We're explicitly avoiding the LongMemEval failure mode: writing a plausible-sounding spec, then discovering it tested something subtly different from what users actually need.

2. **Test the `.devproject` layer.** Two of three RecCli layers (raw conversation + summary) got tested by LongMemEval. The project layer — cross-session feature continuity, file ownership, proposals — has zero benchmark coverage. **That layer is arguably what differentiates RecCli most from chat-memory competitors and we have no numbers on it.** v1 must include explicit project-layer questions.

3. **Latency is a first-class metric.** The LongMemEval Leg A vs Leg B comparison showed summaries added 8.6× wall time. Even if a future test shows summaries help accuracy, we'd need to weigh "+X pp accuracy at 8.6× latency" — not just "+X pp." This benchmark reports latency per question and per cluster alongside accuracy.

4. **Investigation > measurement at the margin.** Today's session demonstrated empirically: $11 of broad measurement produced an ambiguous result; $0.45 of focused diagnostic produced a roadmap-resetting finding. v1 should be small enough (50–100 questions) to favor focused iteration over throughput. We're not chasing leaderboard size.

---

## 2. Problem statement

Coding-session memory has a different shape than chat memory:

- **Chat memory** (LongMemEval): "what time do I wake up on Tuesdays?" Personal facts mentioned in passing, recall of incidentals, recall over short sessions.
- **Coding memory** (RecCli's actual domain): "did our last fix work?" / "what files did we change for the agent harness?" / "what was the rationale for separating consolidate_audit from MMC?" Decisions, bug fixes, file scope, cross-session continuity.

Existing benchmarks — LongMemEval, LoCoMo, MemoryBank — all test chat memory. None tests coding-session continuity. **This is a measurement gap, not a product gap.** RecCli's schema is engineered for the second category; benchmarks against the first will always undersell.

Building a coding-continuity benchmark is research-publishable in its own right ("we measured memory recall in coding workflows; here's the dataset and the methodology") and gives RecCli a benchmark where the schema design is *measured to help*, not just architecturally argued to help.

---

## 3. Question taxonomy (8 clusters, evidenced)

Derived from prompts and decisions observed across 14 sampled sessions. Each cluster maps to a specific RecCli layer it should exercise.

### Cluster A — Decision recall
**Pattern:** "What did we decide about X?" / "Why did we go with Y?" / "What was the rationale?"
**Real example from corpus:** Session `04262026_1909` ended with 14 decisions including "consolidate_audit and MMC stay structurally separate — surface similarity is misleading; cost models, lifecycles, and failure budgets diverge." A future question would ask: *"Why didn't we extract a shared judge/ package between consolidate_audit and MMC?"* Gold: surface the rationale.
**RecCli layer that should help:** `summary.decisions[].title` + `summary.decisions[].detail`.
**Why it matters:** This is the canonical "rationale recall" pattern. It's why the decisions category exists in the schema at all.

### Cluster B — Bug-fix recall
**Pattern:** "What was the bug we fixed?" / "What's the root cause of X?" / "Did our fix work?"
**Real example from corpus:** Session `04052026_1219` opened with "did our fix from last session work?" — referencing the 0-based vs 1-based msg ID bug from the prior session.
**RecCli layer that should help:** `summary.problems_solved[]` + cross-session linking via `session_index`.
**Why it matters:** The most common in-session question type observed. Sessions explicitly chain off prior debug work.

### Cluster C — Open-issue carry-forward
**Pattern:** "What's still open from session X?" / "What did we leave undone?" / "What's blocked on Y?"
**Real example from corpus:** Today's own session has 5 open issues that need to carry to next session. Session `04252026_2235` opened with "I'll run audit_feature again" referencing prior open work.
**RecCli layer that should help:** `summary.open_issues[]` + temporal traversal (most-recent open issues for a given topic).
**Why it matters:** This is the load-bearing case for *why* sessions are linked at all. Without good open-issue recall, every session re-discovers what's pending.

### Cluster D — File-scope recall
**Pattern:** "What files did we change for feature X?" / "What's the scope of the auth work?"
**Real example from corpus:** Session `04262026_1909` touched ~30 files across the agent harness shipment. A future question: *"Which files did we modify when building propose_patch?"* Gold: 5–6 specific paths.
**RecCli layer that should help:** `summary.code_changes[].files` + `.devproject` `feature.file_boundaries`.
**Why it matters:** File-scope is how engineering work is organized. Wrong-file recall is a real product failure (you'd be looking in the wrong place).

### Cluster E — Cross-session continuity
**Pattern:** "How did we get to the current state of X?" / "Walk me through how the MMC design evolved." / "What was the last work on the agent harness?"
**Real example from corpus:** "Continue from where we left off" — appears as variant prompts in many sessions. The 04052026 cluster of 8 sessions is essentially one long arc of MMC development across days.
**RecCli layer that should help:** `session_index` + chronological traversal across sessions linked to the same `feature_id`.
**Why it matters:** This is the *signature* RecCli capability — the "show me how we got here" story we've been talking about as the moat. If summaries are right but session-traversal is broken, we lose this.

### Cluster F — External-reference recall
**Pattern:** "What was that thing the codex strategic essay critiqued?" / "What did Grok say about polish point 1?" / "What was in the regwatch session feedback?"
**Real example from corpus:** Session `04252026_2301` opens with "here was some insight on reccli from the regwatch session" pasting external text. Today's session opened with "have we reasoned through this yet?" pasting prior reasoning. Sessions reference *named external artifacts* and need to surface them.
**RecCli layer that should help:** Raw conversation full-text search (proper-noun matching, paste recall).
**Why it matters:** Tests the bottom layer. If raw-message search is broken, RecCli is just lossy summary. The summary layer can't replace this.

### Cluster G — Project-layer queries
**Pattern:** "Which sessions worked on feat_X?" / "What's the recent activity on the agent harness?" / "How many features are in-progress?"
**Real example from corpus:** Implicit in many "what did we ship recently?" questions. The .devproject feature map has session_index + feature linkage — these queries can only be answered by traversing the structured project layer, not by raw retrieval.
**RecCli layer that should help:** `.devproject` `feature.session_index` + `feature_id` reverse lookup.
**Why it matters:** **This is the layer with no prior benchmark coverage.** If RecCli is uniquely good at cross-session feature continuity, here's where we prove it.

### Cluster H — Duplicate / been-here-before detection
**Pattern:** "Have we already discussed this?" / "Did we previously decide on X?" / "Have we tried this approach before?"
**Real example from corpus:** Today's session opens with "have we reasoned through this yet? i thought we did." The pattern is: user has fuzzy recollection, wants to verify before re-doing work.
**RecCli layer that should help:** Cross-layer (summary semantic search + raw verification + similarity threshold).
**Why it matters:** Highest-stakes pattern — false negatives cause duplicate work; false positives cause stale recall. Easy to under-detect when summaries are vague (today's failure mode!).

### Coverage summary

| Cluster | Tests layer | Estimated v1 question count |
|---|---|---|
| A — Decision recall | summary | 8 |
| B — Bug-fix recall | summary + cross-session | 8 |
| C — Open-issue carry-forward | summary + temporal | 6 |
| D — File-scope recall | summary + .devproject | 8 |
| E — Cross-session continuity | session_index + traversal | 6 |
| F — External-reference recall | raw conversation | 6 |
| G — Project-layer queries | .devproject (untested!) | 8 |
| H — Duplicate detection | cross-layer | 6 |
| **Total** | | **56 questions** |

The 8-question allocation for D and G prioritizes the project-layer-touching clusters since those are the unmeasured differentiators.

---

## 4. Dataset sourcing

### v1: dogfood corpus
**Source:** Will's `~/coding-projects/RecCli/devsession/` — 61 sessions, ~1 month (2026-03-23 → 2026-04-26), spanning a continuous arc of RecCli's own development.

**Why dogfood for v1:**
- Already exists, no sourcing work
- Authored by someone who can hand-label gold answers (Will)
- Long enough arc (1 month) to test cross-session continuity
- Diverse session sizes (9 KB to 2.6 MB) — small fixes to long shipping sessions
- We *know* what should be recallable

**Anonymization:** Probably none for v1 (dogfood-only, not published). If/when public release is considered: redact API keys via existing redaction pipeline (already implemented), consider scrubbing references to specific people/companies (`Grok`, `regwatch`), keep technical content intact.

**Privacy gate before public release:** Will reads the full sampled question set + gold answers and approves each. Only then does the dataset go public. Default-private until then.

### v2 (future): broader corpus
- Public Claude Code transcripts (where authors opt in)
- PR conversation threads (GitHub API, public repos)
- Other RecCli users' sessions (with consent + anonymization)

Out of scope for v1.

---

## 5. Gold-labeling methodology

**Hybrid: 30 auto-derived + 26 hand-labeled.**

### Auto-derived (~30 questions, ~1 hour to generate)

Templates that consume the existing summary structure:

| Source schema field | Question template | Gold |
|---|---|---|
| `decisions[].title` | "Why did we decide [title]?" | `decisions[].detail` |
| `decisions[].title` | "What was the decision about [topic extracted from title]?" | `decisions[].title` + `detail` |
| `problems_solved[].title` | "What was the bug we fixed in session X?" | `problems_solved[].detail` |
| `open_issues[].title` | "What's still open about [topic]?" | `open_issues[].title` + carry-forward chain |
| `code_changes[].files` | "What files did we modify for [feature inferred from session]?" | the `files` list |

Pros: scalable, deterministic, message_range pointers come for free from the schema.
Cons: circular if not careful — must ensure question doesn't trivially regurgitate the field being queried. Mitigation: question templates ask for `detail` when given `title`, never the reverse.

### Hand-labeled (~26 questions, ~3 hours to write)

The hard cases auto-derivation can't generate:
- Cluster E (cross-session continuity): require knowing the arc, not just one session
- Cluster G (project-layer queries): require knowing the feature graph, not just one summary
- Cluster H (duplicate detection): require knowing what *almost* was discussed
- Cluster F (external-reference recall): require remembering specific named external artifacts

**Each hand-labeled question must include:**
- Question text
- Gold answer (free text)
- `message_range` pointer(s) — which messages in which session contain the evidence
- Cluster tag (A-H)
- Layer tag (raw / summary / project / cross)
- Difficulty (1-3)

### Ground-truth message_range pointers

For *every* question (auto and hand): the gold answer must cite the source messages by `(session_id, msg_id_range)`. This enables a second metric beyond answer accuracy: did the system actually retrieve the right evidence?

---

## 6. Scoring rubric

### Per-question metrics (recorded for every system × every question)

| Metric | Definition |
|---|---|
| **Answer accuracy** | Binary pass/fail. gpt-4o judge with LongMemEval-style prompt template. Calibrated by 10% human-labeled spot-check. |
| **Retrieval precision** | Did the cited message_range actually contain the answer? Compare system's drill-down to gold pointer. |
| **Tool calls** | Number of `search_history` / `expand_search_result` / etc. invocations. Lower is better at equal accuracy. |
| **Turns** | Agent loop iterations. |
| **Wall time** | End-to-end seconds. Captures latency. |
| **Token cost** | Approximate dollars (input + output tokens × per-million rate). |

### Aggregates

- **Per cluster:** mean accuracy, mean latency, mean tool calls
- **Per layer:** mean accuracy on questions tagged for that layer
- **Headline number:** weighted average across clusters (weights reflect product priority — Cluster D and G get higher weight because they're the differentiators)

### Latency tiers (for the latency metric specifically)
- **Sub-second** (< 1 s): production-deployable in a Claude Code session without UX cost
- **Few seconds** (1-5 s): acceptable for explicit "ask memory" queries
- **Slow** (5-30 s): only acceptable for batch/offline use
- **Very slow** (> 30 s): not deployable, even if accurate

A system that scores 90% accurate but lives in the "very slow" tier is not actually a product — it's a research artifact. Report which tier each system lives in.

---

## 7. Comparable baselines

Run the same 56 questions through 7 systems:

| # | System | What it tests |
|---|---|---|
| 1 | **BM25 on raw conversation** (no summary, no project) | Lower bound — keyword search only |
| 2 | **Dense retrieval on raw** (text-embedding-3-small, top-10 previews to QA prompt) | LongMemEval static-runner equivalent |
| 3 | **Hand-written NOTES.md + RAG** (one big file with manually-written project notes, RAG'd) | The "what users do without RecCli" baseline |
| 4 | **Full-context-stuffed** (all 61 sessions concatenated → Claude long-context) | Upper bound — expensive but accuracy ceiling |
| 5 | **RecCli, summary off** (agentic loop, raw retrieval only) | The Leg B equivalent. Tells us what summaries add |
| 6 | **RecCli, summary on** (agentic loop, full tri-layer) | The product as shipped |
| 7 | **RecCli with .devproject explicit access** (agentic loop, plus a `query_project_layer` MCP tool exposed to the agent) | Tests the unmeasured layer specifically |

Systems 5 vs 6 = pure summary-layer effect (the Leg A vs Leg B equivalent, but on the right benchmark).
Systems 6 vs 7 = pure project-layer contribution.
Systems 1 vs 2 = retrieval-method effect at fixed corpus.
System 4 = ceiling. If our headline is much below it, we have headroom; if close, we're approaching the ceiling.

---

## 8. Cost + timeline (for v1 implementation, future session)

| Phase | Effort | Cost |
|---|---|---|
| Question authoring (30 auto + 26 hand) | 4 hours | $0 |
| Build runner that exercises 7 systems | 1 day | $0 (code) |
| Run 7 systems × 56 questions | 4 hours wall time | ~$15-30 (mostly Claude/GPT for systems 4 + 6 + 7) |
| gpt-4o judge | 30 min | ~$3 |
| 10% human spot-check + write-up | 2 hours | $0 |
| **Total** | **~3 days** | **~$30-50** |

Within the same budget envelope as the 50q LongMemEval A/B we just ran, but produces a benchmark *that tests what RecCli actually does* and *covers the previously-unmeasured project layer*.

---

## 9. Risks + decision points

### Risks

1. **Self-evaluation bias.** Will writes the questions AND the answers AND the system being measured. gpt-4o judge mitigates *some* of this (judge isn't Will), but the question set itself reflects Will's mental model of what should be recallable. Mitigation: have at least one outside reviewer (me, codex, or a second human) read the question set and flag obvious bias before running.

2. **Schema-circularity in auto-derived questions.** "What was the decision about X?" with answer drawn from `decisions[].title` is meaningless if the system just searches for "decision" in summaries. Mitigation: question templates always ask for `detail`/`rationale` given `title`, never the reverse. Spot-check 10% of auto-derived questions to verify they require real recall.

3. **Cross-session multi-step questions are hard to gold-label.** Cluster E ("walk me through how MMC evolved") needs 4-5 message_range citations across multiple sessions. Hand-labeling these takes 10+ minutes each. Mitigation: cap Cluster E at 6 questions in v1; expand in v2 if mechanism is sound.

4. **Latency measurements vary by network conditions.** OpenAI/Anthropic API latency fluctuates. Mitigation: run each system 3 times, report median. Or run all systems back-to-back in a single batch to share network conditions.

5. **The dogfood corpus is too narrow.** RecCli development sessions are atypically self-aware about memory. Real-world coding sessions might have different recall patterns. Mitigation: acknowledge as v1 limitation; v2 broadens corpus.

### Decision points (need answers before implementation)

| # | Decision | Default |
|---|---|---|
| 1 | Anonymize the dogfood corpus for the question set? | No for v1 (dogfood-only, not published) |
| 2 | Include the `query_project_layer` MCP tool baseline (System 7)? | Yes — that's the whole point of measuring the unmeasured layer |
| 3 | Run on Anthropic, OpenAI, or both? | OpenAI for v1 (Anthropic credits depleted). Re-run on Anthropic when restored. |
| 4 | Publish results publicly? | Defer until after v1 is run and we know what the numbers say |
| 5 | Use the existing `benchmarks/longmemeval/run_agentic_benchmark.py` dispatcher pattern as the runner skeleton? | Yes — already provider-agnostic, just needs a different question loader and a `query_project_layer` tool |

---

## 10. Go / no-go for implementation

Greenlight v1 implementation if:

- [ ] Will agrees the dogfood corpus (61 sessions) is the right substrate for v1
- [ ] The 8-cluster taxonomy resonates as covering what users actually need from coding-session memory
- [ ] No-anonymization-for-v1 is acceptable (private benchmark first, publish later if at all)
- [ ] Self-evaluation bias mitigations (outside reviewer, schema-circularity guards) are sufficient

Block on v1 if:

- A non-dogfood corpus is required (much more sourcing work)
- Anonymization must happen before v1 runs (delays by ~1 day)
- Third-party hand-labeling required (delays by weeks)

---

## 11. Why this is worth building

Today's session demonstrated the cost of not having this: a benchmark designed for the wrong domain produced a misleading negative signal that almost reset our roadmap toward the wrong work. With this benchmark in place:

- **The summary schema gets validated empirically**, not just architecturally.
- **The .devproject layer gets numbers** for the first time. If it's the differentiator we believe it is, this proves it.
- **Latency becomes a first-class product constraint**, captured per-question.
- **Future product decisions** ("should we add semantic spans to summaries?", "should we boost summary items in retrieval?", "should we ship MMC default-on?") can be A/B'd against a benchmark that *should* respond to those changes — instead of one that systematically discards what we change.
- **The codex strategic essay's Tier 1-4 roadmap can be re-prioritized** against measured impact, not architectural argument.

The honest version of the marketing story we discussed today gets even stronger with a benchmark that's purpose-fit:

> "We're competitive on chat-memory benchmarks (LongMemEval: 67% agentic) despite not being designed for that domain. On coding-continuity benchmarks (CCB v1: TBD%), we're [whatever the actual number is] because that's what the schema is built for."

That's a story you can defend, refine, and ship behind. Today's story still requires a benchmark to exist.
