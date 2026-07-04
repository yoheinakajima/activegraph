# Active Graph v1.3 roadmap

`ROADMAP.md` is the current scoping document for proposed v1.3 work.
The closed v1.1 roadmap is archived at the bottom of this file for
provenance; `v1.1-plan.md` remains the original post-v1.0.3 backlog.
`FUTURE_IDEAS.md` holds valid candidates that do not block v1.3
unless the maintainer promotes them here.

This roadmap was written during v1.2.0 release preparation
(2026-07-03). v1.2 itself was not roadmapped in advance: the
GraphStore/FalkorDB arc arrived as an inbound contribution and was
locked retroactively in CONTRACT v1.2 #1–#6 (see the provenance note
there). This document restores the plan-first cadence for the next
cycle and reconciles every `FUTURE_IDEAS.md` deferral whose stated
rationale has expired.

Each item is marked:

- **MUST** — required for the scoped v1.3 path.
- **SHOULD** — intended for v1.3 if it stays small and does not block
  a release-quality MUST item.
- **DEFERRED** — recognized during v1.3 scoping, but intentionally not
  a v1.3 blocker in this pass.

## Phase 0 — v1.2.0 release follow-through (maintainer-owned operations)

Items that close out v1.2.0. None are code; all need the maintainer.

- **MUST: Tag and publish v1.2.0.** Per `docs/about/publishing.md`
  and CONTRACT v1.0 #C8, PyPI publishing is externally owned: merge
  the release branch, push the `v1.2.0` tag, let `publish.yml` run.
  Answers issue #47.
- **MUST: Flip the `tests` workflow to a required status check.**
  CONTRACT v1.2 #6 shipped the gate; a gate that isn't required is
  advisory. The same flip is still pending for wheel-completeness
  (v1.1 #8) and deploy-verification (v1.1 #9) — do all three in one
  settings pass.
- **MUST: Fix the license line on activegraph.ai.** The website says
  MIT; the repository locked Apache 2.0 in CONTRACT v1.0.5.post1 #1.
  The site is the stale artifact (issue #36). Repo-side, the LICENSE
  file, NOTICE, and `test_license.py` gate are already consistent.

## Phase 1 — Quality ratchets (CONTRACT v1.1 #3 / #4, promoted from FUTURE)

**Status: shipped (PR #50).** Type burndown closed 41/41 (100%);
docstring Wave 2 closed Ring 0 at 106/106 full. Ring 1 burndown stays
DEFERRED as marked below.

Both deferrals said "after the drift-gate foundation is in place."
The foundation shipped in v1.1.0; the deferral rationale has expired.

- **MUST: Docstring Wave 1.** Close the 6 remaining Ring 0 exemptions
  in `docstring_gaps.toml` (`Budget`, `InMemoryEventStore`, `Runtime`,
  `View`, `clear_tool_registry`, `get_tool_registry`) — these are the
  most-imported symbols in the framework and the exemption list was
  designed to reach empty. End state: zero `[[exemptions]]` entries.
- **SHOULD: Type-completeness burndown.** Close the 16 dirty modules
  in `docs/reference/api/TYPE_REPORT.md` using
  `converge_clean_set()` in `scripts/audit_types.py`, module by
  module, ratcheting `pyproject.toml`'s strict list forward. Mergeable
  in small batches; each batch is independently shippable.
- **SHOULD: Docstring Wave 2.** Upgrade Ring 0 one-liners to full
  docstrings per `COVERAGE_REPORT.md` (57/100 fully-documented at
  baseline). Follows Wave 1; same audit tooling.
- **DEFERRED: Ring 1 burndown past the 80% threshold gate.** Quality
  bar, not a blocker; revisit after Waves 1–2.

## Phase 2 — Native structured-output mode (promoted from FUTURE)

**Status: design locked (CONTRACT v1.3 #1); implementation in the same
PR series.** Both MUST questions below are answered there: the
provider matrix (Anthropic ``output_config`` / OpenAI
``response_format``, capability-gated with prompt-path fallback,
silent-but-audited) and replay/fixture semantics (mode is part of
prompt identity; omit-when-absent hash field; a mode flip is a true
divergence via the existing ``ReplayDivergenceError``).

The deferral said "after provider tool-shape parity lands." Parity
shipped in v1.1.0 (OpenAI tool translation, parity tests); the
rationale has expired. Same design-first discipline as v1.1's
`fork --set`:

- **MUST before implementation: lock the provider matrix.** Decide
  which native modes are in scope (Anthropic structured outputs,
  OpenAI `response_format` JSON schema) and what happens on providers
  without one (fall back to the current prompt-embedded schema path,
  loudly or silently).
- **MUST before implementation: lock replay/fixture semantics.**
  Native-mode responses must round-trip through recorded fixtures and
  strict replay identically to prompt-mode responses, or the
  divergence must be a documented reason code.
- **SHOULD: Implement behind `@llm_behavior`, not a new decorator.**
  The schema surface stays `output_schema=`; native mode is a
  provider capability, not a user-facing mode switch, unless the
  design pass concludes otherwise.
- **DEFERRED: Dict-form `output_schema`.** Stays in
  `FUTURE_IDEAS.md`; the schema surface stays narrow until native
  mode settles.

## Phase 3 — Graph-store follow-ons (named in CONTRACT v1.2 #4)

- **SHOULD, design-first: `where` push-down.** Named follow-on work
  from the v1.2 arc: evaluating `where` predicates inside the
  database requires flattening (or dual-writing) the JSON `data`
  payload into indexable properties. The design pass must answer:
  which subset of the predicate language translates faithfully, what
  the conformance suite asserts, and whether flattening is opt-in
  per-type. No implementation before those answers — this is the
  v1.2 #4 split rule (only trivially-mirrorable filters push down)
  being deliberately revisited, so it needs its own amendment.
- **DEFERRED: Additional graph backends (Neo4j, Postgres-graph).**
  CONTRACT v1.2 #5 already defines the extension contract
  (`GraphStoreConformance`); new backends are contribution-shaped,
  not roadmap-shaped. If one arrives inbound, follow the v1.2
  merge-then-lock pattern.
- **DEFERRED: Fork cache pre-population symmetry.** Unchanged from
  the v1.1 deferral; no new evidence it blocks anyone.

## Phase 4 — Community surface (promoted from FUTURE; maintainer decisions)

**Status: decided (CONTRACT v1.3 #2).** Conduct reporting is public
by design — an X post mentioning ``@yoheinakajima`` (preferred) or a
public issue; CODE_OF_CONDUCT.md ships with the stock privacy clause
explicitly amended to match. Issues-first relaxes for
no-behavior-change fixes, and a maintainer-curated trusted-contributor
list starts with @dudizimber. CLA/DCO stays deferred.

The deferral said "after observing actual v1.0.x contribution
patterns." The patterns arrived: an external contributor shipped the
v1.2 arc end-to-end (issues #38/#41/#43/#45 → PRs #39/#46), and
adopters are filing product-grade issues (#36). The rationale has
expired; the decisions are the maintainer's.

- **SHOULD: Decide the contact channel, then ship
  `CODE_OF_CONDUCT.md`.** The v1.1 ordering rule stands: the
  reporting document does not ship before a real, staffed contact
  channel is chosen.
- **SHOULD: Revisit the issues-first contribution policy.** The v1.2
  arc followed issue-first discipline and produced excellent results
  — the policy demonstrably works for large contributions. The open
  question is whether trivial-fix PRs (typos, doc nits) still need an
  issue. Decide and update `CONTRIBUTING.md` either way.
- **DEFERRED: CLA / DCO.** Apache 2.0's implicit grant remains
  sufficient at current volume.

## Phase 5 — Observability follow-on

- **DEFERRED, design-first: OTel trace export.** Issue #23's open
  question 3, explicitly scoped out when `OpenTelemetryMetrics`
  shipped in v1.1.0: map the event log to OTel spans (one span per
  behavior invocation, `behavior.failed` as a span event,
  `llm.requested`/`llm.responded` as nested spans). Materially bigger
  than a metrics backend — it is a tracing surface. Promote to SHOULD
  only with a design doc that answers span-identity and
  replay-determinism questions.

## FUTURE_IDEAS reconciliation

Every `FUTURE_IDEAS.md` item was re-reviewed during this scoping
pass:

| Candidate | Disposition |
| --- | --- |
| Native structured-output mode | Promoted — Phase 2 (rationale expired: tool parity shipped) |
| Type-completeness ratchet | Promoted — Phase 1 SHOULD (rationale expired: gates in place) |
| Docstring-completeness ratchet | Promoted — Phase 1 MUST (Wave 1) / SHOULD (Wave 2) |
| CODE_OF_CONDUCT + contact channel | Promoted — Phase 4 SHOULD (rationale expired: patterns observed) |
| Contribution-policy relaxation | Promoted — Phase 4 SHOULD (same) |
| Fork cache pre-population symmetry | Stays FUTURE (restated Phase 3 DEFERRED) |
| Dict-form `output_schema` | Stays FUTURE (restated Phase 2 DEFERRED) |
| `on_failure` callback | Stays FUTURE — still needs its own failure-model design pass |
| Fire-once aggregation triggers | Stays FUTURE — runtime behavior, no current demand signal |
| Full Pack* error migration sweep | Stays FUTURE — larger than any v1.3 slice |
| DB error wrappers | Stays FUTURE — still waiting on real driver-failure experience |
| `Runtime.load` auto-provider ergonomics | Stays FUTURE — migration implications unchanged |
| Content negotiation for docs host | Stays FUTURE — infrastructure beyond GitHub Pages |
| Editorial doc-readability pass | Stays FUTURE — open-ended, unbounded |
| CLA / DCO decision | Stays FUTURE (restated Phase 4 DEFERRED) |

New FUTURE entries surfaced by this pass: `where` push-down design
(Phase 3 tracks it as design-first SHOULD, so it lives here, not in
FUTURE_IDEAS), OTel trace export (Phase 5 DEFERRED).

---

# Archived: v1.1 roadmap (closed with the v1.1.0 release, 2026-06-10)

Preserved for provenance. Every phase below shipped or was
intentionally deferred as marked; the closure record is
`CHANGELOG.md` § v1.1.0 and CONTRACT Phase 5–7 outcomes.

## Phase 0 — Contract/planning split and backlog reconciliation

**Status: shipped.** `CONTRACT-INDEX.md`, the `CONTRACT.md` pointer,
the `v1.1-plan.md` banner, and this roadmap/FUTURE split are in place.

- **MUST: Add the planning document map.** Introduce
  `CONTRACT-INDEX.md` so readers can distinguish locked contract
  decisions, shipped-change notes, backlog sources, current scope, and
  future ideas.
- **MUST: Banner the old backlog.** Mark `v1.1-plan.md` as the
  post-v1.0.3 consolidated backlog and point current scope to this file.
- **MUST: Add a CONTRACT pointer without moving history.** Point the top
  of `CONTRACT.md` to `CONTRACT-INDEX.md` and state that v1.1
  implementation scoping lived here, not inline inside CONTRACT.
- **MUST: Reconcile post-v1.0.3 candidates.** Every v1.1 candidate named
  in `CHANGELOG.md` Unreleased is classified either in this roadmap or
  in `FUTURE_IDEAS.md`.

## Phase 1 — Contract modularization path

**Status: shipped/deferred.** The chosen v1.1 shape is additive
navigation first (`CONTRACT-INDEX.md` plus banners). Physical movement
of historical CONTRACT sections remains deferred.

- **MUST: Decide the modularization shape before moving sections.** Pick
  the v1.1 path for making the historical contract easier to navigate
  (for example: split-per-milestone, executive-summary preambles, or an
  index-first approach) without rewriting locked prose.
- **SHOULD: Land the smallest navigation improvement first.** Prefer
  additive indexes, banners, or generated tables of contents before any
  physical movement of historical sections.
- **DEFERRED: Physically move old CONTRACT milestone sections in Phase
  0.** Movement can happen only after the modularization rule is chosen;
  this first reconciliation PR deliberately does not move them.

## Phase 2 — Drift gates

**Status: shipped.** `tests/test_cli_docs_flags.py`,
`tests/test_doc_python_snippets.py`, and the tagged-release additions
to `tests/test_version_sync.py` cover this phase.

- **MUST: CLI flag gate.** Add an executable check that compares the
  documented CLI flags against the implemented CLI surface so reference
  docs do not drift from parser behavior.
- **MUST: Executable Python snippet gate.** Add a docs test that runs or
  validates Python snippets that are promised to be copy-pasteable.
- **MUST: Version-tag correspondence gate.** Add a check that keeps
  version-tagged docs, tests, and release notes aligned with the package
  version and release tags.

## Phase 3 — Read-only CLI gaps

**Status: shipped.** `inspect --memo` and `inspect --search` are
implemented, documented, and covered in `tests/test_cli.py`.

- **MUST: `inspect --memo`.** Add the read-only inspect surface for memo
  visibility, including docs and tests.
- **MUST: `inspect --search`.** Add the read-only inspect search surface,
  including docs and tests.
- **SHOULD: Keep these read-only.** Do not mix mutation or replay
  semantics into the inspect-gap slice.

## Phase 4 — `fork --set`

**Status: shipped/deferred.** `fork --set` records
`pack.settings_overridden` events and applies them during pack loading.
Broader fork cache-prepopulation ergonomics stay in `FUTURE_IDEAS.md`.

- **SHOULD: Implement `fork --set` after design questions are answered.**
  The feature belongs in v1.1 if the scope stays bounded and the design
  questions below are resolved first.
- **MUST before implementation: define assignment semantics.** Decide how
  repeated `--set` flags, nested paths, type coercion, and invalid paths
  behave.
- **MUST before implementation: define persistence and replay semantics.**
  Decide which event records the change, how the fork stays auditable,
  and how divergence is reported.
- **MUST before implementation: define approval / policy boundaries.**
  Decide whether `fork --set` bypasses, reuses, or records policy checks.
- **DEFERRED: Any broader fork ergonomics.** Fork cache pre-population
  symmetry remains in `FUTURE_IDEAS.md` unless separately promoted.

## Phase 5 — Small correctness and docs-drift items

**Status: shipped.** Issue #27, `object.patched` docs drift,
reason-code taxonomy, failure-routing convention, and the replay
cross-link are resolved in code/docs/tests.

- **MUST: Resolve #27 LLM transient retry before terminal fallback.**
  Provider-call failures with `reason=llm.network_error` or
  `reason=llm.rate_limited` must get bounded runtime retry attempts
  before the terminal `behavior.failed` path, so provider outages do
  not become indistinguishable from legitimate empty extractions in
  long-running caches.
- **MUST: Decide `object.patched` docs-vs-code drift.** The v1.0.5.post2
  candidate is classified here: either correct the docs to match the
  emitted `patch.applied` event or intentionally add / lock a distinct
  framework event.
- **MUST: Add a reason-code taxonomy reference.** The v1.0.5.post2
  candidate is classified here: enumerate the closed `reason=`
  vocabulary for `behavior.failed` / `tool.responded` in one reference
  location or a clearly named expansion of the failure-model docs.
- **MUST: Lock the failure-routing convention.** The v1.0.4 C-3
  candidate is classified here: document or change the carve-out for
  eval-time pattern failures and `ReplayDivergenceError` so the routing
  model is explicit.
- **SHOULD: Add the replay-divergence cross-link.** The v1.0.4 I-4
  candidate is classified here: cross-link `replay-divergence-error.md`
  to replay / fixture documentation with wording that matches the C-3
  routing decision.

## Phase 6 — OpenAI tool-shape translation

**Status: shipped/deferred.** OpenAI tool definitions and returned
tool calls now round-trip through the shared provider/runtime contract.
Native structured-output mode remains deferred in `FUTURE_IDEAS.md`.

- **MUST: Translate tool definitions for OpenAI.** Add provider-aware
  tool definition shape support so Anthropic-style
  `{name, description, input_schema}` and OpenAI-style
  `{type: "function", function: {name, description, parameters}}` are
  both represented correctly.
- **MUST: Extract OpenAI tool calls.** Add the parallel response parsing
  path for OpenAI tool calls and keep reason-code semantics aligned with
  the existing provider surface.
- **MUST: Add parity tests.** Cover the same tool-using behavior across
  supported providers.
- **DEFERRED: Native structured-output mode.** Keep provider-native JSON
  schema / structured-output behavior in `FUTURE_IDEAS.md` unless it is
  explicitly promoted after tool parity lands.

## Phase 7 — v1.1 release closure

**Status: shipped.** `CHANGELOG.md` has v1.1 release notes and
migration notes, and the final verification gates have passed on the
readiness branch.

- **MUST: Update release notes and migration notes.** Summarize shipped
  v1.1 behavior in `CHANGELOG.md` and document any required migration.
- **MUST: Verify contract and version sync.** Run the doc-link,
  llms.txt, version-sync, and docs-build checks used for the release.
- **MUST: Close or reclassify every roadmap item.** Before release, each
  roadmap item is either shipped, intentionally deferred to
  `FUTURE_IDEAS.md`, or removed with a documented reason.
- **SHOULD: Keep the release closure docs-only unless code shipped in an
  earlier phase requires final notes.** Avoid adding new behavior during
  the closure slice.

## CHANGELOG Unreleased reconciliation

The post-v1.0.3 v1.1 candidates that were named in `CHANGELOG.md`
before v1.1 closure were classified as follows:

| Candidate | Classification |
| --- | --- |
| C-3 failure-routing convention for eval-time pattern failures and `ReplayDivergenceError` | ROADMAP Phase 5 MUST |
| I-4 replay-divergence cross-link | ROADMAP Phase 5 SHOULD |
| Content negotiation on the docs host | FUTURE |
| Editorial doc-readability pass | FUTURE |
| CLA / DCO decision | FUTURE |
| `CODE_OF_CONDUCT.md` plus contact channel | FUTURE |
| Contribution-policy relaxation | FUTURE |
| `object.patched` docs-vs-code drift | ROADMAP Phase 5 MUST |
| Reason-code taxonomy reference | ROADMAP Phase 5 MUST |
