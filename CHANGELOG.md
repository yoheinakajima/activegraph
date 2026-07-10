# Changelog

All notable changes to **activegraph** are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Per-version migration notes reference the
[Migration from v0.7](https://docs.activegraph.ai/cookbook/migration-from-v0-7/)
cookbook, the canonical runbook for upgrading runs and code across
milestones.

The doc site mirrors this file at
[Changelog](https://docs.activegraph.ai/about/changelog/) via the
mkdocs snippet plugin — edit `CHANGELOG.md` at the repo root.

## [Unreleased] — v1.8.0

The outbound-observation hardening release: accepted runtime events now
have a sanctioned, typed, bounded export seam. Additive on 1.7.1; the
existing synchronous `Graph.add_listener` behavior and every default run
remain unchanged when no sink is attached.

### Migration

No breaking changes. Applications that want an event stream attach an
`EventSink` explicitly through `Runtime(..., sinks=...)`,
`Runtime.add_sink`, or `Graph.add_sink`; the default remains no sinks.
Call `flush_sinks` / `close_sinks` at an application's orderly shutdown
boundary when externally buffered delivery must complete.

Embedding consumers should call `Runtime.embed` or `ctx.embed` instead of
invoking `runtime.embedding_provider.embed` directly; the provider object
remains available for compatibility, but direct calls are outside the
record/replay guarantee. Direct `web_fetch.fn(...)` calls now fail closed
unless their `ToolContext` explicitly opts into
`external_io_mode="live_unrecorded"`; normal runtime tool dispatch is
unchanged.

### Added

- **`EventSink`: isolated outbound observation for accepted live events**
  (CONTRACT v1.8 #1–#4). The four-method protocol (`open`, `on_event`,
  `flush`, `close`) receives an isolated event copy plus frozen
  `DeliveryContext(run_id, sequence, mode)`. Offers occur after projection
  and durable append but before legacy listeners, preserving re-entrant
  per-run order without giving adapters veto or mutation authority. Each
  attachment owns a bounded FIFO and daemon worker; hanging, raising, or
  backpressured sinks cannot block the runtime or one another. Queue
  capacity and `drop_newest` / `drop_oldest` / `fail_sink` overflow policy
  are explicit through `SinkConfig`, `SinkStatus`, and `SinkHandle`.
- **Observable sink loss and health.** Four cardinality-rule-compliant standard
  metrics join the locked table: sink queue depth, delivered, dropped, and
  errors. Exact local counters remain queryable through `sink_statuses()`
  even under the default `NoOpMetrics`; bounded `flush_sinks` /
  `close_sinks` report a hanging adapter instead of waiting forever. Timed-out
  closes remain visible and retryable, and terminal close failures retain a
  status snapshot until removed or replaced. Sink metrics are coalesced and
  published by the isolated worker, so even a blocked metrics backend cannot
  enter the sink-offer hot path; the Prometheus and OpenTelemetry adapters now
  serialize concurrent lazy instrument creation.
- **`JSONLEventSink` + reusable conformance suite** (CONTRACT v1.8 #5).
  JSONL is append-only UTF-8, one canonical context/event envelope per
  line, using the store's Decimal/date/set normalization; no rotation.
  `EventSinkConformance` mirrors the EventStore extension-test pattern,
  and thread-safe `RecordingSink` is the downstream test double.
- **Replay guard pinned at public boundaries.** `Runtime.load(...,
  sinks=...)` and `fork(..., sinks=...)` attach sinks only after ordinary or
  snapshot-backed history is projected; strict load completes sink-free
  verification before attachment, and normal replay emits no live deliveries.
  **Replay determinism is untouched because sinks are
  absent from `_replay_event` and strict verification, have no graph or
  scheduling return channel, and observe only a copied event after the
  accepted log/projection state is already fixed.** Historical export is
  deferred behind the reserved `replay_export` delivery marker; OTel spans,
  Langfuse, UI transports, and learning queues remain future adapters.
- **Runtime-owned embedding record/replay** (CONTRACT v1.8 #6).
  `Runtime.embed` and the packs-facing `Context.embed` emit
  `embedding.requested` / `embedding.responded`, hash model + ordered text
  inputs without logging source text, record validated vectors, and replay
  through `EmbeddingCache.from_events` with zero provider contact. Load/fork
  gain `replay_embedding_cache`; strict replay always enables it, rejects
  request-hash drift with `ReplayDivergenceError`, and never falls through to
  a live provider. Direct provider calls remain possible but explicitly
  forfeit replay guarantees; adopting `ctx.embed` in `activegraph-packs` is a
  named downstream follow-up.
- **Fail-closed direct `web_fetch`** (CONTRACT v1.8 #7). The existing runtime
  tool loop remains the recorded/replayable path. Calling the reference tool
  body outside it raises `tool.unrecorded_external_io` before network contact
  unless the caller explicitly selects `live_unrecorded` mode.
- **Recorded cooperative wall-budget truncation** (CONTRACT v1.8 #8).
  `runtime.budget_exhausted` now records the accepted-event/tick/queue stop
  position for `max_seconds`. Strict replay disables monotonic-clock reads and
  stops at that recorded sequence, reproducing the queued suffix instead of
  racing CI speed. Parent-side subprocess trial kills were deferred to the R4
  result authority and are closed by the `TrialExecutor` item below.
- **`TrialExecutor` provider boundary with local default** (CONTRACT v1.8
  #9–#12). `TrialSpecification` canonicalizes pinned trial intent into
  versioned JSON; the runtime-checkable protocol returns `TrialResult` with
  structured status, budget use, artifact references, event-log reference,
  failure details, warnings, and declared isolation guarantees.
  `LocalSubprocessTrialExecutor` delegates to the existing fresh-interpreter
  implementation, and `run_forked_trial` remains the behavior-compatible
  `TrialReport` wrapper. `RecordingTrialExecutor` and
  `TrialExecutorConformance` make future adapters testable without adding a
  Docker/E2B/Modal provider now. The local adapter explicitly declares shared
  filesystem and unconfined network/syscalls: subprocess is crash isolation,
  not a security sandbox.
- **Parent-side trial wall kills are now recorded.** The local executor appends
  `trial.wall_clock_exhausted` to the fork after killing/reaping a timed-out
  child, including the configured limit and accepted-event stop sequence.
  Load/replay observes the killed prefix from the log and never re-races the
  timeout, closing the R5 subprocess half at the R4 result authority.

## [v1.7.1] — 2026-07-09

A macOS trial-child fix, surfaced BECAUSE 1.7.0's import fix let the
child reach resource-limit application. Additive, in
`activegraph.sandbox`; packs pinned `>=1.6,<2.0` (and `>=1.5,<2.0`)
keep working unchanged.

### Migration

No breaking changes. `TrialReport` gains a `warnings: tuple[str, ...]`
field (defaults empty), and `sandbox.preflight()` now returns
`tuple[str, ...]` (degradation warnings; empty = fully clean) instead
of `None` — a caller doing `preflight()` for its raise-or-not
behavior is unaffected; a caller that asserted `is None` should
compare to `()`.

### Fixed

- **The memory budget no longer crashes trial children on macOS**
  (CONTRACT v1.5 #1 addendum 1d, from a macOS/arm64 soak going RED on
  rotation 1). `max_rss_bytes` maps to `RLIMIT_AS`, which the Darwin
  kernel rejects (`ValueError: current limit exceeds maximum limit` —
  Darwin does not support address-space limiting), so every
  limits-carrying trial crashed at `_apply_rlimits`. Resource-limit
  application is now portable: an unsettable cap DEGRADES rather than
  crashes or silently skips — the child records a loud warning that
  surfaces in `TrialReport.warnings` and `.detail` and is logged, and
  the wall-clock kill + event budget remain the active nets. Limit
  application never raises a hard limit (the target is clamped to the
  existing hard limit — that raise was the Darwin crash). **The memory
  budget's guarantee is now stated per-platform: enforced on Linux;
  announced-unavailable on macOS/Windows, where wall-clock + events
  bound the runaway-memory accident class.** The 1.7.0 stderr-capture
  fix is what surfaced the exact `RLIMIT_AS` ValueError in the macOS
  digest — it earned its keep.
- **`preflight()` now exercises the resource-limit path** (the false
  green — the real safety gap). The 1.7.0 preflight sent a null job
  with no limits, so it never reached `_apply_rlimits` and passed on a
  box where every real trial then crashed. Preflight now applies a
  representative limits block, so it catches the `RLIMIT_AS` class at
  the gate: on macOS it PASSES with a memory-net warning (returned and
  logged) rather than pass-then-crash; on a box that cannot start a
  child it still RAISES `SandboxStartupError` with the cause. Returns
  the degradation warnings (empty tuple when fully clean).

## [v1.7.0] — 2026-07-08

The sandbox-hardening release: cross-pack trials become expressible,
and a downstream Replit soak's two trial-child defects are fixed —
both additive, both in `activegraph.sandbox`. Everything additive on
1.6.0; packs pinned `>=1.6,<2.0` (and `>=1.5,<2.0`) keep working
unchanged.

### Migration

No breaking changes. One behavioral refinement in the trial sandbox:
the child's environment allow-list is now `PATH`/`HOME`/`LANG` (plus
explicit `env_passthrough`) and its `PYTHONPATH` is COMPUTED by the
parent from its own `sys.path` rather than forwarded from ambient
env. A trial that relied on an ambient `PYTHONPATH` reaching the
child should name it in `env_passthrough` — but in practice the
computed channel is a superset of what the child could import
before, so trials that worked keep working, and trials that broke on
restricted platforms (Replit/Nix) now start. New `preflight()` lets
consumers verify a child can start at boot.

### Added

- **`extra_packs` on `run_forked_trial`: cross-pack interaction
  trials, opt-in** (CONTRACT v1.5 #1 addendum 1b — the gap the first
  consumer flagged). The default is and stays candidate-only
  isolation: the child loads nothing but the candidate. Each
  `extra_packs` entry is a `PackSource` materialized in the child by
  the identical chain the candidate goes through (bundle hash before
  import, manifest schema, two-way surface check — no trust
  shortcut) and loaded, in order, before the candidate; any extra
  pack failing a pin is `materialization_failed` for the whole
  trial. Scenario resolution stays inside the candidate's root.
  Loading-scope statement + consumer-endorsed patterns
  (pinned-driver scenario, marker-object sweep) added to
  `trial-isolation-design.md` §2b/§2c.
- **`sandbox.preflight()` + explicit code-path channel** (CONTRACT
  v1.5 #1 addendum 1c, from the Replit soak). The trial child's
  `PYTHONPATH` is now computed by the parent from its own resolved
  `sys.path` (`activegraph.__file__`'s root first, then real
  `sys.path` dirs) and passed as an explicit channel — so a child
  can import `activegraph` and its deps on any box where the parent
  could, including platforms (Replit, Nix) whose package discovery
  the env allow-list correctly strips. The allow-list itself stays
  closed (a security control): code locations are not secrets, so
  the computed channel widens nothing it protects. `preflight()`
  spawns a null-job child to verify startup end to end and raises
  `SandboxStartupError` with the cause if a child cannot start.

### Fixed

- **Trial child stderr is no longer discarded** (CONTRACT v1.5 #1
  addendum 1c, from the Replit soak). The child was spawned with
  `stderr=subprocess.DEVNULL`, so any crash before it could write
  its report tail — a module-import failure, rlimit setup, anything
  pre-`main` — surfaced as an opaque `crashed` with
  "exited N with no report tail" and the real exception gone. The
  pre-execution gate must never swallow its own failure cause:
  stderr is now PIPED and, on a crashed/no-tail exit, its tail is
  folded into `TrialReport.detail`. A child that dies importing
  `activegraph` now reports the `ModuleNotFoundError`, not an opaque
  string.

### Documentation

- **Retention's offline contract ruled per-RUN, not per-file**
  (CONTRACT v1.5 #2 addendum 2b, a downstream confirm-request).
  Retiring a finished fork while the parent run holds a live runtime
  on the same SQLite file is sanctioned — WAL + `run_id`-scoped
  statements + single-statement autocommit appends + one short
  `BEGIN IMMEDIATE` archive transaction; the rule protects against
  the SAME-run case (verified: `compact` under an attached runtime
  collides on `UNIQUE(id, run_id)`). Caveats stated in the
  docstrings: `pins` → archive is check-then-act (don't race
  pin-creating operations against retirement of that run), and an
  oversized archive move can outlast a concurrent writer's busy
  timeout (`OperationalError`, never corruption). Regression test
  `test_retire_fork_per_run_while_parent_runtime_is_live`.

## [v1.6.0] — 2026-07-08

The closing-the-loop release: two downstream-facing confirmations
(recorded-segment replay expressible as shipped; the fork-tail
removal pattern pinned as contract) plus the manifest validator's
promised warning tier, starting the Q2 clock. Everything additive on
1.5.0; packs pinned `>=1.5,<2.0` keep working unchanged.

### Migration

No breaking changes. One behavioral note: packs that ship a
`manifest.toml` at their pack root now get it validated at
`load_pack`, with violations logged as a single structured WARNING
per pack per process (`activegraph.packs.manifest` logger). Nothing
fails that loaded before; silence the logger if the noise is
unwanted, or fix the manifest — it becomes enforceable no earlier
than 2.0.

### Added

- **Recorded-segment replay: confirmed expressible, worked example
  added** (evolution stage 3's consumer use case). `run_forked_trial`
  as shipped in v1.5.0 covers it with no interface extension: the
  recorded segment IS the fork's history, so the scenario reads the
  recorded inputs back from `rt.trace.events()`, re-injects each as a
  fresh event (fresh id, `actor="trial.replay"`), and drains — the
  candidate's behaviors process the segment in order inside the
  child, and failures + counts read back from the fork's run in the
  store. Worked example now in `trial-isolation-design.md` §2b;
  executable proof in
  `tests/test_sandbox_trial.py::test_recorded_segment_replay_inside_the_trial`.
  The design doc's stale "design for review" status header updated to
  reflect that v1.5.0 shipped it.
- **Fork-tail removals pinned as ordinary events** (CONTRACT v1.3 #4
  addendum 4d). The downstream residue policy — a fork removes every
  entity it created before promoting, so scaffolding reads
  base-None/fork-None and vanishes from the delta while shared-state
  patches promote — is now contract: no promote version, compaction
  pass, or plan optimization may special-case remove-events near the
  fork tip. Regression test
  `test_residue_policy_fork_tail_removal_of_fork_created_entities`
  (covers explicit relation removal, `remove_object` cascade through
  a relation into shared state, and the promote marker's payload);
  sentence added to `promote-design.md` §4.
- **Loader-side manifest validation, warning tier** (CONTRACT
  v1.6 #1 — the Q2 schedule's clock starts). When a `manifest.toml`
  is discoverable at the pack root (best-effort: the pack's
  component modules resolved via `sys.modules`, walked up while
  inside the package), `load_pack` runs `load_manifest` +
  `verify_surface` and emits ONE structured stdlib-`logging` WARNING
  per (pack, version, manifest path) per process on the
  `activegraph.packs.manifest` logger, carrying the full
  `violations` list and `reason="pack.manifest_invalid"`. The pack
  loads regardless — never an error before 2.0, and a bug in the
  tier itself degrades to DEBUG, not a load failure. Absent
  manifest: silent. Hash verification stays host/CI territory (the
  loader never re-hashes a pack directory on the load path). Tests
  cover both tiers: clean manifest silent, absent manifest silent,
  surface drift warns once and still loads, malformed TOML warns and
  still loads.

## [v1.5.0] — 2026-07-08

The designs-become-code release: the two v1.4 design docs (trial
isolation, compaction) become runtime primitives, in dependency order
for the downstream assistant's self-modification loop. Everything
additive on 1.4.0; packs pinned `>=1.4,<2.0` keep working unchanged.

### Migration

No breaking changes. Notes: the SQLite store gains two additive
tables (`events_archive`, `snapshots`) created `IF NOT EXISTS` —
`schema_version` stays `1` and files remain readable by 1.4.0
runtimes (which simply never touch the new tables; do not mix
versions against a store you have already compacted, since a 1.4.0
reader would see only the post-snapshot suffix).

### Added

- **Compaction phase 1: snapshot + archive tier + the pin set**
  (CONTRACT v1.5 #2; `compaction-design.md` phase 1).
  `activegraph.store.retention` ships `compact` (snapshot event +
  blob sidecar + idempotent prefix archival — never deletion),
  `retire` (whole closed unpinned runs), `pins` (the "why can't I
  retire this?" API), and `verify_snapshot` (replay the archive,
  prove the pinned state hash). **The pin set dominates retention
  policy unconditionally**, headed by the normative retention pin: a
  fork referenced by any live `promote.applied` marker is pinned
  whole — the property is tested directly. Compacted runs load by
  hash-verified snapshot projection plus suffix replay
  (`SnapshotIntegrityError` on corruption); forks of compacted
  parents share the snapshot base; fork points below the horizon
  refuse loudly; strict replay verifies the suffix. Phase-1
  boundaries stated in the contract: same-file archive table only,
  no archive-aware causal chains yet, no CLI yet, proposed-status
  patches block compaction.

- **Subprocess fork-trial isolation** (CONTRACT v1.5 #1;
  `trial-isolation-design.md` implemented as scoped).
  `activegraph.sandbox.run_forked_trial(store, parent_run_id=...,
  at_event=..., pack_source=..., scenario=..., limits=...)`: the
  parent forks (child never gets fork authority), a fresh-interpreter
  child materializes the candidate from artifacts pinned by the
  bundle hash (`verify_bundle_hash` before any import, then manifest
  + two-way surface check — the first end-to-end consumer of the
  manifest chain), and runs the scenario under three independent nets
  (rlimits, parent-side wall-clock kill, runtime budgets).
  Key-freedom is structural: the child configures no LLM provider.
  Outcomes are a closed set; the store is the record — the parent
  re-reads the fork run and the stdout tail never overrides it.
  Honest limits carried into the API docs: crash/state isolation,
  not a security sandbox; syscall/network confinement stays host
  territory; the shared-SQLite-file caveat is stated. Seven
  deterministic key-free tests, including a candidate that blows
  each budget, one that hard-crashes the child, and one whose
  undeclared surface is refused before load — every failure case
  re-proves the parent run is byte-untouched.

## [v1.4.0] — 2026-07-08

The manifest-and-rollback release, cut fast because PyPI 1.3.0
predates three commits downstream CI depends on (the manifest
validator, promote apply-time validation, graph-backed approvals).
Everything additive on 1.3.0; packs pinned `>=1.3,<2.0` keep working
unchanged. Driven by the evolution pack's consumer sign-off and the
pack-manifest spec review; two design docs published for review
(`compaction-design.md`, `trial-isolation-design.md` — the
subprocess-trial and compaction implementations land after review;
streaming stays design-first behind its replay-unit question, ROADMAP
Phase 7).

### Migration

No breaking changes. Notes: the provisional
`activegraph.packs.manifest` API remains importable only from its
module path and may take one round of breaking edits before the spec
exits DRAFT; external pack pins should move from the content hash to
the new bundle hash (the content hash cannot cover `manifest.toml`).

### Added

- **Subprocess fork-trial isolation design published for review**
  (`trial-isolation-design.md`): parent-side fork, fresh-interpreter
  child, artifact materialization pinned by the bundle hash, result
  schema where the store is the record, and an honest-limits section
  (crash/state isolation, not a security sandbox; syscall/network
  confinement is host territory). Design only; the evolution pack's
  T5 ask.
- **`runtime.disable_pack(name)`** (CONTRACT v1.4 #3) — the evolution
  pack's rollback primitive. Deregisters a loaded pack's behaviors,
  tools, typed schemas, and gating policies from the live registries
  (short-name ambiguities recompute rather than staling), emits a
  queue-visible `pack.disabled` event with the deregistered surface,
  leaves pack-created state untouched, and is idempotent. Honestly
  not unload: code objects stay in memory, inert — restart to evict.
  Re-enable is `load_pack` again.
- **Bundle hash** (`compute_bundle_hash` / `verify_bundle_hash` in
  `activegraph.packs.manifest`, CONTRACT v1.4 #1 amendment): the §4
  walk WITHOUT the manifest exclusion, for EXTERNAL pins. The
  manifest-internal `content_hash` necessarily excludes itself, which
  left external pins blind to manifest swaps — risk classes,
  `consumes`, `authored_by` are all in the manifest. `[load.pins]`,
  evolution proposal pins, and resolvers pin the bundle hash.
- **`Pack.capabilities`** (manifest spec Q8, runtime half; CONTRACT
  v1.4 #1 amendment): declarative `CapabilityDecl` tuple validated at
  construction, two-way checked by `verify_surface` (including
  risk-class agreement), recorded in the `pack.loaded` payload so
  decision surfaces read declared outbound reach from the graph.
  Registration stays imperative host wiring; the gateway-side check
  is downstream's half.
- **Provisional pack-manifest validator** (CONTRACT v1.4 #1).
  `activegraph.packs.manifest` ships the reference implementation of
  the pack manifest spec (activegraph-packs `docs/manifest-spec.md`,
  DRAFT): `load_manifest` (parse + schema-validate, every violation
  aggregated into one `PackManifestError`), `verify_surface` (two-way
  manifest ↔ `Pack` check per the spec's identity mapping;
  capabilities/consumes stay statically verified downstream), and
  `compute_content_hash` / `verify_content_hash` (spec §4,
  byte-exact, with runtime amendments: directory symlinks rejected
  like file symlinks, non-UTF-8/non-NFC paths rejected loudly).
  **PROVISIONAL** — importable only from `activegraph.packs.manifest`;
  expect one round of breaking edits before the spec exits DRAFT.
  `load_pack` does not enforce manifests this cycle (grandfathering).

- **Promote apply-time schema validation** (CONTRACT v1.3 #4 addendum
  4c). Promote's hand-built events bypass `add_object`'s pack-schema
  hook; apply now runs the parent's object and relation validators
  over the full delta pre-mutation. Typed data violating a
  parent-loaded schema raises `PackSchemaViolation` with nothing
  applied; valid typed data is stored canonicalized exactly as
  `add_object` would store it; undeclared types keep v0.9 untyped
  semantics. Catches fork/parent pack version skew at the boundary.

- **Graph-backed pending approvals** (CONTRACT v1.4 #2). The
  pending-approval queue was in-memory only and silently dropped on
  restart. `approval.proposed` events now carry the full deferred
  payload, and `Runtime.load` / `fork()` rebuild the queue from the
  log (proposed minus granted, id counter reseeded), so a reloaded
  runtime can `approve()` a proposal made before the restart and a
  fork inherits proposals pending at its fork point. Proposals
  recorded before v1.4 lack the payload in their events and are not
  reconstructible (documented boundary; no history rewriting).
- **Compaction/retention design published for review**
  (`compaction-design.md`; shipped a cycle later as CONTRACT
  v1.5 #2, phase 1): snapshot
  events + archive tier, never deletion; a normative pin set that
  retention policy can never override — promoted-from fork logs
  (evolution-pack sign-off input), live lineage, replay-cache
  dependence, pending machinery. Design only; implementation after
  review.

### Fixed

- `load_prompts_from_dir` skips hidden files and symlinks. pathlib's
  `glob("*.md")` matched both, so a prompt the runtime loaded could
  be a file the manifest content hash excludes (hidden) or rejects
  (symlink) — unhashed load-bearing content. Nothing the loader
  reads may now escape the hash pin.

## [v1.3.0] — 2026-07-08

The agent-readiness release. Two arcs: the v1.3 cycle work locked in
[CONTRACT.md § v1.3](https://github.com/yoheinakajima/activegraph/blob/main/CONTRACT.md)
(#1 native structured output, #2 community surface — merged in PR #50
but not changelogged at merge time; this section pays that debt), and
the July 2026 agent-readiness upgrade arc driven by the downstream
pack library's runtime evaluation: **promote** — the missing third of
fork → test → promote (#4, design-reviewed downstream before
implementation) — plus developer-experience surfacing, provider
compatibility (#3), and the embedding seam. Everything is additive;
code pinned to 1.2.0 keeps working unchanged.

### Migration

No breaking changes. Two behavioral notes: LLM auth/invalid-request
failures now fail immediately with `llm.auth_error` /
`llm.request_error` instead of being retried under
`llm.network_error` (custom code branching on reason codes may want
the new ones); and decorating a handler whose signature cannot
satisfy its calling convention now raises `TypeError` at the
decorator line instead of failing at first invocation.

### Added

- **`Runtime.promote(fork, dry_run=False)` — the third of
  fork → test → promote** (CONTRACT v1.3 #4; design reviewed
  downstream and locked in `promote-design.md`). Applies a fork's
  net structural delta to its parent as ordinary, audited parent
  events: three-way comparison against the parent's state at the
  recorded fork point; fork-only changes promote, parent-only
  changes are untouched, both-sides changes (including identical
  concurrent edits) raise `PromoteConflictError` before any mutation
  — fail-closed, atomic, no semantic merge, no `force=`. Referential
  integrity conflicts (dangling promoted relations, removals that
  would orphan parent relations) are part of the check. The delta
  applies quiescently — behaviors react only to the single
  queue-visible `promote.applied` marker event, never per delta
  event, live or on reload. Dry-run plans are advisory (apply
  recomputes against parent-now; `computed_against` records the tip
  the plan saw). Lineage is verified from the store's runs table
  (`PromoteLineageError`; grandchildren promote one level at a
  time). Ships with the `activegraph promote` CLI command
  (conflicts exit 5), a `[promote.applied]` trace rendering, and the
  new [Fork, test, promote](https://docs.activegraph.ai/guides/fork-test-promote/)
  guide. Strict replay (`replay_strict=True`) projects promote blocks
  verbatim at their recorded position and excludes them from the
  re-derivation comparison (behaviors were quiescent when they
  landed); the marker-subscription re-derivation gap is a documented
  known limitation in the v0.5 #7 tradition.

### Fixed

- **Promote warnings derive from the event logs, not live runtime
  state.** `loaded_packs()` is empty on a `Runtime.load`-ed runtime
  (packs are code), so the CLI promote path silently lost every
  fork-only pack warning — the governance signal the design names —
  and a live fork promoted into a reloaded parent false-positived.
  Pack-load and settings-override warnings now read `pack.loaded` /
  `pack.settings_overridden` events positionally from the fork tail.
- **`activegraph promote` CLI hardening** (adversarial review):
  mistyped run ids exit 3 with a clear message instead of inserting a
  phantom run row and reporting a misleading lineage error; a
  conflicted `--dry-run` exits 5 like a conflicted apply, so scripts
  can gate on it; bare filesystem paths are a usage error (exit 2),
  matching `inspect`/`fork`. Full CliRunner coverage added.
- **Strict replay no longer falsely diverges on multi-goal runs.**
  `_verify_replay` used to batch every seed event and drain once, so
  a recorded `goal1, derived1, goal2, derived2` stream replayed as
  `goal1, goal2, derived1, derived2` and diverged. The verify run now
  drains the queue exactly where the recorded log shows derivation
  happened, matching the original interleaving (also what lets
  promote blocks project at their true positions).
- **`fork(at_event=...)` rejects cutoffs that would slice a promote
  block** (marker or mid-delta): the child would requeue the marker
  against a partially-applied delta. The structured error names the
  block's final event id as the valid cutoff.
- **`upsert_run` no longer erases fork lineage on reload** (both
  SQLite and Postgres stores; surfaced by promote). `Runtime.load`
  upserts the run row with only `created_at`, and the blind
  `ON CONFLICT` overwrite nulled `parent_run_id` /
  `forked_at_event_id` / `label` on every reload — silently
  destroying the lineage records `fork()` had written (and that
  `promote()` verifies against). The three columns now get the same
  `COALESCE` protection `goal` / `frame_id` already had.
- **Pack-scoped tool names no longer break provider function calling**
  (CONTRACT v1.3 #3). Canonical dotted names (`diligence.fetch_docs`)
  are outside both providers' `[a-zA-Z0-9_-]` tool-name alphabet, so
  every pack tool offered to a model was a guaranteed request
  rejection. Names are now rewritten (`.` → `__`) in outbound tool
  definitions and echoed assistant tool-call turns, and returned calls
  reverse-map through an explicit per-request table
  (`activegraph/llm/wire.py`) — the runtime, event log, and fixtures
  only ever see canonical names. Non-dotted names are byte-identical
  on the wire, so recorded v1.2 runs replay unchanged.
- **`OpenAIProvider` no longer 400s on reasoning-model families**
  (CONTRACT v1.3 #3). `o1`/`o3`/`o4`/`gpt-5` models get
  `max_completion_tokens` and omit `temperature`/`top_p` (the API
  rejects the GPT-4-era parameters); other families are unchanged.
  Family table overridable via the `reasoning_model_prefixes=`
  constructor kwarg.
- **Auth and invalid-request failures are terminal, not retried**
  (CONTRACT v1.3 #3). New reason codes `llm.auth_error` (401/403 —
  bad or revoked API key) and `llm.request_error` (other 4xx —
  unknown model, rejected parameter) split out of the
  `llm.network_error` catch-all, which sits in the transient-retry
  set — so a revoked key was previously retried with exponential
  backoff and then reported as a network problem. Unrecognized
  exception shapes keep the `llm.network_error` classification and
  its retry behavior.

### Added

- `EmbeddingProvider` protocol + `HashEmbeddingProvider` test double
  (`activegraph.llm`): the runtime's second provider seam, next to
  `LLMProvider`. `Runtime(embedding_provider=...)` /
  `Runtime.load(..., embedding_provider=...)` hold one for packs
  (memory/retrieval capabilities read `runtime.embedding_provider`);
  forks inherit it and can override per-fork. The runtime never calls
  it itself, ships no real embedding implementation (no network deps,
  no keys), and `None` remains the not-configured signal packs degrade
  against. `HashEmbeddingProvider` is a deterministic, dependency-free
  double for testing embedding plumbing offline.
- `@tool` infers `input_schema` from the first parameter's Pydantic
  annotation when `input_schema=` is omitted
  (`def fetch(args: FetchArgs, ctx)` → the model sees `FetchArgs`'s
  JSON schema instead of an empty parameters object, and the runtime
  validates arguments before invocation). Explicit `input_schema=`
  wins; unannotated or non-model-annotated tools are unchanged.
- `Trace.events()` and `Trace.failures()`: structured accessors on
  `runtime.trace`. `events()` returns the run's events as `Event`
  objects in log order — the discoverable way to pick an event id for
  `runtime.fork(at_event=...)` (an external evaluation previously had
  to read the SQLite events table directly). `failures()` returns the
  run's `behavior.failed` events, whose payloads have carried the full
  exception `traceback` since v1.0.3 — now documented and reachable
  without filtering `graph.events` by hand. See the
  [debugging cookbook](https://docs.activegraph.ai/cookbook/debugging/).
- Registration-time handler-signature validation: `@behavior`,
  `@relation_behavior`, `@llm_behavior`, and `@tool` (both the global
  and pack-scoped decorators) now raise `TypeError` at decoration time
  when the function cannot accept the decorator's positional calling
  convention — e.g. a 2-arg `@behavior` handler or a 1-arg `@tool`.
  Previously these registered fine and failed at first invocation with
  the `TypeError` buried in a `behavior.failed` event. The check is
  permissive where the call can still succeed: `*args`/`**kwargs`,
  extras with defaults, and the pack settings-injection pattern
  (annotated extras, e.g. `*, settings: MyPackSettings`) all pass.
  Callables without an inspectable signature are skipped. Same
  precedent as `output_schema=` strict validation (CONTRACT v1.0.3 #2).
- **Native structured-output mode, opt-in** (CONTRACT v1.3 #1; PR #50).
  `Runtime(native_structured_output=True)` resolves a per-behavior
  mode at registration time as a pure function of the flag, the
  provider's capability claim (`supports_native_structured_output`,
  additive and `getattr`-guarded like the v1.0.2 provider methods),
  the resolved model, and an offline schema pre-flight
  (`activegraph/llm/native.py`). `AnthropicProvider` sends the
  Messages API `output_config` JSON-schema format; `OpenAIProvider`
  sends Chat Completions `response_format` with `strict: true`. Both
  gate by table-driven model-family allowlists with constructor
  overrides (the pricing-table pattern). Prompt-mode calls stay
  byte-identical to v1.2: the `structured_output_mode` kwarg is passed
  only when native mode resolved. The mode contributes to prompt
  hashes and fixture payloads only when native (omit-when-absent), so
  every pre-v1.3 hash and fixture is untouched; a record-vs-replay
  mode flip raises the existing `ReplayDivergenceError`.
- `CODE_OF_CONDUCT.md` (Contributor Covenant v2.1) with a
  public-by-design reporting model: conduct reports go through the
  public channels (GitHub issues, X `@yoheinakajima`) rather than a
  private inbox the single maintainer cannot commit to staffing
  (CONTRACT v1.3 #2, revised in PR #50).

### Changed

- `CONTRIBUTING.md`: the direct-PR exemption extends from docs-only
  changes to no-behavior-change fixes anywhere (no test behavior
  change, no public signature change, no CONTRACT surface)
  (CONTRACT v1.3 #2).

## [v1.2.0] — 2026-07-03

The GraphStore release: the materialized graph projection becomes a
pluggable seam with a FalkorDB backend, contributed by
[@dudizimber](https://github.com/dudizimber) (issues #38, #41, #43,
#45; PRs #39, #46). The architectural decisions are locked in
[CONTRACT.md § v1.2](https://github.com/yoheinakajima/activegraph/blob/main/CONTRACT.md)
(#1–#6), including the provenance note recording that the lock was
written retroactively during release preparation.

### Added

- `GraphStore` abstraction for the materialized graph projection, with
  `InMemoryGraphStore` (the default) and `FalkorDBGraphStore` backing the
  current-state view in a FalkorDB graph. `Graph(graph_store=...)`,
  `Runtime.load(..., graph_store=...)`, and `Runtime.fork(..., graph_store=...)`
  select where the projection is materialized; the event log remains the
  source of truth. In FalkorDB, objects are `:AGNode:AGObject` nodes and
  relations are **native `AGRelation` edges** between them, so the
  projection is a real, traversable graph; dangling relations are supported
  via bare `:AGNode` placeholder endpoints. The store
  connects to a running FalkorDB via `url=`/`host=` arguments or the
  `FALKORDB_URL` / `FALKORDB_HOST` (`_PORT` / `_USERNAME` / `_PASSWORD`)
  environment variables, falling back to the embedded `falkordblite`
  engine. Install with `pip install 'activegraph[falkordb]'` (server
  client) or `'activegraph[falkordb-embedded]'` (embedded engine). See the
  [Using the FalkorDB graph store](https://docs.activegraph.ai/guides/using-falkordb/)
  guide.
- `GraphStore` query hooks (`find_objects`, `find_objects_in_types`,
  `find_relations`, `neighborhood`, `match_chain`) that let backends evaluate
  structural queries close to the data. `Graph.objects(type=...)`,
  `Graph.objects_in_types(...)`, `Graph.relations(...)`,
  `Graph.get_relations(...)`, `Graph.neighborhood(...)`, and the pattern
  matcher delegate to these hooks. The default implementations compute
  results in Python (identical to
  before), while `FalkorDBGraphStore` overrides them with Cypher so type
  filters, relation lookups, neighborhood walks, and whole pattern chains run
  inside the database instead of scanning the whole projection. `where`
  predicates still evaluate in Python since the structured payload is stored
  as JSON.
- Agent-discovery files at the doc-site root: `/llms.md`, a markdown
  mirror of `/llms.txt` produced by a post-build hook
  (`scripts/mkdocs_hooks.py`) for AI agents that probe well-known `.md`
  root paths on a static host that cannot do Accept-header content
  negotiation, and `/robots.txt` with the canonical `sitemap.xml`
  pointer (copied verbatim from `docs/robots.txt`). Both are gated by
  `tests/test_llms_txt.py` in the docs workflow. PyPI metadata now
  carries `Documentation`, `Repository`, `Changelog`, and `Issues`
  project URLs so the package is verifiable as the official
  distribution for docs.activegraph.ai.

### Changed

- Pattern matching now resolves the entire structural chain through the
  pushed-down `Graph.match_chain(...)` hook instead of scanning
  `all_objects()` / `all_relations()` on every event. On
  `FalkorDBGraphStore` a whole pattern (node types + relation
  types/directions) collapses into a **single** index-backed Cypher query
  rather than one round-trip per hop per candidate; the default store
  reproduces the same depth-first walk, so match results and their order are
  unchanged. Semantics stay homomorphic (a node or relation may fill more
  than one position). Node `{prop: value}` equality and `WHERE` predicates
  continue to evaluate in Python over the resolved chains.
- Cascade-deleting an object's relations (on `object.removed`) now uses two
  scoped `find_relations` lookups (out-edges + in-edges) instead of scanning
  every relation, so on `FalkorDBGraphStore` it is an index-backed query
  rather than a full edge scan.
- Matching `RelationBehavior`s now filters candidate edges via the pushed-down
  `Graph.relations(type=...)` hook instead of scanning every relation on each
  event; on `FalkorDBGraphStore` this is an index-backed lookup.
- Building a behavior `View` with a type-scoped `include_types` spec (and no
  `around` anchor) now pushes the type filter into the store via
  `Graph.objects_in_types(...)` (a single `type IN [...]` query on
  `FalkorDBGraphStore`) instead of materializing every object and filtering in
  Python. The default store preserves the same single-pass order, so the
  in-memory `View` is byte-for-byte unchanged.

### Tests

- The test suite is now a CI gate (`.github/workflows/tests.yml`,
  CONTRACT v1.2 #6): `pytest -m "not slow"` runs on every push to
  main and every pull request, on a Python 3.11 + 3.12 matrix. The
  3.12 leg installs `falkordb-embedded` so the FalkorDB store and
  `GraphStoreConformance` tests execute, and a Postgres 16 service
  container runs the Postgres `EventStore` conformance tests. Every
  non-slow test now executes on at least one matrix leg; previously
  no workflow ran the suite at all.
- `GraphStoreConformance` (`activegraph/store/graph_conformance.py`)
  is the pytest-collectable extension contract for graph-store
  backends; `InMemoryGraphStore` and `FalkorDBGraphStore` both
  inherit it (CONTRACT v1.2 #5).

### Migration notes

- No store schema migration is required. Runs that do not pass
  `graph_store=` behave byte-for-byte as before; `InMemoryGraphStore`
  remains the default.
- The FalkorDB backend is opt-in via the new `activegraph[falkordb]`
  (server client) or `activegraph[falkordb-embedded]` (embedded
  engine, Python 3.12+) extras. Neither is included in `[all]` or
  `[dev]`.
- The FalkorDB projection layout changed between the pre-release
  relations-as-nodes shape (PR #39) and the released native-edge
  shape (PR #46). No migration tooling exists or is needed: the
  projection is disposable — point `Runtime.load(...,
  graph_store=...)` at the event log and it rematerializes in the
  new layout.
- Event consumers are unaffected: v1.2.0 adds no new event types,
  reason codes, or error classes.


## [v1.1.0] — 2026-06-10

### Added

- `Runtime` now performs bounded provider-call retries for transient
  LLM failures (`llm.network_error`, `llm.rate_limited`) before the
  terminal `behavior.failed` path. Failed attempts are visible as
  error-shaped `llm.responded` events and successful object provenance
  points at the successful retry attempt.
- `activegraph inspect --memo` renders memo objects with the same
  operator format used by `activegraph quickstart`.
- `activegraph inspect --search <query>` searches event ids, types,
  actors, and payload JSON.
- `activegraph fork --set <pack>.<setting>=<value>` records fork-local
  pack setting overrides as `pack.settings_overridden` events. Pack
  loading applies and validates those overrides before post-fork
  execution resumes.
- `OpenTelemetryMetrics` ships as a second optional metrics backend
  alongside `PrometheusMetrics`. Install with
  `pip install 'activegraph[opentelemetry]'`.
- `OpenAIProvider` now supports the shared LLM/tool loop by translating
  framework tool definitions to OpenAI Chat Completions function tools
  and extracting returned `tool_calls` into the shared `ToolCall` shape.
- Release drift gates now cover CLI-reference flags, executable Python
  doc snippets, reason-code docs, and tagged-release version
  correspondence.

### Changed

- `LLMCache.from_events` ignores error-shaped `llm.responded` events so
  retry failures cannot poison replay caches.
- The trace printer annotates LLM retry attempts and error-shaped
  responses.
- Docs now use `patch.applied` for direct object patches instead of the
  non-emitted `object.patched` event name.
- The failure model now states why strict replay
  `ReplayDivergenceError` and dispatch-time contract failures escape
  instead of becoming `behavior.failed`.
- The LLM provider reference now treats OpenAI and Anthropic tool use as
  supported through the same runtime contract. Native provider
  structured-output modes remain deferred in `FUTURE_IDEAS.md`.

### Tests

- Pinned `Diff.is_identical` as a bool property (`is True` /
  `is False`) to close issue #28.
- Added retry, replay-cache, CLI-selector, fork-override,
  OpenTelemetry, OpenAI tool-parity, doc-snippet, CLI-reference, and
  reason-code regression coverage.

### Migration notes

- No store schema migration is required.
- Event consumers that treat framework event types as a closed list
  should allow the new `pack.settings_overridden` event.
- The new OpenTelemetry backend is optional; existing Prometheus and
  custom `Metrics` implementations continue to work unchanged.

## [v1.0.5.post2] — 2026-05-20

Type-system concepts page. A maintainer-driven doc-gap review found
that the framework's type system is documented across four concepts
pages (`graph.md`, `events.md`, `relations.md`, `patches.md`) plus
the pack-authoring guide, with no single page answering the question
a new reader arrives with: *what types are framework-defined, what
types are developer-defined, and how do they compose?* In
particular, "are there framework base object types?" — the answer
is no — was reachable only by assembling fragments from three pages.

v1.0.5.post2 ships one new concepts page. No framework code changes.
No new public API. No reshape of any locked decision below
v1.0.5.post2.

### The single finding

  v1.0.5.post2 #1 — A type-system concepts page lands at
                    `docs/concepts/type-system.md`, slotted into
                    the Concepts nav between Graph and Events. The
                    page commits to four claims: (a) object types
                    are developer-defined strings; the framework
                    ships zero base object types; (b) relation
                    types follow the same model; (c) event types
                    are framework-defined, with the complete
                    enumerated set in named families
                    (lifecycle / graph mutations / behavior dispatch
                    / patterns / LLM / tools / patches / approvals
                    / pack lifecycle); (d) patch lifecycle states
                    (`proposed | applied | rejected`) are
                    framework-defined.

### Added

- **`docs/concepts/type-system.md`** (v1.0.5.post2 #1). The new
  page. Sections: the framework-defined layer (event types, fully
  enumerated); the developer-defined layer (object types); the
  developer-defined layer (relation types); how the three layers
  compose; patch lifecycle states; designing an ontology; the
  Diligence pack ontology as the worked example. The page makes
  the framework's "no base object types" stance explicit because
  users from typed-schema backgrounds (databases, Pydantic,
  GraphQL, Protobuf) arrive expecting a schema-definition step.
  The deep-research-agent user-test finding ("object types should
  be nouns describing their role in the pipeline, not just data
  bags") is the surfacing path for the ontology design guidance.

### Changed

- **`README.md`**: new `## The type system at a glance` section between
  `Concepts at a glance` and `A small example`. Three short beats —
  event types are fixed (full enumerated list inline), object and
  relation types are yours (no central schema, no registration), patch
  states are fixed — plus a pointer to the new concepts page. The
  bridge between the primitive index and the example: a reader who
  has skimmed the twelve primitives now knows the vocabulary the
  example uses (`object.created`, the custom `task.completed`, the
  `task` and `depends_on` strings) before they hit the code. The
  section calls out the `task.completed` custom-event-type usage in
  the example explicitly so the framework-vs-application event
  distinction lands before the code rather than after it.
- **`mkdocs.yml`**: nav adds `Type system: concepts/type-system.md`
  to the Concepts section between Graph and Events; the
  `mkdocs-llmstxt` plugin's `sections.Concepts` list adds the
  matching entry with its one-sentence description. The
  `tests/test_llms_txt.py` gate from v1.0.5 #1 picks up the new
  page automatically at build time (the generated `/llms.txt` and
  `/llms-full.txt` regenerate from `mkdocs.yml` + the `docs/`
  source on every build, so the new page appears in both).
- **`pyproject.toml`**: `version` bumps `1.0.5.post1` →
  `1.0.5.post2`.
- **`activegraph/__init__.py`**: `__version__` tracks the bump
  (the `test_version_sync` gate asserts it matches
  `pyproject.toml` byte-identically).
- **`tests/snapshots/errors/{internal_bug__pattern_unknown_op,
  internal_bug__graph_view_unknown_op,schema_version_mismatch}.txt`**:
  rebaselined the embedded version string from `1.0.5.post1` to
  `1.0.5.post2` (same three snapshots v1.0.5.post1 rebaselined
  for the same reason — they render `activegraph.__version__`
  inline per the internal-bug context format and the
  schema-version-mismatch context format).

### CONTRACT amendments

- **`v1.0.5.post2` milestone added** with one numbered finding
  (`v1.0.5.post2 #1`) and a "deliberately does NOT touch"
  section. Single-finding milestone — same scope discipline as
  v1.0.1 / v1.0.2 / v1.0.2.post1 / v1.0.5 / v1.0.5.post1.
  Appended as a new section after v1.0.5.post1 per Standing
  Rule §1.

### v1.1 backlog (filed in `v1.1-plan.md`)

- **`object.patched` event-name drift in `docs/concepts/events.md`.**
  The page's "Object mutations" family lists `object.patched`,
  but the code emits `patch.applied` for the direct
  `graph.patch_object(...)` shortcut and never `object.patched`.
  Either correct the doc (drop the name or rename to
  `patch.applied`) or add the event in code; the fix shape
  depends on whether direct mutations were intended to be
  distinguishable from patch applies. Surfaced during the
  v1.0.5.post2 diagnosis; out of scope for a "no concepts-page
  reshape" docs-only release.
- **Reason-code taxonomy as a dedicated concepts page.** The
  `behavior.failed` / `tool.responded` `reason=` field carries a
  closed taxonomy (`llm.network_error`, `llm.parse_error`,
  `tool.unknown_tool`, `tool.max_turns_exhausted`, …) documented
  only across the per-error pages. A dedicated reference (or
  `failure-model.md` expansion) could enumerate the codes the
  way v1.0.5.post2 #1 enumerates the event types.

### Migration from v1.0.5.post1

Forward-compatible. No code changes required. The runtime API,
public surface, and CI gates are unchanged. The doc site grows
by one page; existing pages stay byte-identical. `/llms.txt` and
`/llms-full.txt` regenerate automatically on the next
`mkdocs build` (the v1.0.5 #1 structural-drift guarantee).

```bash
pip install --upgrade activegraph==1.0.5.post2
```

- Every v1.0.5.post1 surface (LICENSE, NOTICE, CONTRIBUTING.md,
  issue templates, license metadata) stays byte-identical.
- Every v1.0.5 surface (`/llms.txt`, `/llms-full.txt`,
  `mkdocs-llmstxt` plugin, `tests/test_llms_txt.py`) stays
  byte-identical apart from the new entry the plugin picks up
  from `mkdocs.yml`.
- Every v1.0.4 / v1.0.3 / v1.0.2.post1 / v1.0.2 / v1.0.1 / v1.0
  surface stays byte-identical.

## [v1.0.5.post1] — 2026-05-19

Pre-launch foundation pass before the repository is flipped public.
Three coupled deliverables: the framework's license switches from
MIT to Apache 2.0; a `CONTRIBUTING.md` lands with an issues-first
contribution policy; the `.github/ISSUE_TEMPLATE/` surface lands with
three structured templates plus a `config.yml` that disables blank
issues. Coupled because shipping any one without the others would
leave a public repository in a half-stated posture.

No framework code changes. No new public API. No reshape of any
locked decision below v1.0.5. The release surface is repo-root
metadata (`LICENSE`, `NOTICE`, `pyproject.toml`'s license field,
`README.md`'s license section), contributor-facing prose
(`CONTRIBUTING.md`), and the GitHub issue-template surface
(`.github/ISSUE_TEMPLATE/`). The framing — post-release patch
between a numbered milestone and the next — matches the v1.0.2.post1
precedent (CONTRACT v1.0.4 #6's appended archeology section is the
canonical example of how a `.postN` release lands in CONTRACT under
Standing Rule §1).

### The finding (v1.0.5.post1 #1)

  v1.0.5.post1 #1 — Active Graph is licensed under Apache 2.0 from
                    v1.0.5.post1 forward. Three reasons named in the
                    CONTRACT amendment: explicit patent grant (§3 of
                    the license, which MIT does not provide;
                    load-bearing for a framework whose primitives —
                    event-sourced reactive graph, relation behaviors,
                    binding-moment validation, pack format — are
                    themselves the contribution surface);
                    institutional standard for foundation-shaped
                    projects (ASF, CNCF, LF AI; matches enterprise
                    legal-review calibration); legal precision on
                    trademark / contribution / NOTICE boundaries
                    (§§6, 5, 4(d); MIT leaves these implicit). The
                    previous declared license was MIT, recorded in
                    `pyproject.toml` and `README.md` through twelve
                    milestones but never accompanied by a `LICENSE`
                    file at the repo root — this release is the
                    first to ship the canonical license text.

### Added

- **`LICENSE`** (v1.0.5.post1 #1). Canonical Apache 2.0 text from
  `https://www.apache.org/licenses/LICENSE-2.0.txt` prefixed with
  a single-line `Copyright 2026 Yohei Nakajima` header. The body
  below the header is byte-identical to the Apache Foundation's
  canonical plain-text version.
- **`NOTICE`** (v1.0.5.post1 #1). The Apache 2.0 §4(d) attribution
  pair. Two lines: project name (`Active Graph`) and copyright
  line (`Copyright 2026 Yohei Nakajima`). Downstream redistributors
  preserve NOTICE per §4(d).
- **`CONTRIBUTING.md`** (v1.0.5.post1 #1). Issues-first
  contribution policy for the framework's early public phase.
  Issues are open; code PRs are maintainer-only with an
  issue-first discussion gate; documentation PRs may be opened
  directly. Names the policy as a pre-launch posture (not a
  permanent stance) with the relaxation criteria stated. Includes
  the explicit Apache 2.0 §5 inbound-equals-outbound statement.
  Names three out-of-scope items mirroring the CONTRACT
  amendment's "deliberately does NOT touch" section: CLA / DCO
  decision, `CODE_OF_CONDUCT.md` paired with a contact channel,
  broader contributor surface.
- **`.github/ISSUE_TEMPLATE/bug_report.md`**,
  **`feature_request.md`**, **`question.md`** (v1.0.5.post1 #1).
  Three structured templates prompting for the information that
  makes a triage pass deterministic — minimal reproduction (bugs),
  problem statement and current workaround (feature requests),
  what-tried and what-expected (questions). Each template heads
  with a one-line pointer to `docs.activegraph.ai` and
  `CONTRIBUTING.md`. Pre-labels each issue (`bug`, `enhancement`,
  `question`) so issue-list filters work without manual triage.
- **`.github/ISSUE_TEMPLATE/config.yml`** (v1.0.5.post1 #1).
  Disables `blank_issues_enabled` and adds two `contact_links`
  pointing at the docs site and `CONTRIBUTING.md`. Forces every
  issue through one of the three templates.
- **`tests/test_license.py`** (v1.0.5.post1 #1). Standing Rule §2
  gate anchored on the contract boundary ("Active Graph is
  licensed under Apache 2.0 from v1.0.5.post1 forward"). Six
  assertions covering the five surfaces the claim binds: LICENSE
  carries the Apache canonical heading plus the §3 patent-grant
  section header; NOTICE carries the project name and copyright
  line; pyproject.toml's license field reads SPDX `Apache-2.0`;
  no `License ::` classifier remains in pyproject; README's
  license section names Apache 2.0 and points at LICENSE; sanity
  check on tomllib availability.

### Changed

- **`pyproject.toml`**: `[project].license` switches from
  `{ text = "MIT" }` to the SPDX string `"Apache-2.0"` per PEP
  639. Adds `[project].license-files = ["LICENSE", "NOTICE"]`
  declaring the carried metadata. Drops the
  `License :: OSI Approved :: MIT License` classifier — PEP 639
  forbids `License ::` classifiers when the SPDX form is used.
  Bumps `build-system.requires` from `setuptools>=68` to
  `setuptools>=77.0.3`, the minimum that supports PEP 639's SPDX
  license metadata.
- **`README.md`**: the `## License` section now reads "Active
  Graph is licensed under the Apache License 2.0" with pointers
  to LICENSE and NOTICE. The `## Contributing` section now
  points at `CONTRIBUTING.md` and names the issues-first policy
  as the pre-launch posture.
- **`activegraph/__init__.py`**: `__version__` tracks the bump
  to `"1.0.5.post1"` (the `test_version_sync` gate asserts it
  matches `pyproject.toml` byte-identically).
- **`tests/snapshots/errors/{internal_bug__pattern_unknown_op,
  internal_bug__graph_view_unknown_op,schema_version_mismatch}.txt`**:
  rebaselined the embedded version string from `1.0.5` to
  `1.0.5.post1` (these three snapshots render
  `activegraph.__version__` inline per the internal-bug context
  format and the schema-version-mismatch context format).

### CONTRACT amendments

- **`v1.0.5.post1` milestone added** with one numbered finding
  (`v1.0.5.post1 #1`) and a "deliberately does NOT touch"
  section. Single-finding milestone — same scope discipline as
  v1.0.1 / v1.0.2 / v1.0.2.post1 / v1.0.5. Appended as a new
  section between v1.0.5 and v1.1 per Standing Rule §1 (the
  v1.0.2.post1-via-v1.0.4-#6 retroactive archeology section is
  the precedent for how a `.postN` release lands).

### v1.1 backlog (filed in `v1.1-plan.md`)

- **CLA / DCO decision.** Apache 2.0 §5's implicit grant covers
  the contract today; if contribution volume grows past
  maintainer-review bandwidth or if enterprise legal desks
  request the ceremony, decide between a CLA (with a signing
  workflow) and a DCO (the `Signed-off-by:` discipline). v1.1
  work, not v1.0.5.post1.
- **`CODE_OF_CONDUCT.md` paired with a contact channel.**
  Contributor Covenant v2.1 is the standard text; the missing
  piece is the contact channel for reports. v1.1 picks both up
  together — shipping the document without the inbox would
  publish a hollow reporting commitment.
- **Relax the issues-first contribution policy.** Today's
  maintainer-only-code-PRs posture is explicitly pre-launch;
  v1.1 owns the decision to broaden it based on observed
  contribution patterns during v1.0.x's public window.

### Migration from v1.0.5

Forward-compatible. No code changes required. The runtime API,
public surface, doc-site content, and CI gates are unchanged.

```bash
pip install --upgrade activegraph==1.0.5.post1
```

PyPI metadata changes for v1.0.5.post1 onward:
`License-Expression: Apache-2.0` (PEP 639 SPDX form);
`License-File: LICENSE`, `License-File: NOTICE` (the carried
metadata files); the `License :: OSI Approved :: MIT License`
classifier is removed. Redistributors should update their
license-tracking metadata accordingly.

- Every v1.0.5 surface (`/llms.txt`, `/llms-full.txt`,
  `mkdocs-llmstxt` plugin, `tests/test_llms_txt.py`) stays
  byte-identical.
- Every v1.0.4 / v1.0.3 / v1.0.2.post1 / v1.0.2 / v1.0.1 / v1.0
  surface stays byte-identical.

## [v1.0.5] — 2026-05-19

AI-readable docs via `llms.txt` support. The v1.0.4 external
user-test surfaced that most evaluators of Active Graph in 2026 reach
the doc site through AI coding assistants (Claude Code, Cursor,
Replit) rather than browsers — and that mkdocs-rendered HTML wraps
content in navigation chrome that those agents spend tokens
unwrapping. The dominant convention for machine-readable docs is
[llms.txt](https://llmstxt.org/) (Howard, 2024), adopted by Stripe,
Vercel, Anthropic's docs, Nuxt, and many others.

v1.0.5 ships both files at the doc-site root, generated at build
time from the existing `docs/` markdown source. No abstraction
changes, no new runtime capability, no source-markdown changes. The
release is docs + build-infrastructure only.

### The single finding

  v1.0.5 #1 — /llms.txt (structured markdown index, ~96 lines) and
              /llms-full.txt (concatenated full content, ~110K
              tokens) at the docs.activegraph.ai site root,
              generated by the `mkdocs-llmstxt` plugin inside
              `mkdocs build`. Drift prevention is structural — no
              hand-maintained `llms.txt` lives in the repository,
              so both files cannot drift from the source markdown
              they are generated from.

### Added

- **`https://docs.activegraph.ai/llms.txt`** (v1.0.5 #1).
  Structured markdown index with `# Active Graph` H1, blockquote
  summary, and H2 sections (Quickstart, Concepts, Guides, Cookbook,
  Reference, Optional) listing every doc page with a one-sentence
  description per curated entry. Sized for AI tools that support
  llms.txt-aware fetching.
- **`https://docs.activegraph.ai/llms-full.txt`** (v1.0.5 #1).
  Concatenated markdown of every doc page in reading order, sized
  for large-context-window AI ingestion (~110K tokens, under the
  200K target). The "everything in one file" reference for AI
  tools that prefer comprehensive corpora.
- **`mkdocs-llmstxt>=0.2`** added to `pyproject.toml`'s `[docs]`
  extra. Same maintainer as `mkdocstrings`; the workflow
  auto-syncs because `.github/workflows/docs.yml` installs
  `.[docs]`.
- **`tests/test_llms_txt.py`** — Standing Rule §2 gate anchored
  on the v1.0.5 #1 contract claim. Six assertions: both files
  exist; `llms.txt` has the H1 + blockquote + at least 4 H2
  sections; `llms.txt` references the three nav-anchor pages
  named in the amendment (concepts/graph, quickstart, at least
  one cookbook page); `llms-full.txt` carries the H1 plus a
  distinctive marker phrase from the quickstart body. Marked
  `@pytest.mark.slow`; runs in the `docs.yml` workflow after
  `mkdocs build`.

### Changed

- **`mkdocs.yml`**: adds the `llmstxt` plugin block with
  `markdown_description`, `full_output: llms-full.txt`, and a
  `sections:` map mirroring the existing `nav:` 1:1.
- **`.github/workflows/docs.yml`**: adds the
  `pytest -m slow tests/test_llms_txt.py` verification step after
  `mkdocs build`. Build cost: ~5 seconds on top of the existing
  ~5-second mkdocs build.
- **`README.md`**: short note under `## Documentation` pointing
  AI agents at `/llms.txt` and `/llms-full.txt`. Frames the
  audience so human readers understand why both URLs exist
  alongside the rendered site rather than replacing it.

### CONTRACT amendments

- **v1.0.5 milestone added** with one numbered finding
  (`v1.0.5 #1`) and a "deliberately does NOT touch" section.
  Single-finding milestone — same scope discipline as v1.0.1 /
  v1.0.2 / v1.0.2.post1 (each release small, independently
  reviewable, and free of unrelated cleanup).

### v1.1 backlog (filed in `v1.1-plan.md`)

- **Content negotiation on the docs host.** A worker / edge
  function returning `text/markdown` for `Accept: text/markdown`
  requests, complementing the static `/llms.txt` and
  `/llms-full.txt` files. The static-files approach handles the
  index-and-bulk case; content negotiation handles the per-page
  case. Requires docs-host infrastructure GitHub Pages does not
  support natively.
- **Editorial doc-readability pass.** Front-load page summaries
  (so the first paragraph stands alone as the page abstract),
  tighten cross-references, normalize terminology across the
  per-error catalog. Open-ended editorial work, orthogonal to
  v1.0.5's mechanical file-generation scope.

### Migration from v1.0.4

Forward-compatible. No code changes required. The new files appear
automatically at the doc-site root on the next deploy.

```bash
pip install --upgrade activegraph==1.0.5
```

- Every v1.0.4 surface (`Graph.relations`, `Graph.get_relations`
  alias, the failure-model footers on the 10 per-error pages,
  CONTRACT review-overlay markers) stays byte-identical.
- Every v1.0.3 surface (Graph.objects, Runtime.errors,
  BehaviorFailure, LLMMessage.tool_calls, WARNING log) stays
  byte-identical.
- Every v1.0.2 / v1.0.2.post1 surface (LLMProvider.default_model,
  recognizes_model, both-binding-moments validation) stays
  byte-identical.

### Provider non-promises in v1.0.5

Inheriting v1.0.4 / v1.0.3 / v1.0.2 / v1.0.1 #5 (c). Specifically
unchanged in this release:

- `LLMProvider` Protocol stays at v1.0.2 #1's widened shape.
- The closed CONTRACT v0.6 #11 reason taxonomy is unchanged.
- The `behavior.failed` event payload, the WARNING log format,
  and the `BehaviorFailure` shape stay byte-identical to v1.0.3
  through v1.0.4.

## [v1.0.4] — 2026-05-19

Pre-launch foundation cleanup absorbing six small findings from the
post-v1.0.3 contract review (see `CONTRACT-review-findings.md` §5).
No abstraction changes, no new runtime capability, no new CI gate.
Three documentation corrections, one additive API method, one test
addition, one CONTRACT archeology restoration.

The release also operationalizes the two Standing Rules adopted by
the contract review banner: §1 (amendments append, never modify)
shaped every v1.0.4 commit; §2 (tests anchor on the contract
boundary, not the implementation's path) shaped the new tests for
#1 and #4.

### The six findings

  v1.0.4 #1 — graph.relations(source=, target=, type=) canonical
              filter API (mirrors v1.0.3 #1's graph.objects fix)
  v1.0.4 #2 — per-error-page footer pointing at failure-model.md's
              "Observing failures in caller code" (10 pages)
  v1.0.4 #3 — WARNING-log vs BehaviorFailure field-name divergence
              documented in failure-model.md
  v1.0.4 #4 — boundary-anchored test for _requeue_unfired
              zero-subscriber carve-out (Standing Rule §2 shape)
  v1.0.4 #5 — review-overlay markers at v0 #11, v0 #16, v0.8 #19
              for stale forward-pointer prose
  v1.0.4 #6 — appended ### v1.0.2.post1 section to CONTRACT under
              v1.0.2 #1, restoring archeology that v1.0.2.post1's
              in-place revision destroyed

### Added

- **`Graph.relations(source=None, target=None, type=None) -> list[Relation]`
  (v1.0.4 #1).** Canonical filter API on `Graph`. Three kwargs
  compose by AND; no-kwargs returns every relation; the
  source/target decomposition replaces the asymmetric
  `direction="outgoing"|"incoming"|"both"` axis on the alias.
  Eight filter combinations (each row of the table in CONTRACT
  v1.0.4 #1) are the contract claim; each is covered by a
  dedicated test in `tests/test_graph.py`.

  Implementation note: the method is a direct projection over
  `self._relations.values()`, not a wrapper over `get_relations`.
  The underlying loop is six lines, and routing through the alias
  would obscure the per-row contract claim that the tests anchor
  on. The duplication trade is small; the readability win is the
  point.

- **`tests/test_requeue_unfired.py::test_zero_subscriber_event_ids_are_absent_from_requeue_set_on_load`
  (v1.0.4 #4).** Boundary-anchored sibling to the existing
  `queue_depth == 0` test. Asserts directly on the requeue set
  (`rt._queue._q`) rather than the implementation's symptom.
  Locks the v0.5 #8 carve-out at the contract boundary the
  amendment names.

### Changed

- **`Graph.get_relations(...)` (v1.0.4 #1).** Kept as a
  backward-compatible alias for `Graph.relations`. No deprecation
  warning in v1.0.4; v1.1's Theme A (Graph/View harmonization)
  owns the deprecation decision.

### Documentation

- **`docs/concepts/graph.md`** (v1.0.4 #1). The long-broken
  line-43 reference `graph.relations(source=claim_id)` now
  resolves to the new method. Three canonical-form examples
  (`source=`, `target=`, `type=`) plus one line on the
  `get_relations` alias.
- **`docs/concepts/failure-model.md`** (v1.0.4 #3). Adds one
  paragraph in the "Observing failures in caller code" section
  naming the intentional field-name divergence between the
  WARNING log (`error_type` / `error_message` — v0.8 #6 schema)
  and `BehaviorFailure` (`exception_type` / `message` — Python
  convention). Values are identical; only the names differ.
- **`docs/reference/errors/*.md`** (v1.0.4 #2). Adds the fixed
  one-line footer pointing at
  `failure-model.md#observing-failures-in-caller-code` to the 10
  per-error pages whose error classes route through
  `behavior.failed` (identified by tracing each error class's
  raise sites through the runtime emission path, not by guessing
  from page titles). The 21 pages whose errors are raised at
  decoration / registration / setup / lookup time do NOT get the
  footer.

### CONTRACT amendments

- **v1.0.4 milestone added** with six amendments and a
  "deliberately does NOT touch" section.
- **`### v1.0.2.post1` subsection appended** as §(e)-equivalent
  under v1.0.2 #1 (v1.0.4 #6). Documents the validation-boundary
  correction (lazy-at-first-run → both binding moments), the
  `_live.py` `weakref.WeakSet` mechanism, and cites
  `tests/test_llm_default_model.py` Section (g) as the canonical
  Standing Rule §2 model.
- **Top-of-v1.0.2-#1 breadcrumb** updated in place to point at
  the now-existing post1 section (the single in-place edit
  Standing Rule §1 permits in v1.0.4, explicitly authorized by
  the contract review).
- **Three review-overlay markers added in place** at v0 #11,
  v0 #16, v0.8 #19 (v1.0.4 #5). Original prose preserved
  verbatim; each overlay is bracketed `[review overlay
  2026-05-19: …]` so the layer boundary is explicit and
  greppable by date.

### v1.1 backlog (filed in `v1.1-plan.md`)

- **C-3 — Lock failure-routing for eval-time pattern failures.**
  Surfaced during v1.0.4 #2's audit. Most dispatch-time errors
  route through `behavior.failed`; `ReplayDivergenceError` and
  `UnsupportedPatternError` eval-time raises deliberately escape.
  v1.1 should either keep the asymmetry and document the
  carve-out criterion in CONTRACT, or route every dispatch-time
  error uniformly. The current state is neither documented nor
  locked.
- **I-4 — Cross-link `replay-divergence-error.md` to
  replay/fixture documentation.** The standard v1.0.4 #2 footer
  doesn't apply to this page because the error deliberately
  escapes rather than emitting `behavior.failed`. A different
  cross-link is needed; phrasing depends on how C-3 resolves.

### Migration from v1.0.3

Forward-compatible. No code changes required.

```bash
pip install --upgrade activegraph==1.0.4
```

- `graph.get_relations(object_id=, type=, direction=)` keeps
  working byte-identically; new code uses
  `graph.relations(source=, target=, type=)`.
- All v1.0.3 surfaces (Graph.objects, Runtime.errors,
  BehaviorFailure, LLMMessage.tool_calls, WARNING log) are
  unchanged.
- All v1.0.2 / v1.0.2.post1 surfaces (LLMProvider.default_model,
  recognizes_model, both-binding-moments validation) are
  unchanged.

### Provider non-promises in v1.0.4

Inheriting v1.0.3 / v1.0.2 / v1.0.1 #5 (c). Specifically
unchanged in this release:

- `LLMProvider` Protocol stays at v1.0.2 #1's widened shape;
  no further additions.
- The closed CONTRACT v0.6 #11 reason taxonomy is unchanged.
- The `behavior.failed` event payload, the WARNING log format,
  and the `BehaviorFailure` shape stay byte-identical to v1.0.3.

## [v1.0.3] — 2026-05-19

Comprehensive response to two user-test reports. Four findings span
the framework's user-facing API surface, the largest single release
since v1.0.1. The framing — patch release on the adoption-surface
milestone, one commit per finding for independent review — matches
v1.0.1 / v1.0.2. The two prior user-test findings carried forward
into this release (`output_schema=` UX and silent `behavior.failed`
UX) are addressed in #2 and #3.

### The four findings

  v1.0.3 #1 — graph.objects(type=...) as canonical query API
  v1.0.3 #2 — @llm_behavior(output_schema=) strict-validates at
              decoration time (dict-form filed as v1.1 candidate)
  v1.0.3 #3 — WARNING log + Runtime.errors property for behavior.failed
  v1.0.3 #4 — multi-turn tool-use messages carry full content blocks

### Added

- **`Graph.objects(type=..., where=...)` (v1.0.3 #1).** Canonical
  query API on `Graph`, mirroring `View.objects(type=...)` so call
  sites read the same inside and outside behaviors. External users
  who reached for the natural form previously hit `AttributeError`;
  the docs even showed the call. `Graph.query(object_type=...)`
  stays as a backward-compatible alias — no deprecation in v1.0.3.
- **`Runtime.errors -> list[BehaviorFailure]` (v1.0.3 #3).** A
  read-only property projecting `behavior.failed` events from the
  graph's event log into structured named-tuples. Five fields per
  failure (`behavior`, `event_id`, `reason`, `exception_type`,
  `message`) plus `failed_event_id` for callers that want the full
  payload. The events stay the source of truth; the property is a
  view.
- **`BehaviorFailure` (v1.0.3 #3).** New `NamedTuple` exported from
  the top-level `activegraph` namespace. Distinct from Python's
  builtin `RuntimeError` — the name was chosen so it doesn't shadow.
- **`LLMMessage.tool_calls` (v1.0.3 #4).** Additive field
  (`Optional[tuple[ToolCall, ...]]`, default `None`) carrying the
  originating `ToolCall` objects on assistant messages that
  triggered tool_use. The provider adapter reconstructs the
  wire-format content blocks from this field.
- **`doc_url` in the structured log schema (v1.0.3 #3).** The JSON
  log formatter now emits `doc_url` (when present). The
  `behavior.failed` WARNING log carries the URL pointing at the
  reason's class-level documentation page so operators tailing
  logs can click through.

### Changed

- **`@llm_behavior(output_schema=)` strict-validates at decoration
  time (v1.0.3 #2).** Passing anything that isn't `None` or a
  Pydantic `BaseModel` subclass raises `TypeError` from the
  decorator with a structured message that names the actual type
  passed and inlines a copy-pasteable code example of the correct
  form. Previously, a JSON-schema dict raised a `TypeError`
  internally and the runtime caught it as a generic exception,
  producing a `behavior.failed` event with reason
  `llm.schema_violation` and no diagnostic naming the cause.
  Dict-form `output_schema=` support is a v1.1 candidate.
- **`Runtime._emit_behavior_failed` emits a WARNING log line
  (v1.0.3 #3).** Every `behavior.failed` emission produces exactly
  one log line at `WARNING` level on the `activegraph.runtime`
  logger. The line carries `behavior`, `event_id`, `reason`,
  `error_type`, `error_message`, and `doc_url`. The function- and
  relation-behavior exception handlers now route through this
  centralized emitter rather than calling `_emit_lifecycle`
  directly, removing a duplicate `ERROR` log on the function path.
  Users opt out via standard Python logging configuration.
- **Multi-turn tool-use message construction (v1.0.3 #4).** When
  the LLM returns `tool_use` blocks, the runtime appends an
  `LLMMessage(role="assistant", content=raw_text or "",
  tool_calls=tuple(response_tool_calls))` to the message history,
  not just the raw text. The Anthropic provider adapter's
  `_message_to_anthropic` reconstructs the wire-format content
  blocks (text + tool_use) on the way out. Single-turn flows and
  zero-tool assistant messages keep their byte-identical wire
  serialization. Hashing-stability invariant: `LLMMessage.to_dict()`
  only emits the `tool_calls` key when non-None, so existing
  single-turn fixture prompt hashes are unchanged.

### Fixed

- **First user-test report, finding A** —
  `graph.objects(type="x")` `AttributeError`'d when called on a
  `Graph` instance. Users who'd been writing
  `ctx.view.objects(type="x")` inside behaviors hit the gap
  immediately when trying the equivalent outside a behavior.
  Fixed by adding `Graph.objects` as the canonical form. See
  "Added: `Graph.objects(...)`".
- **First user-test report, finding B** —
  `@llm_behavior(output_schema={"type": "object", ...})` silently
  produced zero results: the dict raised `TypeError` internally,
  the runtime emitted `behavior.failed` with
  `reason="llm.schema_violation"`, and the diagnostic carried no
  hint that the user had passed a dict instead of a Pydantic
  class. Fixed by failing at the `@llm_behavior(...)` line with a
  structured message + code example. See "Changed:
  `@llm_behavior(output_schema=)` strict-validates".
- **First user-test report, finding C** — `runtime.run_goal()`
  returned cleanly with zero results when behaviors failed,
  leaving users no signal short of inspecting `graph._events`.
  Fixed by emitting a WARNING log line and exposing
  `Runtime.errors`. See "Added: `Runtime.errors`" and "Changed:
  `_emit_behavior_failed` emits a WARNING log line".
- **Second user-test report, finding D** — multi-turn tool-use
  exchanges through the Vertex AI proxy returned HTTP 400 because
  the runtime appended only `raw_text` to the message history after
  a tool_use turn, dropping the tool_use blocks Anthropic's spec
  requires for matching subsequent tool_result blocks. Fixed by
  carrying `ToolCall` objects on the assistant message and
  reconstructing the content blocks in `_message_to_anthropic`.
  See "Added: `LLMMessage.tool_calls`" and "Changed: multi-turn
  tool-use message construction".

### Examples

No example changes in this release. The bundled Diligence pack and
the BabyAGI example continue to run unchanged against the new
surfaces; their tool-using behaviors exercise v1.0.3 #4's fix
through the existing fixture path.

### Documentation

- **`docs/concepts/graph.md`** already showed
  `graph.objects(type="claim")` as the canonical form; v1.0.3 #1
  makes that documentation match the implementation. No prose
  change required.
- **`docs/concepts/failure-model.md`** gains an "Observing
  failures in caller code" section documenting the WARNING log
  line and the `Runtime.errors` property as the two user-facing
  surfaces (v1.0.3 #3).
- **`docs/reference/`** picks up the new `BehaviorFailure`
  NamedTuple shape and the `Runtime.errors` property.

### Migration from v1.0.2.post1

Forward-compatible. No code changes required.

```bash
pip install --upgrade activegraph==1.0.3
```

- `graph.query(object_type=...)` keeps working byte-identically;
  new code uses `graph.objects(type=...)`.
- `@llm_behavior(output_schema=SomeBaseModel)` keeps working
  byte-identically. Callers passing a JSON-schema dict will see a
  `TypeError` at the decorator line instead of a silent
  `behavior.failed` at first LLM call — the error names what they
  passed and shows the correct form.
- `runtime.run_goal()` keeps working byte-identically; the new
  WARNING log line is opt-out via stdlib logging configuration.
  Code that inspected `graph._events` for `behavior.failed`
  continues to work; the new `Runtime.errors` property is the
  ergonomic alternative.
- Multi-turn tool-use exchanges send extra content blocks on the
  wire now. Direct Anthropic API access keeps working;
  Vertex-AI-proxy users stop hitting HTTP 400.

### Provider non-promises in v1.0.3

Inheriting v1.0.2 / v1.0.1 #5 (c). Specifically unchanged:

- `LLMProvider.complete()` / `estimate_cost()` / `count_tokens()`
  signatures stay locked at v0.6 #3 / v0.7. v1.0.2's additive
  members (`default_model`, `recognizes_model`) are unchanged.
- The closed CONTRACT v0.6 #11 reason taxonomy is unchanged.
- OpenAI tool-use translation stays a v1.1 candidate per
  v1.0.1 #5 (c) clause 2.
- The pack format, the exception hierarchy, and the failure model
  are unchanged.

## [v1.0.2.post1] — 2026-05-19

Post-release fix to v1.0.2. The CONTRACT amendment landed in v1.0.2
promised registration-time validation; an external spot-check
discovered the implementation was firing the validation lazily, at
first `run_goal()` / `run_until_idle()` / `run_until()` via
`_ensure_registry()` rather than at registration time. The
validation logic and error message were correct — the boundary was
wrong.

This post-release moves the validation to **both binding moments**
so cross-provider model mismatches fail fast at setup time:

1. **`Runtime(graph, llm_provider=...)` construction** — runs the
   bulk validation pass against whatever is already registered
   (global registry, explicit `behaviors=[...]`, or pack-loaded).
2. **`register()` / `@llm_behavior` decoration** — when one or more
   Runtimes are alive, the freshly-registered behavior is checked
   against each live Runtime's provider via a `weakref.WeakSet`.
   The WeakSet auto-cleans on GC; no `Runtime.close()` is added.

The lazy path inside `_ensure_registry()` stays in place as a
defensive double-check for code paths that bypass both binding
moments (currently: pack behaviors registered after Runtime
construction via `load_pack`). Pack-load-time validation is filed
as a v1.1 candidate if friction surfaces.

The CONTRACT v1.0.2 #1 (b) wording is clarified to match — see
that section for the locked decision.

### Changed

- **Validation boundary corrected.** No public-API change; the
  error message is byte-identical to v1.0.2. The difference is
  *when* it fires: at `Runtime(...)` construction or at
  `@llm_behavior` / `register()` time, instead of at first
  `run_goal()`.
- **`Runtime.__init__` order updated.** Bulk validation runs
  before the Runtime self-registers in the live-set, so a Runtime
  whose construction fails validation stays out of the WeakSet.
  This prevents pytest exception-traceback strong-refs on
  failed-construction `self` from polluting subsequent
  `@llm_behavior` validation passes during a test session. In
  production it's a no-op (failed-construction Runtimes go out of
  scope and are GC'd promptly anyway), but the invariant — "only
  successfully-constructed Runtimes participate in validation" —
  is worth keeping clean.
- **`tests/conftest.py`** clears the live-Runtime WeakSet in the
  autouse `_isolate_registry` fixture for the same pytest-pollution
  reason. The WeakSet auto-cleans on GC in production; only test
  isolation needs the explicit clear.

### Added

- **`activegraph/runtime/_live.py`** — new module owning the live-
  Runtime WeakSet, the `track_runtime()` hook, and the
  single-behavior cross-provider validator that both binding
  moments invoke. The validator is factored out of v1.0.2's
  `_resolve_and_validate_llm_models` so the check itself lives in
  one place; the bulk function and the new decorator-path call
  site both delegate.

### Fixed

- **External spot-check finding** — `Runtime(graph,
  llm_provider=...)` no longer returns successfully when the
  registry contains a cross-provider model mismatch. The
  `@llm_behavior` decorator no longer adds a conflicting behavior
  to the registry when a Runtime with an incompatible provider
  is already alive. Same diagnostic message; earlier boundary.

### Migration from v1.0.2

No code changes required. The error fires at an earlier point in
the program's execution — what used to surface at `rt.run_goal(...)`
now surfaces at `Runtime(...)` or at `@llm_behavior` decoration.
Code that was already catching `InvalidRuntimeConfiguration`
around `run_goal` should move the `try`/`except` to the relevant
binding moment, or wrap the whole setup block.

## [v1.0.2] — 2026-05-19

Patch release addressing the most urgent of three findings from the
second-round external user-test. The framing — patch release based
on user-test findings — matches v1.0.1's; the scope is narrower
(one finding, not four, plus no provider-expansion work). The
other two findings need design consideration and are tracked for
v1.0.3 / v1.1, not folded into this release.

### The finding (v1.0.1 #5 credibility hit)

v1.0.1 #5 shipped `OpenAIProvider` and locked the provider-
commitment contract: same Protocol surface, swap one for the other
without reshaping any `@llm_behavior`. The second-round user-test
exercised exactly that swap and surfaced a silent default-model
mismatch: `@llm_behavior(...)` without an explicit `model=`
inherited the decorator's hardcoded default `"claude-sonnet-4-5"`
— an Anthropic-family name. With `Runtime(graph,
llm_provider=OpenAIProvider())`, that name went verbatim to
OpenAI's `chat.completions.create` and produced an HTTP 404 with
no hint that the cross-provider mismatch was the cause. The
`behavior.failed` event carried the provider's verbatim 404 prose;
diagnosis required inspecting the decorator, tracing the default,
and recognizing the model-family conflict.

This directly undermined v1.0.1's provider-agnostic claim. v1.0.2
makes the default provider-aware and validates explicit model
names at registration time.

### Added

- **`LLMProvider.default_model` attribute (additive Protocol
  widening, CONTRACT v1.0.2 #1 (a)).** Each shipped provider
  declares a default model:

  | Provider | `default_model` |
  | --- | --- |
  | `AnthropicProvider` | `"claude-sonnet-4-5"` |
  | `OpenAIProvider` | `"gpt-4o-mini"` |

  `@llm_behavior(...)` with no `model=` argument now resolves to
  the configured provider's `default_model` at registration time,
  via `Runtime(graph, llm_provider=...)._ensure_registry()`. The
  resolved name is stamped onto the `LLMBehavior` instance so
  `behavior.build_prompt(...)` sees the concrete model in its
  hash inputs.
- **`LLMProvider.recognizes_model(name) -> bool` method (additive,
  CONTRACT v1.0.2 #1 (b)).** Returns True when `name` belongs to
  a model family the provider serves. Shipped providers:
  `AnthropicProvider` recognizes `claude-*`; `OpenAIProvider`
  recognizes `gpt-*`, `o1-*`, `o3-*`, `o4-*`.

### Changed

- **`@llm_behavior(model=...)` default changed from
  `"claude-sonnet-4-5"` to `None`.** Existing call sites passing
  `model="..."` explicitly stay byte-identical. Call sites that
  omitted `model=` previously inherited `"claude-sonnet-4-5"` via
  the decorator default; they now inherit the same string when
  the configured provider is `AnthropicProvider` (whose
  `default_model` is `"claude-sonnet-4-5"`), and the *provider-
  appropriate* default (`"gpt-4o-mini"`) when the configured
  provider is `OpenAIProvider`. Custom providers that don't
  declare a `default_model` retain the v1.0.1 hardcoded fallback
  for backward compat. CONTRACT v1.0.2 #1 (c).
- **`LLMBehavior.model` field type changed from `str` to
  `Optional[str]`.** v1.0.1 instances pickle/load cleanly (string
  values still load); freshly-decorated v1.0.2 behaviors carry
  `None` until a Runtime resolves a provider default. CONTRACT
  v1.0.2 #1 (c).
- **Registration-time cross-provider validation.** When a behavior
  pins `model=` explicitly and the configured provider doesn't
  recognize the name, the runtime checks each shipped provider's
  `recognizes_model()` and raises
  `InvalidRuntimeConfiguration` if a *different* shipped provider
  claims the name. The error is structured per the v1.0 format —
  `what_failed` / `why` / `how_to_fix` — and names both providers
  plus the way out (swap the provider, or use the configured
  provider's default). Permissive by default: unknown names
  (custom deployments, fine-tunes like `ft:gpt-4o-mini:org::id`)
  pass through silently. CONTRACT v1.0.2 #1 (b).
- **`docs/reference/llm-providers.md`** gains a "Default model
  resolution" section, a "Cross-provider model-name validation"
  section, and updated rows for `default_model` and recognized
  prefixes in the side-by-side table. The "Writing a custom
  provider" example now shows the optional `default_model` +
  `recognizes_model` members and notes they are additive.

### Fixed

- **Second external user-test, finding 1** —
  `@llm_behavior` with no `model=` argument silently used an
  Anthropic-family default, producing HTTP 404 at first LLM call
  when the configured provider was `OpenAIProvider`. The
  diagnostic message on `behavior.failed` did not name the cause.
  See "Added: `LLMProvider.default_model`" and "Changed:
  registration-time cross-provider validation" above.

### Examples

- **`examples/babyagi.py`** simplified to drop its per-provider
  model table. The `@llm_behavior` definitions omit `model=` and
  let `Runtime`'s provider resolution pick the right default.
  Switching `--provider` between `anthropic` and `openai` is a
  one-line change in the example; nothing else needs to change.

### Provider non-promises in v1.0.2

Inheriting the v1.0.1 #5 (c) clauses. Specifically *unchanged* in
v1.0.2:

- `LLMProvider.complete()` / `estimate_cost()` / `count_tokens()`
  signatures stay locked at v0.6 #3 / v0.7. v1.0.2 widens the
  Protocol additively with two members; it does not reshape the
  three core methods.
- The closed CONTRACT v0.6 #11 reason taxonomy is unchanged. The
  cross-provider mismatch raises a `ConfigurationError` subclass
  at registration time, not a new behavior-failure reason code.
- The v1.0.1 #2 prompt-assembly shape is unchanged. Same schema
  + example instance + "instance not schema" language.

### Migration from v1.0.1

Additive. Forward-compatible:

```bash
pip install --upgrade activegraph==1.0.2
```

- `@llm_behavior(model="...")` call sites that pinned an explicit
  string keep working byte-identically.
- `@llm_behavior(...)` call sites that omitted `model=` now
  inherit the configured provider's `default_model`. For
  `AnthropicProvider`, that's the same `"claude-sonnet-4-5"` the
  v1.0.1 decorator default produced. For `OpenAIProvider`, the
  default changes from the v1.0.1 silent-Anthropic-name to
  `"gpt-4o-mini"`, fixing the v1.0.2 finding.
- Custom providers that don't declare `default_model` continue to
  use the v1.0.1 hardcoded fallback. Custom providers that want
  the v1.0.2 default-resolution behavior add a
  `default_model: str = "..."` class attribute (and optionally a
  `recognizes_model()` method to participate in cross-provider
  validation).
- `LLMProvider` Protocol gains two additive members. Existing
  custom-provider classes that don't implement them still pass
  `isinstance(p, LLMProvider)` checks at the three core methods.

## [v1.0.1] — 2026-05-19

The first-external-user-test patch plus the OpenAI provider
expansion. v1.0 final shipped on 2026-05-18; the first developer
outside the maintainer's loop ran the install / quickstart /
tutorial path on the day-of, and three small UX findings surfaced
before v1.0.1 publish. All three fit the "X is confusing" shape on
HANDOFF.md's user-test heuristic (none "X doesn't compose with Y
the way I expected" — architectural shape held).

v1.0.1 also closes an implicit adoption-surface gap the user-test
didn't surface but readers feel: the framework shipped a single
concrete `LLMProvider` (`AnthropicProvider`), making the
provider-agnostic claim read as theoretical. v1.0.1 #5 ships
`OpenAIProvider` with surface parity and locks in the
provider-commitment contract.

No CONTRACT amendments to v1.0's own decisions, no public-API
renames, no new runtime capability. CONTRACT v1.0.1 records the
four user-test fixes plus the provider-expansion decision; this
entry is the shipping changelog.

### Added

- **`activegraph.register(behavior_obj)`** — public function for
  appending an already-constructed behavior to the global registry.
  Pairs with `clear_registry()` for multi-run scripts that capture
  the registry once and re-register per run, replacing the v1.0
  pattern of reaching into the private
  `activegraph.behaviors.decorators._REGISTRY` list. Validates the
  argument is a `Behavior` / `RelationBehavior` / `LLMBehavior`
  instance and raises `TypeError` otherwise. CONTRACT v1.0.1 #1.
- **`docs/cookbook/multi-run-scripts.md`** — new cookbook recipe
  covering the capture-once-re-register-per-run pattern, when to
  use it (hypothesis sweeps, A/B comparisons inside one process,
  batch jobs that want per-input graph isolation without per-input
  process startup), and when not (single-runtime scripts don't need
  any of this). Wired into the mkdocs nav under the Cookbook
  section. CONTRACT v1.0.1 #1.
- **`activegraph.llm.prompt.example_instance_from_schema`** — new
  helper that walks a JSON Schema and produces a deterministic
  placeholder instance. Used by `build_system_prompt` to render an
  example alongside the schema in the LLM system prompt; exported
  for tests and for prompt-debugging tools. CONTRACT v1.0.1 #2.

### Changed

- **`@llm_behavior(output_schema=...)` system prompt now embeds an
  example instance and explicit "instance, not schema" language.**
  The first external user-test surfaced a failure mode v1.0's
  prompt-assembly didn't anticipate: some models echo the JSON
  Schema definition back as their response instead of an instance
  that conforms to it. The framework refused with
  `llm.schema_violation`, the user had to reverse-engineer the
  cause from the raw response. v1.0.1 changes the system-prompt
  schema block to three parts — the schema (unchanged), a
  synthesized example instance, and explicit "Return an INSTANCE
  that conforms to this schema, NOT the schema itself" language.
  `build_instruction` (the user-message task sentence) also gains
  "NOT the schema definition itself" so the framing appears in
  two places. The example generator handles `type`, `properties`,
  `items`, `enum`, `const`, `anyOf`/`oneOf` (picks the non-null
  variant), and `$ref` to `$defs`/`definitions`; unrecognized
  shapes fall back to `null`. The synthesized example is
  deterministic across runs so the prompt-hash cache key stays
  stable. CONTRACT v1.0.1 #2. See the expanded
  [`llm-behavior-error`](https://docs.activegraph.ai/errors/llm-behavior-error/)
  reference page for the failure mode and the `prompt_template=`
  override pattern when the auto-derived example isn't useful.
- **`SQLiteEventStore()` constructor error points at the higher-
  level `Runtime(graph, persist_to=...)` API.** v1.0 raised a bare
  Python `TypeError: missing 1 required positional argument:
  'run_id'`; the user-test reader had to first look up "what is a
  run_id" before they could decide how to recover. v1.0.1
  hand-raises a TypeError with a structured hint:

  ```
  SQLiteEventStore requires a run_id. For most cases, use
  Runtime(graph, persist_to='path/to/trace.sqlite') instead,
  which handles run_id automatically. If you need a per-run
  handle (migration, conformance test, trace inspection), pass
  both explicitly: SQLiteEventStore('path/to/trace.sqlite',
  run_id='run_...').
  ```

  The signature change (`Optional[str] = None` for both args) is
  internal — every existing caller passes both args positionally
  or by keyword. CONTRACT v1.0.1 #3.
- **`clear_registry()` returns the cleared list.** v1.0 returned
  `None`; v1.0.1 returns `list[Behavior | RelationBehavior]` in
  registration order. Callers that ignored the return value still
  work unchanged. The shape pairs with the new `register()` for
  the multi-run pattern. CONTRACT v1.0.1 #1.
- **`@llm_behavior` decorator docstring names what each
  `prompt_template=` placeholder contains.** v1.0 documented the
  four placeholders by name (`{system}`, `{view}`, `{event}`,
  `{instruction}`) but didn't say what each one rendered to. The
  v1.0.1 doc-site entry for the schema-echo failure mode points
  readers at `prompt_template=` as a fallback for schemas the
  auto-example can't render usefully, so the decorator docstring
  grows a four-bullet list naming the content of each placeholder.
  Concrete enough to compose a custom template without first
  opening `activegraph/llm/prompt.py`. CONTRACT v1.0.1 #4.

### Fixed

- **First external user-test, finding 1** — multi-run scripts had
  no public-API path to re-populate the registry after
  `clear_registry()`. See "Added: `activegraph.register`" and
  "Changed: `clear_registry()` returns the cleared list" above.
- **First external user-test, finding 2** — models occasionally
  returned the JSON Schema definition as their response instead of
  an instance, triggering `llm.schema_violation`. See "Changed:
  `@llm_behavior(output_schema=...)` system prompt now embeds an
  example instance" above.
- **First external user-test, finding 3** — `SQLiteEventStore()`
  with missing args produced a bare Python `TypeError` instead of
  hinting at the higher-level `persist_to=` API. See "Changed:
  `SQLiteEventStore()` constructor error" above.

### Provider expansion

- **`activegraph.llm.OpenAIProvider`** — second concrete
  `LLMProvider` with surface parity to `AnthropicProvider`. Same
  three Protocol methods (`complete`, `estimate_cost`,
  `count_tokens`), same lazy-SDK + env-var loading shape, same
  family-prefix pricing table, same structured-output path through
  the framework's instruction-based prompt assembly. A runtime
  swapping `AnthropicProvider()` for `OpenAIProvider()` doesn't
  reshape any `@llm_behavior` definition. CONTRACT v1.0.1 #5.
- **`activegraph.llm.parsing.parse_structured_response`** —
  JSON-extraction-then-Pydantic-validate helper extracted from
  `AnthropicProvider`. Both shipped providers (and any future
  provider that uses the framework's instruction-based path)
  import it directly, producing byte-identical `llm.parse_error`
  and `llm.schema_violation` reason codes for byte-identical
  responses. The extraction preserved Anthropic's behavior
  exactly; all 9 existing `test_llm_anthropic.py` tests pass
  unchanged. CONTRACT v1.0.1 #5.
- **`pyproject.toml` extras follow a three-pattern shape.** `[llm]`
  pulls every shipped provider's SDK (`anthropic>=0.40`,
  `openai>=1.0`, `tiktoken>=0.7`). `[anthropic]` and `[openai]`
  aliases install one provider at a time for cost-conscious
  production deployments. `[all]` rolls up everything from `[llm]`
  plus persistence and metrics extras. CONTRACT v1.0.1 #5 (b).
- **`docs/reference/llm-providers.md`** — new reference page
  documenting both providers side-by-side: install commands, API
  key env vars, the symmetric Protocol surface, the asymmetric
  details (`count_tokens` server-side vs client-side, tool-use
  support gap, native structured-output mode deferral), and a
  "writing a custom provider" section pointing at
  `parse_structured_response` for error-semantics parity. Wired
  into the mkdocs nav under Reference. CONTRACT v1.0.1 #5.

### Provider non-promises in v1.0.1 (per CONTRACT v1.0.1 #5 (c))

Documented as contract clauses rather than discovered as user
friction later — same discipline as v1.0's honesty section.

- **Token counting is provider-dependent.** Anthropic uses
  `messages.count_tokens` server-side; OpenAI uses `tiktoken`
  client-side when available and a `chars / 4` heuristic when
  not, with a one-time debug log on first heuristic call.
  Operators gating on `budget.max_cost_usd` should install
  `tiktoken` (via `[openai]` or `[llm]`) for accurate accounting.
- **Tool use is Anthropic-only in v1.0.1.** `OpenAIProvider`
  accepts the `tools=` kwarg for Protocol compatibility but
  raises `LLMBehaviorError(reason="llm.network_error")` with a
  v1.1 pointer when the list is non-empty. Tool-shape translation
  in `Tool.to_definition()` is filed under v1.1 #7-and-beyond.
- **Native structured-output modes are v1.1 candidates.** Both
  providers use the instruction-based path that v1.0.1 #2's
  example-instance work feeds. OpenAI's
  `response_format={"type":"json_schema",...}` and analogous
  future modes stay v1.1 — they diverge providers' latency
  profiles, cache-key semantics, and error paths in ways that
  warrant their own decision.
- **No new reason codes.** The closed CONTRACT v0.6 #11 taxonomy
  is unchanged. OpenAI auth failures land in `llm.network_error`
  with the exception message preserved verbatim, same as
  Anthropic for the same failure mode.

### Examples

- **`examples/babyagi.py`** with companion
  `examples/babyagi/README.md` — BabyAGI's autonomous agent loop
  (Nakajima 2023) rebuilt as three reactive behaviors over a
  shared graph. The minimal-loop counterpart to the Diligence
  pack's domain-rich example: same conceptual lineage as the
  framework's launch essays, runnable end-to-end against either
  provider, traces to `traces/babyagi-<timestamp>.sqlite`. The
  v1.0.1 public `register()` API replaces v1.0's `_REGISTRY`
  workaround. A `--provider {anthropic,openai}` CLI flag exercises
  the new symmetric surface — same example, same loop, swap the
  provider with one argument.

### Migration from v1.0

Additive. The changes are forward-compatible:

```bash
pip install --upgrade activegraph==1.0.1
```

- `clear_registry()` now returns a list; v1.0 callers that ignored
  the return continue to work unchanged.
- `register()` is new; nothing existing calls it.
- `SQLiteEventStore("/path", run_id="r")` (the v1.0 supported
  shape) keeps working; the new error fires only on missing-arg
  call sites, which are by construction unmigrated.
- LLM prompt-hash values change because the system prompt got
  longer. No on-disk LLM fixtures exist in the framework's tests,
  so no replay-divergence risk for in-tree code; user code that
  saved fixtures from a v1.0 live run will see a fresh
  `llm.fixture_missing` against v1.0.1 prompts and needs a
  re-record pass. Same shape as any v0.6+ prompt-assembly change;
  see [`llm-behavior-error`](https://docs.activegraph.ai/errors/llm-behavior-error/)'s
  re-record recipe.
- `OpenAIProvider` is new; install via
  `pip install "activegraph[openai]"` or
  `pip install "activegraph[llm]"`. Existing `[llm]` extra users
  pick up `openai` and `tiktoken` automatically on upgrade
  alongside the existing `anthropic` SDK.

## [v1.0] — 2026-05-18

v1.0 final. The lighter-weight verification pass against v1.0-rc3
ran the same seven-check shape as the rc2 lighter pass and produced
six clean passes plus one partial finding on Check 6 (the tutorial's
step 7 fork-and-diff snippet undersold its own output). The B2
fix's core promise — fork-and-diff runs without an API key against
bundled fixtures — held intact. Scope = v1.0-rc3 + the Check 6
tutorial fix + a README "Concepts at a glance" section bridging the
README and the doc site for evaluators.

No runtime capability changes; no public-API renames; no CONTRACT
amendment.

### Changed

- **Tutorial step 7 fork-and-diff snippet** emits its own next-step
  guidance instead of a bare `forked: <run-id>` line. The shipped
  rc3 snippet ran cleanly end-to-end but its terminal output was
  one anticlimactic line, leaving a first-time reader to scroll
  past it and notice the "Then diff:" CLI block on their own. The
  snippet now prints the exact `activegraph diff` command for the
  fork it just created (parameterized from the same constants
  defined at the top of the snippet), and the transition prose
  before the CLI block names it explicitly. The diff itself
  produces 61 divergent objects and 49 divergent relations — a
  substantive output that the rc3 snippet was hiding behind a
  prose-only handoff. The voice test: a first-time reader running
  `python fork_and_diff.py` cold now sees both the fork creation
  and the exact next command, with no ambiguity about whether they
  need to do something else.
- **README adds a "Concepts at a glance" section** between
  "What you get" and "A small example." Twelve primitives — graph,
  events, behaviors, relations, patches, views, frames, policies,
  patterns, replay, forking, failure model — each with a one-line
  "what + why" and a link to the concept page. The section is
  evaluator-facing: it lets a reader scan the framework's
  conceptual primitives from the GitHub repo page without first
  clicking through to the doc site. Mirrors the
  `docs/concepts/*.md` navigation 1:1; complements but does not
  duplicate "What you get" (which is feature-oriented; the new
  section is primitive-oriented).
- **Deploy-verification workflow gains a `pull_request` trigger.**
  Discovered at v1.0 final merge time: the gate (CONTRACT v1.1 #9)
  ran on push-to-main and cron but not on PR events, so the
  required-status-check rule on branch protection had nothing to
  match against on the PR. A `workflow_dispatch` run reported under
  a different context name and didn't satisfy the rule. Adding
  `pull_request:` to the workflow's `on:` triggers lets the check
  report alongside the other CI gates on every PR; the existing
  push and cron triggers continue to run unchanged. Config-only
  change; the gate's logic is untouched.

### Fixed

- **Check 6 user-test finding** (rc3 lighter pass). See the
  Tutorial step 7 entry above. The runtime artifact didn't change;
  the tutorial prose and the snippet's terminal output changed.

### Migration from v1.0-rc3

Additive. No code changes required. Existing v1.0-rc3 installs
should:

```bash
pip install --upgrade activegraph==1.0.0
```

## [v1.0-rc3 amendment — docs-build fix] — 2026-05-18

Post-rc3-merge, pre-rc3-tag follow-on. The v1.1 #9 deploy-
verification gate's first run on `main` after the rc3 merge caught
a silent failure that had been dwelling since the doc-site phase:
`mkdocs.yml` declared the `mkdocstrings` plugin for API-reference
auto-generation (added in commit `b533dd4` during the doc-site
phase), but `.github/workflows/docs.yml`'s hardcoded install step
only pulled `mkdocs` + `mkdocs-material`. Every docs build through
every doc-site PR failed with `Config value 'plugins': The
"mkdocstrings" plugin is not installed`, the deploy job never had
an artifact to upload, and the `has_pages: false` finding from the
rc3 #2 investigation was an effect of this — not just the
externally-owned Pages-enable step. No version bump; this is a
follow-on to v1.0-rc3, not a new rc.

### The gate did its job

CONTRACT v1.1 #9 (deploy-verification) was designed to catch the
class "internal CI ships green, the external artifact is broken,
nobody notices for months." On its first real run, it caught the
build failure that v1.0 had been carrying silently since the doc-
site phase. The gate's red signal on `main` post-rc3-merge was
correctly the discipline call to action, not noise.

### Fixed

- **`mkdocstrings[python]` now installed by the docs workflow.**
  Root cause: hardcoded `pip install mkdocs mkdocs-material` in
  `.github/workflows/docs.yml` drifted from `mkdocs.yml`'s
  `plugins:` block. Audit-then-fix discipline (same shape as rc3's
  wheel-completeness audit): enumerated every plugin and every
  markdown extension in `mkdocs.yml`, cross-checked against the
  workflow install. Single gap: `mkdocstrings` (+ its `[python]`
  handler). The `pymdownx.*` extensions are covered transitively by
  `mkdocs-material` (verified via fresh-venv install).

### Changed

- **`pyproject.toml` gains a `docs` optional-dependency extra.**
  Lists `mkdocs`, `mkdocs-material`, `mkdocstrings[python]`. The
  docs workflow now installs from this extra (`pip install -e
  ".[docs]"`) instead of hardcoding the dep list. Adding a new
  mkdocs plugin in the future updates one place — the same pattern
  every other workflow already follows (`types.yml`,
  `docstrings.yml`, `wheel-completeness.yml`, and
  `deploy-verification.yml` all install from pyproject).

### Audit-decision: no CONTRACT v1.1 #10

Considered: a generalized "declared-but-not-installed" audit gate
in the same shape as v1.1 #8 and v1.1 #9. Cross-checked all six
workflows' install steps. `docs.yml` was the **only** workflow
whose install step hardcoded deps that could drift from a separate
config file (`mkdocs.yml`). Every other workflow installs from
pyproject extras (auto-synced) or installs only its own tool, so
the failure mode "config declares X, install doesn't include X" is
structurally impossible for them. Joining `docs.yml` to that
pattern institutes the prevention; an audit gate would be
mechanism without a target. Filed as a one-off, not a class.

### Externally owned (unchanged from rc3)

The two operational steps named in the rc3 entry above still
gate the v1.1 #9 deploy-verification check from passing: enable
GitHub Pages on the repo, configure DNS for `docs.activegraph.ai`.
With this build-fix landed, the docs workflow can now produce a
deployable artifact for the first time since the doc-site phase
shipped — the artifact is the precondition that the externally-
owned Pages-enable step then publishes.

## [v1.0-rc3] — 2026-05-18

The lighter-user-test-findings milestone. Two findings from the
[CONTRACT v1.0 #C4](https://github.com/yoheinakajima/activegraph/blob/main/CONTRACT.md)
lighter pass against rc2 addressed; both surfaced
discipline gaps that ship with new v1.1 CI gates institutionalizing
the verification layer that caught them. No runtime capability
changes; no public-API renames.

### Added

- **Wheel-completeness CI gate**
  (`.github/workflows/wheel-completeness.yml` +
  `tests/test_wheel_completeness.py`). CONTRACT v1.1 #8. Builds the
  wheel via `python -m build`, installs it into a fresh venv (NOT
  editable), runs `activegraph quickstart` against the installed
  wheel, fails if any runtime data file is missing. Catches the
  class of bug that's structurally invisible to source-tree tests
  and editable installs. Marked `slow`; CI invokes via
  `pytest -m slow tests/test_wheel_completeness.py`. To be
  configured as a required status check on main per CONTRACT
  v1.1 #8 implementation scope.
- **Deploy-verification CI gate**
  (`.github/workflows/deploy-verification.yml` +
  `tests/test_doc_site_reachable.py`). CONTRACT v1.1 #9. Fetches
  `DOCS_BASE_URL` + 4 known-good page paths, asserts HTTP 200
  and that the response body contains `Active Graph` (the mkdocs
  `site_name`). Failure-mode design distinguishes DNS failure,
  HTTP 404, and content mismatch — each fails with a message that
  names the operational step to fix it. The HTTP-reachability
  complement to `tests/test_doc_links.py`, which is source-tree-
  scoped only. Runs on push to main + daily cron (catches drift
  if the site goes down without a code change). Required for
  merge once GitHub Pages is enabled.

### Changed

- **B3 fix: `prompts/*.md` ship in the wheel.** The v1.0-rc2 user-
  test gate surfaced that `pip install activegraph==1.0.0rc2`
  followed by `activegraph quickstart` crashed with
  `PackPromptLoadError: prompts directory does not exist`. Root
  cause: `pyproject.toml` declared no
  `[tool.setuptools.package-data]` block, so setuptools' default
  behavior (ship only `.py` files) omitted the 4 `.md` prompt
  files. Audit confirmed exactly 4 non-`.py` files in
  `activegraph/`, all in `packs/diligence/prompts/`. Fix is a
  single key/glob; rc3 #1 commit. The wheel-completeness gate
  above is the enforcement layer that prevents this class from
  recurring.
- **Domain cutover: `docs.activegraph.dev` → `docs.activegraph.ai`.**
  CONTRACT v1.0 #C6 amended (rc3 amendment block in CONTRACT.md).
  Primary domain switched to the already-owned
  `docs.activegraph.ai`. `.dev` becomes a redirect-source the
  maintainer registers and configures separately (externally
  owned). The codebase holds exactly one primary; the constant
  `DOCS_BASE_URL` is the single swap point. Affected files:
  `activegraph/errors.py` (constant), `docs/CNAME`, `mkdocs.yml`
  (`site_url`), `README.md` (5 link refs), `CHANGELOG.md` (11 link
  refs), `HANDOFF.md`, `docs/about/publishing.md`,
  `examples/quickstart_session.txt`,
  `.github/workflows/docs.yml` (comments). Error snapshots
  rebaselined via `UPDATE_SNAPSHOTS=1` — 58 files. The cutover
  pattern worked as designed: one constant change propagates to
  every URL through `f"{DOCS_BASE_URL}/..."` interpolation.
  `tests/test_doc_links.py` continues to recognize the .dev URL
  form so historical CHANGELOG entries linking to .dev still pass
  the source-presence check.

### Externally owned (B4 findings; the gate is shipped, the
operational steps are yours):

- **GitHub Pages must be enabled on the repo.** Per rc3 step-4
  investigation: `has_pages: false` is the smoking-gun finding;
  the doc site at any URL 404s because there's nothing to serve.
  Fix: Settings → Pages → Source: GitHub Actions. Precondition:
  the repo must be public (free plan) or on GitHub Pro/Team
  /Enterprise (paid). Until this lands, the v1.1 #9 deploy-
  verification gate is red on every CI run; that's the correct
  signal.
- **DNS for `docs.activegraph.ai` must be configured.** CNAME
  record pointing at `yoheinakajima.github.io` (the GitHub Pages
  default subdomain). The v1.1 #9 gate's failure message names
  this when DNS is the missing piece.
- **`docs.activegraph.dev` redirect.** Register the .dev domain
  and configure it to 301/302-redirect to
  `docs.activegraph.ai`. Not strictly required for the v1.1 #9
  gate to pass (gate only checks .ai), but needed for historical
  CHANGELOG entries' .dev links to keep resolving for users.

### Boundary shift (CONTRACT v1.1 framing):

The two new gates each move the user-test boundary outward by one
layer.

- **v1.1 #8 (wheel-completeness):** after this gate lands, the
  lighter user-test verifies the PyPI artifact (CDN, upload,
  distribution) — not the wheel itself.
- **v1.1 #9 (deploy-verification):** after this gate lands, the
  lighter user-test verifies the published-domain experience
  (does the README link land on a page that reads well? does
  navigation feel right?) — not the basic reachability question
  of "does this URL return 200."

Same rc1-vs-rc2 discipline pattern noted in the CONTRACT entries:
each rc surfaces a finding that's structurally invisible to the
prior layer's CI; each rc institutionalizes the verification
layer that caught it.

## [v1.0-rc2] — 2026-05-18

The user-test-findings milestone. Five findings from the
[CONTRACT v1.0 #C4](https://github.com/yoheinakajima/activegraph/blob/main/CONTRACT.md)
gate addressed; one was a latent runtime state-machine bug since
v0.5. No new runtime capability; no public-API renames.

### Added

- **PyPI publish workflow** (`.github/workflows/publish.yml`).
  Tag-push trigger matching `v*` triggers `python -m build` then
  upload via PyPI trusted publishing (OIDC-based). Documented in
  [Publishing a release](https://docs.activegraph.ai/about/publishing/).
  Externally owned per
  [CONTRACT v1.0 #C8](https://github.com/yoheinakajima/activegraph/blob/main/CONTRACT.md)
  — the agent ships the workflow, the maintainer runs the publish.
- **Tutorial-snippet CI test** (`tests/test_tutorial_snippets.py`).
  Subprocess-runs the tutorial's step 7 fork snippet end-to-end
  against the bundled fixtures; asserts exit 0 and idempotency on
  re-run. Tactical down-payment on
  [CONTRACT v1.1 #2 expansion](https://github.com/yoheinakajima/activegraph/blob/main/CONTRACT.md)
  (spec-vs-impl drift gate for Python doc snippets).
- **`_requeue_unfired` regression test** (`tests/test_requeue_unfired.py`).
  Locks the C3 regression vector: `Runtime.load` on a cleanly-drained
  saved run produces `queue_depth == 0`.

### Changed

- **`_requeue_unfired` uses `runtime.idle` as the high-water mark.**
  Latent bug since CONTRACT v0.5 #8: the function relied on the false
  reverse-implication "no `behavior.started` references this event id
  ⟹ event was still in the queue." Events with **zero** subscribed
  behaviors are popped-and-discarded with no `behavior.started`
  emitted, so they were falsely requeued on every `Runtime.load`.
  The fix uses the last `runtime.idle` event as the high-water mark
  (the runtime emits `runtime.idle` only after the queue empties);
  only events after the last idle are candidates for requeue.
  `runtime.budget_exhausted` is explicitly NOT a drain marker —
  using it would break budget-bounded pause-and-resume.
- **Tutorial step 3 and quickstart prose** distinguish the provider
  layer (where the fixture provider produces responses) from the
  runtime's replay cache layer (where `cache_hit=true` legitimately
  appears under strict-replay loads or `Runtime.fork()` in-process).
  Pre-rc2 prose conflated the two. The conflation was originally in
  the v1.0 spec at `examples/quickstart_session.txt`; the spec is
  updated with a header drift-note documenting the two-layer reality.
- **Tutorial step 7 fork snippet** uses
  `RecordedDiligenceProvider(companies=THREE_COMPANIES)` as the fork's
  `llm_provider=`. Matches the parent run's provider; preserves the
  "no API key required" tutorial pitch. The snippet also includes a
  tutorial-only cleanup-on-collision branch so it's re-runnable
  without manual DB surgery.
- **`_prepare_interactive_subdir` collision prompt** re-prompts on
  unrecognized input. Pre-rc2 behavior fell through to the suffix
  branch on any input that wasn't `o` or `q`, which swallowed
  typeahead from the next prompt. Mirrors the existing iteration-loop
  pattern at `run_interactive_mode`.

### Deprecated

Nothing. Backward compatibility holds — all v0–v1.0-rc1 tests pass.

### Removed

Nothing user-facing.

### Fixed

- **`Runtime.load(...).status().queue_depth` reads 0 on a freshly
  loaded cleanly-drained run.** Was a non-zero false count of events
  that had been popped-and-discarded during the original run. See
  the Changed entry for `_requeue_unfired` above.
- **`activegraph --version` reports the correct version.** Was stuck
  at `0.9.1` through v1.0-rc1's release (the version-sync gate
  validated internal consistency but not correspondence with the git
  tag). The v1.1 #6 version-tag-correspondence gate closes this gap
  in v1.1.
- **Tutorial step 7 fork snippet runs end-to-end against bundled
  fixtures** with no API key, matching the rest of the quickstart's
  "no API key required" pitch.
- **`cache_hit=true` claim** in the tutorial and quickstart "what
  just happened" prose: the claim was wrong for initial fixture-mode
  runs (the runtime's replay cache only fires on strict-replay or
  in-process fork). Prose corrected; two-layer vocabulary lands
  cleanly for first-time readers.

### Migration from v1.0-rc1

Additive. No code changes required. Existing v1.0-rc1 installs
should:

```bash
pip install --upgrade activegraph==1.0.0rc2
```

Existing saved runs (`*.db` files) load with `queue_depth == 0`
correctly post-upgrade — the C3 fix is in the load path, not the
storage format.

### Known follow-ons (v1.1 scope)

In addition to the v1.0-rc1 v1.1 backlog (carried forward):

- **CONTRACT v1.1 #2 expansion** — spec-vs-impl drift gate covers
  executable Python snippets in docs, not just CLI flags. v1.0-rc2
  ships the tactical step 7 test; v1.1 generalizes.
- **CONTRACT v1.1 #5** — `Runtime.load` auto-provider ergonomics.
  The rc2 fix for B2 passes `RecordedDiligenceProvider` explicitly;
  the v1.1 design question is whether `Runtime.load` should infer a
  provider from the run's recorded events or from a pack-manifest
  declaration. New runtime capability, banned in v1.0.
- **CONTRACT v1.1 #6** — version-tag-correspondence CI gate.
  Existing version-sync gate validates `__version__` matches
  `pyproject.toml`; the v1.1 gate adds correspondence with the
  current annotated tag in tagged-release CI runs.
- **CONTRACT v1.1 #7 backlog item: fork cache pre-population
  symmetry.** `Runtime.fork(at_event=...)` in-process pre-populates
  the LLM cache from the parent's events; the persistent shape
  (`SQLiteEventStore.fork_run` then `Runtime.load`) does not. The
  two paths should be symmetric. New runtime behavior, banned in v1.0.

## [v1.0-rc1] — 2026-05-18

The adoption-surface milestone. No new runtime capability; the
contract is "a new user can install, run, understand, debug, and
extend the framework without reading source code."

### Added

- **Error hierarchy rewrite.** Every exception now inherits from
  `ActiveGraphError` and carries structured `what_failed`,
  `how_to_fix`, and `context` fields. Seven category bases
  (`ConfigurationError`, `RegistrationError`, `ExecutionError`,
  `ReplayError`, `StorageError`, `PatternError`, `PackError`) with
  33 leaves. Built-in lineage preserved via multi-inheritance —
  `except ValueError`/`except KeyError` clauses still work.
- **Per-error reference catalog.** Every error message ends with a
  `More:` link to a dedicated page documenting when it fires, why,
  how to diagnose, and how to fix. Catalog at
  [docs.activegraph.ai/reference/errors](https://docs.activegraph.ai/reference/errors/replay-divergence-error/).
- **Documentation site at [docs.activegraph.ai](https://docs.activegraph.ai/)**:
  concepts pages for every primitive (graph, events, behaviors,
  relations, patches, views, frames, policies, patterns, replay,
  forking, failure model); guides; cookbook (common patterns,
  debugging, migration); CLI reference; API reference via
  mkdocstrings.
- **`activegraph quickstart` CLI command.** Bundled Diligence demo
  in fixture mode (byte-deterministic, no API key, ~20 seconds);
  `--interactive` mode walks the user through writing their first
  behavior.
- **10-minute tutorial at
  [docs.activegraph.ai/quickstart](https://docs.activegraph.ai/quickstart/).**
  Install → run → write a behavior → save and inspect → fork and
  diff. Seven steps; every example runs.
- **CI gates on the public surface.** Version-sync gate
  (`pyproject.toml` ↔ `activegraph.__version__`), broken-link gate
  for the doc site, mypy `--strict` gate on the
  [`__all__` allowlist](https://github.com/yoheinakajima/activegraph/blob/main/docs/reference/api/TYPE_REPORT.md)
  (22/38 modules clean at baseline), docstring coverage gate
  ([Ring 0 92/100 not-missing, Ring 1 at 84.7%](https://github.com/yoheinakajima/activegraph/blob/main/docs/reference/api/COVERAGE_REPORT.md);
  exemption list in
  [`docstring_gaps.toml`](https://github.com/yoheinakajima/activegraph/blob/main/docstring_gaps.toml)).
- **CLI follow-on flags** (referenced from error messages' recovery
  prose): `inspect --event <id>`, `inspect --behaviors`,
  `inspect --pack-version`, `migrate --skip-corrupted`,
  `fork --record`.

### Changed

- **README trimmed** from 1275 lines to ~190. The doc site is now
  the canonical reference; the README is the conversion funnel
  (30-second pitch → install → `activegraph quickstart` →
  tutorial).
- **Error messages structured.** Every framework-raised exception
  exposes `what_failed` (one line), `how_to_fix` (actionable
  prose), and `context` (structured detail) on the exception
  instance. Plain `str(exc)` renders all three.
- **Trace printer formats `pack.loaded`** (was previously falling
  through to the generic event renderer).

### Deprecated

Nothing. Backward compatibility holds — all v0–v0.9 tests pass.

### Removed

Nothing user-facing. Internal: a handful of dead code paths
surfaced during the error-rewrite audits were removed.

### Fixed

- `pack.loaded` trace formatting (was missing despite being spec'd
  in CONTRACT v0.9 #25).
- Several inconsistent error categories — see CONTRACT v1.0 PR-F
  audit findings for the cross-category reclassifications.

### Migration from v0.9.1

Additive. See
[Migration from v0.7 § 5–6](https://docs.activegraph.ai/cookbook/migration-from-v0-7/#5-adopt-the-v10-error-hierarchy-v09--v10):

```python
# v1.0 — broader catches with structured context:
try:
    rt = Runtime.load(url, run_id=rid)
except activegraph.StorageError as e:
    log(e.what_failed, e.how_to_fix, e.context)
except activegraph.ActiveGraphError as e:
    log(e.what_failed, e.how_to_fix)
```

Existing `except ValueError`/`except KeyError`/`except TypeError`
clauses keep working — multi-inheritance preserves builtin
lineage.

### Known follow-ons (v1.1 scope)

- `fork --set <pack>.<key>=<value>` for cheap fork-with-override
  experiments (CONTRACT v1.1 #1; canonical Python-API recipe at
  [Cookbook § Fork with a pack-setting override](https://docs.activegraph.ai/cookbook/common-patterns/#fork-with-a-pack-setting-override-v10-python-api)).
- `inspect --memo` and `inspect --search` (CONTRACT v1.1 #1).
- Type-completeness burndown — close the 16 dirty allowlist
  modules (CONTRACT v1.1 #3,
  [`TYPE_REPORT.md`](https://github.com/yoheinakajima/activegraph/blob/main/docs/reference/api/TYPE_REPORT.md)).
- Docstring-completeness burndown — close the 8 missing Ring 0
  exemptions and upgrade one-liners to full
  (CONTRACT v1.1 #4,
  [`COVERAGE_REPORT.md`](https://github.com/yoheinakajima/activegraph/blob/main/docs/reference/api/COVERAGE_REPORT.md)).
- Spec-vs-impl drift gate for CLI flags (CONTRACT v1.1 #2).

## [v0.9.1] — 2026-05-17

Operator-visible quality-of-life fixes between v0.9 and v1.0.

### Added

- `[trace.flags]` rollup header at the top of every trace block
  with `prompt_normalized=true|false` so operators can see at a
  glance whether a run used the v0.7+ normalized-prompt format.

### Changed

- Approval-demo output is now granular (per-object) rather than
  batched, so operators can see which approval the runtime is
  waiting on.

### Migration from v0.9

None — additive trace and demo improvements; no API changes.

## [v0.9] — 2026-05-16

The **pack format** milestone. A pack bundles object types,
behaviors, tools, prompts, and policies for a specific domain.

### Added

- `Pack` dataclass: frozen, equality by `(name, version)`.
- Pack-aware decorators (`activegraph.packs.behavior`,
  `llm_behavior`, `relation_behavior`, `tool`) with no global
  registry side effects.
- `runtime.load_pack(pack, settings=...)` — idempotent;
  conflicts (object type, relation type, behavior name, tool
  name, policy name) raise `PackConflictError` before any state
  mutation; version mismatch raises `PackVersionConflictError`.
- Object type schemas enforced via Pydantic at
  `graph.add_object`; relation type validation at
  `graph.add_relation`.
- Namespace prefixing: canonical strict
  (`diligence.claim_extractor`); short-name lookups lenient.
- Three settings access forms: typed parameter injection
  (primary), `ctx.settings`, `ctx.pack_settings(name)`.
- Prompt loader: TOML frontmatter; content-hashed via SHA-256
  truncated to 16 hex chars; hash (not version) is the replay
  contract.
- Discovery via Python entry points
  (`activegraph.packs`); `discover()`, `load_by_name()`,
  `clear_discovery_cache()`.
- `activegraph pack new <name>` scaffolding command.
- `activegraph pack list` to enumerate installed packs.
- `activegraph.packs.diligence` — production-quality reference
  pack: 8 object types, 6 relation types, 7 behaviors, 3 tools,
  2 policies, 4 prompts, recorded fixtures for 3 companies,
  end-to-end demo at
  [`examples/diligence_real_run.py`](https://github.com/yoheinakajima/activegraph/blob/main/examples/diligence_real_run.py).
- Pack authoring guide at
  [Authoring packs](https://docs.activegraph.ai/guides/authoring-packs/).

### Changed

- **Python floor raised to 3.11** (uses stdlib `tomllib`).
- **`pydantic>=2` is now a hard dependency** (was opt-in via
  `[llm]`). The pack format's object-type schemas and settings
  models require it.
- `click>=8,<9` becomes a hard dependency (CLI is always
  available).

### Migration from v0.8

Additive. See
[Migration from v0.7 § 4](https://docs.activegraph.ai/cookbook/migration-from-v0-7/#4-adopt-the-pack-format-v08--v09).
Global decorators (`@behavior`, `@tool`) keep working alongside
loaded packs; the pack format is opt-in for new code.

Python 3.10 users must upgrade to 3.11+ before installing v0.9.

## [v0.8] — 2026-05-16

The **operator surface** milestone. Hardens the boundary between
the framework and the world it runs in.

### Added

- `PostgresEventStore` behind the same `EventStore` protocol as
  SQLite (Postgres 16+; `pip install activegraph[postgres]`).
- Connection-URL addressing everywhere (`sqlite:///relative`,
  `sqlite:////absolute`, `postgres://...`).
- `activegraph migrate --from <url> --to <url>` —
  transaction-per-run, idempotent, one-directional.
- Structured JSON logging via `configure_logging(json_output=True)`
  with a documented schema (the operator contract).
- `Metrics` protocol (three methods: counter, histogram, gauge)
  with `NoOpMetrics` default and reference `PrometheusMetrics`
  backend.
- `runtime.status(recent=N)` — frozen `RuntimeStatus` dataclass
  for introspection.
- `activegraph` CLI: `inspect`, `replay`, `fork`, `diff`,
  `export-trace`, `migrate`. CLI exit codes documented as
  contract.
- Operator guide at
  [Operating in production](https://docs.activegraph.ai/guides/operating-in-production/).

### Migration from v0.7

Additive. See
[Migration from v0.7 § 3, 7, 8](https://docs.activegraph.ai/cookbook/migration-from-v0-7/#3-adopt-connection-urls-v07--v08).
Old SQLite path arguments (`persist_to="/path/to.db"`) keep
working; URLs are required for CLI and cross-store operations.

## [v0.7] — 2026-05-16

The **tools and advanced matching** milestone.

### Added

- `@tool` decorator: tools as first-class primitives with input
  schema, output schema, determinism flag, cost, timeout.
- LLM ↔ tool turn loop owned by the runtime; multi-turn until
  the model returns a non-tool response or `max_tool_turns` hits.
- `tool.requested` / `tool.responded` event pair; replay cache
  separate from the LLM cache.
- `RecordedToolProvider` + `RecordingToolProvider` for tests.
- Two reference tools: `web_fetch`, `graph_query` (factory-based
  for graph read access).
- Cypher-subset pattern subscriptions via `pattern=` on
  `@behavior` / `@llm_behavior`. Compile-time strict; the
  unsupported tokens raise `UnsupportedPatternError` naming the
  offending token.
- Negation via `NOT EXISTS { ... }`.
- Temporal predicates: `activate_after=N` events (event-count,
  not wall-clock — keeps replay deterministic).
- Tool budgets (`max_tool_calls`) + cost-sharing with LLM
  (`max_cost_usd` covers both).
- Causal-chain walk crosses tool boundaries via
  `tool_request_event_id` provenance.

### Changed

- Prompt assembly normalized — every prompt is content-hashed
  via the canonical form; the `prompt_normalized=true` flag
  appears in the v0.9.1 trace rollup for runs using this format.

### Migration from v0.6

Additive. v0.6 LLM behaviors continue to work without `tools=`
declarations.

## [v0.6] — 2026-05-16

The **LLM integration** milestone.

### Added

- `@llm_behavior` decorator with structured output parsing
  (Pydantic schema).
- Frame-aware prompt construction: system prompt assembled from
  frame goal + constraints + behavior description + output-schema
  reminder, in a fixed order.
- `llm.requested` / `llm.responded` event pair with model, full
  prompt+params, prompt hash, estimated cost, deterministic flag,
  cache-hit flag.
- `AnthropicProvider` reference implementation (reads
  `ANTHROPIC_API_KEY`; never from code).
- `RecordedLLMProvider` + `RecordingLLMProvider` for tests
  (fixtures keyed by SHA-256 of prompt+params canonical form).
- Cost accounting: Decimal-precise `max_cost_usd` budget; pre-call
  estimate via `count_tokens`; post-call actual cost from
  provider's `usage`.
- Structured failure reasons (`llm.network_error`,
  `llm.rate_limited`, `llm.parse_error`, `llm.schema_violation`,
  `llm.fixture_missing`, `budget.cost_exhausted`).

### Migration from v0.5

Additive. LLM behaviors are opt-in; non-LLM runs unaffected. New
optional dependency `activegraph[llm]` (anthropic SDK).

## [v0.5] — 2026-05-16

The **resumability** milestone. The event log becomes the source
of truth.

### Added

- Full event log persistence via the `EventStore` protocol;
  SQLite reference backend with schema version pinned from day
  one in a `meta` table.
- `Runtime.load(url, run_id=...)` — open, pick a run, replay,
  return runtime ready to continue.
- Strict-replay mode (`replay_strict=True`) — re-executes
  behaviors and fires `ReplayDivergenceError` on mismatch.
- Fork (`runtime.fork(at_event=...)`) — new run, copies parent's
  event log up to the cutoff (inclusive), independent log
  thereafter.
- Structural diff (`parent.diff(other)`) — shared / parent-only
  / fork-only event partitions; divergent objects and relations.
- Multiple runs per file; ULID `run_id`s; provenance carries
  `run_id`.
- Unfired-event re-queue on load (events emitted but never popped
  return to the queue on resume).

### Migration from v0

Additive. v0 in-memory runs continue to work without
`persist_to=`. New optional dependency
`activegraph[sqlite]` (stdlib — no extra packages needed).

## [v0] — 2026-05-16

The **core runtime**.

### Added

- In-memory `Graph` with typed objects, typed relations, and an
  append-only event log.
- Function-based (`@behavior`) and class-based
  (subclass `Behavior`) behaviors.
- Relation behaviors (`@relation_behavior`) — coordination logic
  on edges.
- Event-type subscriptions with predicate filters (`where=`).
- Patch system with optimistic concurrency (version-keyed apply;
  rejected patches surface as `patch.rejected` events).
- Views with type/depth/recent-events scoping.
- Frames (mission context per run) and policies (per-behavior
  capability declarations).
- Trace printer (`runtime.print_trace()`); causal-chain query
  (`runtime.trace.causal_chain(object_id=...)`).
- Budgets (`max_events`, `max_behavior_calls`, `max_seconds`,
  `max_depth`, etc.) — runtime stops cleanly when hit; resumable.

### Migration from before v0

There is no before-v0.

---

The graph is the world. Behaviors are physics. The trace is the proof.
