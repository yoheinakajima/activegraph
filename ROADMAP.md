# Active Graph v1.1 roadmap

`ROADMAP.md` is the current scoping document for proposed v1.1 work.
`v1.1-plan.md` remains the original consolidated backlog from the
post-v1.0.3 review; use it for provenance and older priority notes, not
as the live roadmap. `FUTURE_IDEAS.md` holds valid candidates that do
not block v1.1 unless the maintainer later promotes them here.

This roadmap is organized as mergeable slices. Each item is marked:

- **MUST** — required for the scoped v1.1 cleanup path.
- **SHOULD** — intended for v1.1 if it stays small and does not block a
  release-quality MUST item.
- **DEFERRED** — recognized during v1.1 scoping, but intentionally not a
  v1.1 blocker in this pass.

Status for the v1.1 readiness branch: Phases 0-7 are shipped or
intentionally deferred as marked below.

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
